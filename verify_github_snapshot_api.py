from __future__ import annotations


import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any




OLD_COMMIT = "03728c8fc0b6a4a0bf4ad70f28f99336a9d30070"
NEW_COMMIT = "10aad66b8e7831ac8bc4e9a72b91e9950b88758a"




def request_json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_url = os.getenv(
        "SNAPSHOT_API_BASE_URL",
        "http://127.0.0.1:8000",
    ).strip().rstrip("/")
    token = os.getenv("SNAPSHOT_MVP_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SNAPSHOT_MVP_TOKEN is required")


    request = urllib.request.Request(
        f"{base_url}{path}",
        data=(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        ),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "X-Vision-Snapshot-Token": token,
            "User-Agent": "Vision-Snapshot-Smoke/1.0",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {path} failed with HTTP {exc.code}: {detail}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"{method} {path} failed: {exc}") from exc


    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {path} returned non-JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{method} {path} returned a non-object JSON value")
    return value




def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)




def create_snapshot(repository_id: str, commit_sha: str) -> dict[str, Any]:
    return request_json(
        "POST",
        f"/v1/snapshot-control/repositories/{repository_id}/snapshots",
        payload={"ref": commit_sha},
    )




def read_snapshot_file(snapshot_id: str, path: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"path": path})
    return request_json(
        "GET",
        f"/v1/snapshot-control/snapshots/{snapshot_id}/file?{query}",
    )




def main() -> int:
    repository = request_json(
        "POST",
        "/v1/snapshot-control/repositories",
        payload={"repository_full_name": "h5vision/api_test"},
    )
    repository_id = str(repository.get("repository_id") or "")
    require(repository_id.startswith("repo_"), "Repository ID was not generated")
    require(
        repository.get("provider_repository_id") == "1306244300",
        "Unexpected GitHub repository identity",
    )
    print(f"PASS repository {repository_id}")


    old_result = create_snapshot(repository_id, OLD_COMMIT)
    new_result = create_snapshot(repository_id, NEW_COMMIT)
    old_snapshot = old_result.get("snapshot")
    new_snapshot = new_result.get("snapshot")
    require(isinstance(old_snapshot, dict), "Old Snapshot response was invalid")
    require(isinstance(new_snapshot, dict), "New Snapshot response was invalid")


    old_snapshot_id = str(old_snapshot.get("snapshot_id") or "")
    new_snapshot_id = str(new_snapshot.get("snapshot_id") or "")
    require(old_snapshot.get("commit_sha") == OLD_COMMIT, "Old Commit mismatch")
    require(new_snapshot.get("commit_sha") == NEW_COMMIT, "New Commit mismatch")
    require(old_snapshot_id != new_snapshot_id, "Distinct commits shared one Snapshot ID")
    require(
        old_snapshot.get("tree_sha") != new_snapshot.get("tree_sha"),
        "Distinct test commits unexpectedly shared one tree",
    )
    print(f"PASS snapshots {old_snapshot_id} {new_snapshot_id}")


    old_repeat = create_snapshot(repository_id, OLD_COMMIT)
    repeated_snapshot = old_repeat.get("snapshot")
    require(isinstance(repeated_snapshot, dict), "Repeated Snapshot response was invalid")
    require(
        repeated_snapshot.get("snapshot_id") == old_snapshot_id,
        "Repeated Commit created another Snapshot ID",
    )
    require(old_repeat.get("deduplicated") is True, "Dedup flag was not returned")
    print("PASS dedup")


    old_plan = request_json(
        "GET",
        f"/v1/snapshot-control/snapshots/{old_snapshot_id}/resolve",
    )
    new_plan = request_json(
        "GET",
        f"/v1/snapshot-control/snapshots/{new_snapshot_id}/resolve",
    )
    for plan, expected_commit in (
        (old_plan, OLD_COMMIT),
        (new_plan, NEW_COMMIT),
    ):
        require(plan.get("available") is True, "Snapshot Access Plan was unavailable")
        require(plan.get("provider") == "github", "Non-GitHub provider was selected")
        require(
            plan.get("access_mode") == "backend-proxy",
            "Unexpected Access Plan mode",
        )
        require(plan.get("commit_sha") == expected_commit, "Access Plan Commit mismatch")
    print("PASS resolve")


    old_readme = read_snapshot_file(old_snapshot_id, "README.md")
    new_readme = read_snapshot_file(new_snapshot_id, "README.md")
    require(old_readme.get("commit_sha") == OLD_COMMIT, "Old file Commit mismatch")
    require(new_readme.get("commit_sha") == NEW_COMMIT, "New file Commit mismatch")


    old_content = str(old_readme.get("content") or "")
    new_content = str(new_readme.get("content") or "")
    require(
        '"client_request_id": "vscode-1710000000000-001"' in old_content,
        "Old README marker missing",
    )
    require(
        '"client_request_id": "vscode-1710000000000-001"' not in new_content,
        "New Snapshot returned old README content",
    )
    require('"role": "user"' in new_content, "New README role marker missing")
    require(
        '"content": "결제 재시도 횟수를 알려줘"' in new_content,
        "New README content marker missing",
    )
    print("PASS immutable README content")


    print("GitHub Snapshot API smoke verification completed")
    return 0




if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc