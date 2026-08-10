from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from backend.snapshot_compare import (
    SnapshotCompareRequest,
    SnapshotComparisonService,
)


COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40


class FakeGithubSnapshots:
    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def list_repositories(self, *, limit=100, offset=0):
        if not self.enabled:
            return []
        return [
            SimpleNamespace(
                repository_id="repo_1",
                repository_full_name="h5vision/api_test",
            )
        ]

    def list_snapshots(self, repository_id, *, limit=100):
        assert repository_id == "repo_1"
        return SimpleNamespace(
            snapshots=[
                SimpleNamespace(
                    snapshot_id="snap_github",
                    commit_sha=COMMIT,
                    tree_sha="c" * 40,
                    fingerprint="d" * 64,
                )
            ]
        )


class FakeProjectSnapshots:
    def __init__(self, *, present: bool = True) -> None:
        self.present = present

    def get_current_snapshot_context(self, project_id):
        if not self.present or project_id != "h5vision/fest-api":
            return None
        return {
            "project_id": project_id,
            "snapshot_id": "snap_current",
            "revision": COMMIT,
            "manifest_sha256": "e" * 64,
            "status": "completed",
        }

    def find_snapshots(self, *, project_id=None, revision=None, limit=20):
        current = self.get_current_snapshot_context(project_id or "")
        if current is None:
            return []
        if revision and revision.lower() != COMMIT:
            return []
        return [current]

    def get_snapshot(self, snapshot_id):
        current = self.get_current_snapshot_context("h5vision/fest-api")
        return current if current and current["snapshot_id"] == snapshot_id else None


def service(*, github=False, project=True):
    return SnapshotComparisonService(
        FakeGithubSnapshots(enabled=github),
        FakeProjectSnapshots(present=project),
    )


def test_project_registry_commit_comparison_returns_same():
    result = service().compare(
        SnapshotCompareRequest(
            project_id="h5vision/fest-api",
            commit_id=COMMIT.upper(),
        ),
        request_id="req_same",
    )
    assert result.comparison == "same"
    assert result.same_version is True
    assert result.update_warning is False
    assert result.baseline_source == "project_registry"
    assert result.baseline_snapshot_id == "snap_current"


def test_different_commit_sets_update_warning():
    result = service().compare(
        SnapshotCompareRequest(
            project_id="h5vision/fest-api",
            commit_id=OTHER_COMMIT,
        ),
        request_id="req_different",
    )
    assert result.comparison == "different"
    assert result.same_version is False
    assert result.update_warning is True
    assert result.reason_code == "different_commit"


def test_missing_baseline_returns_unknown_and_registration_required():
    result = service(project=False).compare(
        SnapshotCompareRequest(
            project_id="flask-realworld-example-app",
            commit_id=COMMIT,
        ),
        request_id="req_unknown",
    )
    assert result.comparison == "unknown"
    assert result.same_version is None
    assert result.registration_required is True
    assert result.baseline_source == "none"


def test_textual_none_is_treated_as_missing_identity():
    payload = SnapshotCompareRequest(
        project_id="h5vision/fest-api",
        commit_id="None",
        snapshot_id="null",
    )
    assert payload.commit_id is None
    assert payload.snapshot_id is None
    result = service().compare(payload, request_id="req_missing")
    assert result.comparison == "unknown"
    assert result.reason_code == "identity_missing"


def test_github_commit_snapshot_is_preferred_when_repository_exists():
    result = service(github=True).compare(
        SnapshotCompareRequest(
            project_id="api_test",
            snapshot_id="snap_github",
        ),
        request_id="req_github",
    )
    assert result.comparison == "same"
    assert result.baseline_source == "github_commit"
    assert result.matched_snapshot_id == "snap_github"


def test_checked_at_is_timezone_aware():
    result = service().compare(
        SnapshotCompareRequest(project_id="h5vision/fest-api", commit_id=COMMIT),
        request_id="req_time",
    )
    assert result.checked_at.tzinfo == timezone.utc
    assert result.checked_at <= datetime.now(timezone.utc)
