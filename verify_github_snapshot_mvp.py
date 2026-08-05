from __future__ import annotations


import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone


from fastapi import FastAPI
from pydantic import ValidationError


from backend.snapshot_control_plane import mount_snapshot_control_plane
from backend.snapshots.adapters.github import PublicGithubAdapter
from backend.snapshots.contracts import (
    LocatorRecord,
    SnapshotRecord,
    normalize_git_sha,
    normalize_repository_path,
    snapshot_fingerprint,
)
from backend.snapshots.resolver import GithubSnapshotResolver




OLD_COMMIT = "03728c8fc0b6a4a0bf4ad70f28f99336a9d30070"
NEW_COMMIT = "10aad66b8e7831ac8bc4e9a72b91e9950b88758a"




@contextmanager
def temporary_environment(**values: str):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value




def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)




def verify_contracts() -> None:
    require(normalize_git_sha("A" * 40) == "a" * 40, "40-character SHA failed")
    require(normalize_git_sha("b" * 64) == "b" * 64, "64-character SHA failed")
    for invalid in ("a" * 39, "a" * 41, "a" * 63, "a" * 65):
        try:
            normalize_git_sha(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid SHA length was accepted: {len(invalid)}")


    for unsafe in (
        "../README.md",
        "/README.md",
        "\\..\\README.md",
        "%2e%2e%2fREADME.md",
        "README.md%00",
    ):
        try:
            normalize_repository_path(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Unsafe repository path was accepted: {unsafe!r}")


    fingerprint = snapshot_fingerprint(
        tenant_id="vision-default",
        provider_repository_id="1306244300",
        commit_sha=NEW_COMMIT,
    )
    require(len(fingerprint) == 64, "Snapshot fingerprint was not SHA-256")


    now = datetime.now(timezone.utc)
    try:
        SnapshotRecord(
            snapshot_id="snap_invalid",
            repository_id="repo_invalid",
            snapshot_type="artifact",
            commit_sha="a" * 40,
            tree_sha="b" * 40,
            fingerprint="c" * 64,
            verified_at=now,
            created_at=now,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("artifact was accepted as a Snapshot type")




def verify_resolver() -> None:
    now = datetime.now(timezone.utc)
    snapshot = SnapshotRecord(
        snapshot_id="snap_verify",
        repository_id="repo_verify",
        commit_sha="a" * 40,
        tree_sha="b" * 40,
        fingerprint="c" * 64,
        verified_at=now,
        created_at=now,
    )
    locator = LocatorRecord(
        locator_id="loc_verify",
        snapshot_id=snapshot.snapshot_id,
        availability="durable",
        last_verified_at=now,
    )
    plan = GithubSnapshotResolver().resolve(snapshot, locator)
    require(plan.available, "Verified GitHub locator was not available")
    require(plan.provider == "github", "Resolver selected a non-GitHub provider")
    require(
        plan.capabilities == ["commit.read", "tree.read", "file.read"],
        "Resolver returned unsupported capabilities",
    )




def verify_feature_flag() -> None:
    with temporary_environment(SNAPSHOT_CONTROL_PLANE_ENABLED="false"):
        disabled_app = FastAPI()
        require(
            mount_snapshot_control_plane(disabled_app) is False,
            "Feature flag OFF unexpectedly mounted the router",
        )
        disabled_paths = {
            route.path
            for route in disabled_app.routes
            if hasattr(route, "path")
        }
        require(
            not any(
                path.startswith("/v1/snapshot-control")
                for path in disabled_paths
            ),
            "Feature flag OFF exposed Snapshot routes",
        )


    with temporary_environment(
        SNAPSHOT_CONTROL_PLANE_ENABLED="true",
        SNAPSHOT_MVP_TOKEN="verification-snapshot-token-32-bytes-minimum",
    ):
        enabled_app = FastAPI()
        require(
            mount_snapshot_control_plane(enabled_app) is True,
            "Feature flag ON did not mount the router",
        )
        require(
            mount_snapshot_control_plane(enabled_app) is False,
            "Router mount was not idempotent",
        )
        enabled_paths = [
            path
            for path in enabled_app.openapi()["paths"]
            if path.startswith("/v1/snapshot-control")
        ]
        require(bool(enabled_paths), "Feature flag ON exposed no Snapshot routes")
        require(
            len(enabled_paths) == len(set(enabled_paths)),
            "Snapshot routes were mounted more than once",
        )




def verify_live_github() -> None:
    adapter = PublicGithubAdapter(timeout_seconds=30)
    repository = adapter.get_repository("h5vision/api_test")
    require(
        repository.provider_repository_id == "1306244300",
        "Unexpected GitHub repository identity",
    )
    require(repository.private is False, "Test repository must remain public")


    old_commit = adapter.resolve_commit("h5vision/api_test", OLD_COMMIT)
    new_commit = adapter.resolve_commit("h5vision/api_test", NEW_COMMIT)
    require(old_commit.commit_sha == OLD_COMMIT, "Old commit did not resolve exactly")
    require(new_commit.commit_sha == NEW_COMMIT, "New commit did not resolve exactly")
    require(old_commit.tree_sha != new_commit.tree_sha, "Test commits share one tree")


    adapter.verify_commit_tree(
        "h5vision/api_test",
        old_commit.commit_sha,
        old_commit.tree_sha,
    )
    adapter.verify_commit_tree(
        "h5vision/api_test",
        new_commit.commit_sha,
        new_commit.tree_sha,
    )


    old_readme = adapter.get_file(
        "h5vision/api_test",
        old_commit.commit_sha,
        "README.md",
    )
    new_readme = adapter.get_file(
        "h5vision/api_test",
        new_commit.commit_sha,
        "README.md",
    )
    require(
        '"client_request_id": "vscode-1710000000000-001"'
        in old_readme.content,
        "Old Snapshot README marker was not found",
    )
    require(
        '"client_request_id": "vscode-1710000000000-001"'
        not in new_readme.content,
        "New Snapshot unexpectedly returned the old README",
    )
    require('"role": "user"' in new_readme.content, "New README role marker missing")
    require(
        '"content": "결제 재시도 횟수를 알려줘"' in new_readme.content,
        "New README content marker missing",
    )




def main() -> int:
    checks = (
        ("contracts", verify_contracts),
        ("resolver", verify_resolver),
        ("feature_flag", verify_feature_flag),
    )
    for name, check in checks:
        check()
        print(f"PASS {name}")


    if os.getenv("GITHUB_INTEGRATION_TESTS", "").strip() == "1":
        verify_live_github()
        print("PASS live_github")
    else:
        print("SKIP live_github (set GITHUB_INTEGRATION_TESTS=1)")


    print("GitHub Snapshot MVP verification completed")
    return 0




if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
