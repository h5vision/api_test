from __future__ import annotations


import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError


from backend.snapshot_control_plane import mount_snapshot_control_plane
from backend.snapshots.adapters.github import PublicGithubAdapter
from backend.snapshots.contracts import (
    LocatorRecord,
    SnapshotRecord,
    normalize_git_sha,
    normalize_repository_path,
    snapshot_fingerprint,
    snapshot_id_from_fingerprint,
)
from backend.snapshots.resolver import GithubSnapshotResolver




SHA_A = "a" * 40
SHA_B = "b" * 40
TEST_TOKEN = "unit-test-snapshot-token-32-bytes-minimum"
OLD_COMMIT = "03728c8fc0b6a4a0bf4ad70f28f99336a9d30070"
NEW_COMMIT = "10aad66b8e7831ac8bc4e9a72b91e9950b88758a"




class GithubSnapshotContractTests(unittest.TestCase):
    def test_sha_accepts_only_exact_git_object_lengths(self) -> None:
        self.assertEqual(normalize_git_sha("A" * 40), "a" * 40)
        self.assertEqual(normalize_git_sha("b" * 64), "b" * 64)
        for invalid in ("a" * 39, "a" * 41, "a" * 63, "a" * 65, "g" * 40):
            with self.subTest(length=len(invalid), prefix=invalid[:1]):
                with self.assertRaises(ValueError):
                    normalize_git_sha(invalid)


    def test_repository_path_rejects_traversal_and_absolute_forms(self) -> None:
        invalid_paths = (
            "../README.md",
            "../../.env",
            "/README.md",
            "\\..\\README.md",
            "%2e%2e%2fREADME.md",
            "README.md%00",
            "a//b.py",
            "./README.md",
        )
        for value in invalid_paths:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_repository_path(value)
        self.assertEqual(
            normalize_repository_path("backend/snapshots/router.py"),
            "backend/snapshots/router.py",
        )


    def test_snapshot_identity_is_deterministic_and_tenant_scoped(self) -> None:
        first = snapshot_fingerprint(
            tenant_id="vision-default",
            provider_repository_id="1306244300",
            commit_sha=NEW_COMMIT,
        )
        second = snapshot_fingerprint(
            tenant_id="vision-default",
            provider_repository_id="1306244300",
            commit_sha=NEW_COMMIT,
        )
        other_tenant = snapshot_fingerprint(
            tenant_id="another-tenant",
            provider_repository_id="1306244300",
            commit_sha=NEW_COMMIT,
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_tenant)
        self.assertEqual(snapshot_id_from_fingerprint(first), f"snap_{first[:24]}")


    def test_artifact_is_not_a_snapshot_type(self) -> None:
        with self.assertRaises(ValidationError):
            SnapshotRecord(
                snapshot_id="snap_test",
                repository_id="repo_test",
                snapshot_type="artifact",
                commit_sha=SHA_A,
                tree_sha=SHA_B,
                fingerprint="c" * 64,
                verified_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )




class GithubSnapshotResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        now = datetime.now(timezone.utc)
        self.snapshot = SnapshotRecord(
            snapshot_id="snap_test",
            repository_id="repo_test",
            commit_sha=SHA_A,
            tree_sha=SHA_B,
            fingerprint="c" * 64,
            verified_at=now,
            created_at=now,
        )
        self.locator = LocatorRecord(
            locator_id="loc_test",
            snapshot_id="snap_test",
            availability="durable",
            last_verified_at=now,
        )


    def test_only_verified_github_locator_is_resolved(self) -> None:
        plan = GithubSnapshotResolver().resolve(self.snapshot, self.locator)
        self.assertTrue(plan.available)
        self.assertEqual(plan.provider, "github")
        self.assertEqual(plan.access_mode, "backend-proxy")
        self.assertEqual(
            plan.capabilities,
            ["commit.read", "tree.read", "file.read"],
        )


    def test_missing_locator_is_unavailable(self) -> None:
        plan = GithubSnapshotResolver().resolve(self.snapshot, None)
        self.assertFalse(plan.available)
        self.assertEqual(plan.access_mode, "unavailable")
        self.assertEqual(plan.reason, "github_locator_missing")




class SnapshotMountAndAuthenticationTests(unittest.TestCase):
    def test_feature_flag_off_exposes_no_snapshot_routes(self) -> None:
        with patch.dict(
            os.environ,
            {"SNAPSHOT_CONTROL_PLANE_ENABLED": "false"},
            clear=False,
        ):
            app = FastAPI()
            self.assertFalse(mount_snapshot_control_plane(app))
            paths = {
                route.path
                for route in app.routes
                if hasattr(route, "path")
            }
            self.assertFalse(
                any(path.startswith("/v1/snapshot-control") for path in paths)
            )


    def test_feature_flag_on_mounts_once(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SNAPSHOT_CONTROL_PLANE_ENABLED": "true",
                "SNAPSHOT_MVP_TOKEN": TEST_TOKEN,
            },
            clear=False,
        ):
            app = FastAPI()
            self.assertTrue(mount_snapshot_control_plane(app))
            self.assertFalse(mount_snapshot_control_plane(app))
            paths = [
                path
                for path in app.openapi()["paths"]
                if path.startswith("/v1/snapshot-control")
            ]
            self.assertEqual(len(paths), len(set(paths)))
            self.assertIn(
                "/v1/snapshot-control/snapshots/{snapshot_id}/file",
                paths,
            )


    def test_missing_and_wrong_token_are_rejected_before_storage(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SNAPSHOT_CONTROL_PLANE_ENABLED": "true",
                "SNAPSHOT_MVP_TOKEN": TEST_TOKEN,
            },
            clear=False,
        ):
            app = FastAPI()
            mount_snapshot_control_plane(app)
            client = TestClient(app)


            missing = client.get(
                "/v1/snapshot-control/snapshots/snap_unknown"
            )
            self.assertEqual(missing.status_code, 401)


            wrong = client.get(
                "/v1/snapshot-control/snapshots/snap_unknown",
                headers={"X-Vision-Snapshot-Token": "wrong"},
            )
            self.assertEqual(wrong.status_code, 403)


    def test_short_configured_token_is_rejected_as_insecure(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SNAPSHOT_CONTROL_PLANE_ENABLED": "true",
                "SNAPSHOT_MVP_TOKEN": "too-short",
            },
            clear=False,
        ):
            app = FastAPI()
            mount_snapshot_control_plane(app)
            response = TestClient(app).get(
                "/v1/snapshot-control/snapshots/snap_unknown",
                headers={"X-Vision-Snapshot-Token": "too-short"},
            )
            self.assertEqual(response.status_code, 503)




@unittest.skipUnless(
    os.getenv("GITHUB_INTEGRATION_TESTS", "").strip() == "1",
    "Set GITHUB_INTEGRATION_TESTS=1 to call the public GitHub API",
)
class GithubSnapshotLiveIntegrationTests(unittest.TestCase):
    def test_api_test_two_commits_remain_distinct(self) -> None:
        adapter = PublicGithubAdapter(timeout_seconds=30)
        repository = adapter.get_repository("h5vision/api_test")
        self.assertEqual(repository.provider_repository_id, "1306244300")
        self.assertFalse(repository.private)


        old_commit = adapter.resolve_commit("h5vision/api_test", OLD_COMMIT)
        new_commit = adapter.resolve_commit("h5vision/api_test", NEW_COMMIT)
        self.assertEqual(old_commit.commit_sha, OLD_COMMIT)
        self.assertEqual(new_commit.commit_sha, NEW_COMMIT)
        self.assertNotEqual(old_commit.tree_sha, new_commit.tree_sha)


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
        self.assertIn(
            '"client_request_id": "vscode-1710000000000-001"',
            old_readme.content,
        )
        self.assertNotIn(
            '"client_request_id": "vscode-1710000000000-001"',
            new_readme.content,
        )
        self.assertIn('"role": "user"', new_readme.content)
        self.assertIn('"content": "결제 재시도 횟수를 알려줘"', new_readme.content)




if __name__ == "__main__":
    unittest.main()
