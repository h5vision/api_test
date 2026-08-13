from __future__ import annotations

from backend.snapshot_compare import SnapshotCompareRequest, SnapshotComparisonService


COMMIT = "a" * 40
REMOTE_COMMIT = "b" * 40


class EmptyGithub:
    def list_repositories(self, *, limit=100, offset=0):
        return []

    def list_snapshots(self, repository_id, *, limit=100):
        return {"snapshots": []}

    def get_snapshot(self, snapshot_id):
        return None


class RemoteGithub(EmptyGithub):
    def list_repositories(self, *, limit=100, offset=0):
        return [
            {
                "repository_id": "repo_1",
                "repository_full_name": "h5vision/api_test",
                "default_branch": "main",
            }
        ]

    def list_snapshots(self, repository_id, *, limit=100):
        return {
            "snapshots": [
                {
                    "snapshot_id": "snap_a",
                    "repository_id": repository_id,
                    "commit_sha": COMMIT,
                    "tree_sha": "f" * 40,
                    "fingerprint": "e" * 64,
                }
            ]
        }

    def resolve_revision(self, repository_id, ref=None):
        return {
            "repository_id": repository_id,
            "repository_full_name": "h5vision/api_test",
            "ref": ref,
            "commit_sha": REMOTE_COMMIT,
            "tree_sha": "d" * 40,
        }

    def compare_revisions(self, repository_id, base_sha, target_sha):
        return {
            "status": "not_needed",
            "base_sha": base_sha,
            "target_sha": target_sha,
            "merge_base_sha": base_sha,
            "files": [],
            "file_count": 0,
            "truncated": False,
        }


class ProjectStore:
    def get_current_snapshot_context(self, project_id):
        return {
            "snapshot_id": "snap_a",
            "project_id": project_id,
            "revision": COMMIT,
            "status": "completed",
            "manifest_sha256": "f" * 64,
        }

    def get_snapshot(self, snapshot_id):
        return self.get_current_snapshot_context("h5vision/api_test")

    def find_snapshots(self, *, project_id=None, revision=None, limit=20):
        return [self.get_current_snapshot_context(project_id or "h5vision/api_test")]


def _compare(git_state):
    service = SnapshotComparisonService(EmptyGithub(), ProjectStore())
    return service.compare(
        SnapshotCompareRequest(
            project_id="h5vision/api_test",
            commit_id=COMMIT,
            git_state=git_state,
        ),
        request_id="req_test",
    )


def test_same_commit_clean_workspace_matches_snapshot() -> None:
    result = _compare({"dirty": False, "branch": "main"})
    assert result.comparison == "same"
    assert result.workspace_state == "clean"
    assert result.workspace_matches_snapshot is True
    assert result.update_warning is False
    assert result.reason_code == "same_commit"


def test_same_commit_dirty_workspace_preserves_revision_equality() -> None:
    result = _compare(
        {"dirty": True, "working_tree_count": 2, "staged_count": 1}
    )
    assert result.comparison == "same"
    assert result.same_version is True
    assert result.workspace_state == "modified"
    assert result.workspace_matches_snapshot is False
    assert result.update_warning is True
    assert result.reason_code == "working_tree_modified"


def test_same_commit_merge_conflict_is_separate_workspace_axis() -> None:
    result = _compare({"dirty": True, "merge_count": 1})
    assert result.comparison == "same"
    assert result.workspace_state == "conflicted"
    assert result.workspace_matches_snapshot is False
    assert result.reason_code == "working_tree_conflicted"


def test_same_snapshot_with_remote_branch_update_sets_legacy_warning() -> None:
    service = SnapshotComparisonService(RemoteGithub(), ProjectStore())
    result = service.compare(
        SnapshotCompareRequest(
            project_id="h5vision/api_test",
            commit_id=COMMIT,
            git_state={"dirty": False, "branch": "main"},
        ),
        request_id="req_remote_update",
    )

    assert result.comparison == "same"
    assert result.same_version is True
    assert result.revision_context is not None
    assert result.revision_context.local_vs_remote == "different"
    assert result.update_warning is True
    assert result.reason_code == "remote_revision_different"
