from __future__ import annotations

from types import SimpleNamespace

from backend.domains.snapshots.revision_context import (
    RevisionContextService,
    RevisionObservationStore,
)


SNAPSHOT_SHA = "a" * 40
LOCAL_SHA = "b" * 40
TREE_SHA = "c" * 40


class FakeGithubRevisions:
    def __init__(
        self,
        *,
        remote_sha: str = LOCAL_SHA,
        diff_status: str = "available",
        github_relation: str = "ahead",
        merge_base_sha: str = SNAPSHOT_SHA,
    ) -> None:
        self.remote_sha = remote_sha
        self.diff_status = diff_status
        self.github_relation = github_relation
        self.merge_base_sha = merge_base_sha
        self.resolved_refs: list[str] = []

    def list_repositories(self, *, limit: int = 100, offset: int = 0):
        return [
            SimpleNamespace(
                repository_id="repo_1",
                repository_full_name="h5vision/vision",
                default_branch="main",
            )
        ]

    def get_snapshot(self, snapshot_id: str):
        if snapshot_id != "snap_github":
            raise KeyError(snapshot_id)
        return SimpleNamespace(
            snapshot_id=snapshot_id,
            repository_id="repo_1",
            commit_sha=SNAPSHOT_SHA,
        )

    def resolve_revision(self, repository_id: str, ref: str | None = None):
        assert repository_id == "repo_1"
        self.resolved_refs.append(ref or "")
        return {
            "repository_id": repository_id,
            "repository_full_name": "h5vision/vision",
            "ref": ref or "main",
            "commit_sha": self.remote_sha,
            "tree_sha": TREE_SHA,
        }

    def compare_revisions(self, repository_id: str, base_sha: str, target_sha: str):
        assert repository_id == "repo_1"
        if self.diff_status != "available":
            return {
                "status": self.diff_status,
                "base_sha": base_sha,
                "target_sha": target_sha,
                "files": [],
                "file_count": 0,
                "truncated": False,
                "reason": "target is not available on GitHub",
            }
        return {
            "status": "available",
            "github_relation": self.github_relation,
            "base_sha": base_sha,
            "target_sha": target_sha,
            "merge_base_sha": self.merge_base_sha,
            "ahead_by": 1,
            "behind_by": 0,
            "total_commits": 1,
            "files": [
                {
                    "path": "backend/app.py",
                    "old_path": None,
                    "change_type": "modified",
                    "blob_sha": "d" * 40,
                    "additions": 3,
                    "deletions": 1,
                    "changes": 4,
                    "patch": "@@ -1 +1 @@",
                    "patch_truncated": False,
                }
            ],
            "file_count": 1,
            "truncated": False,
            "reason": None,
        }


class FakeProjectSnapshots:
    def __init__(self, revision: str = SNAPSHOT_SHA) -> None:
        self.revision = revision

    def get_current_snapshot_context(self, project_id: str):
        assert project_id == "h5vision/vision"
        return {
            "project_id": project_id,
            "snapshot_id": "snap_current",
            "source_id": "repo_1",
            "revision": self.revision,
            "status": "completed",
        }

    def get_snapshot(self, snapshot_id: str):
        if snapshot_id != "snap_current":
            return None
        return self.get_current_snapshot_context("h5vision/vision")

    def find_snapshots(self, *, project_id=None, revision=None, limit=20):
        current = self.get_current_snapshot_context(project_id or "h5vision/vision")
        if current is None:
            return []
        if revision and str(revision).lower() != str(current["revision"]).lower():
            return []
        return [current]


class FakeCoordinator:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}

    def set_ephemeral_json(self, namespace, key, value, *, ttl_seconds):
        assert ttl_seconds >= 60
        self.rows[(namespace, key)] = value

    def get_ephemeral_json(self, namespace, key):
        return self.rows.get((namespace, key))


def test_local_remote_snapshot_are_normalized_without_frontend_schema_change():
    github = FakeGithubRevisions(remote_sha=LOCAL_SHA)
    service = RevisionContextService(github, FakeProjectSnapshots())

    context = service.resolve(
        project_id="h5vision/vision",
        local_head_sha=LOCAL_SHA.upper(),
        branch="frontend",
        snapshot_id=None,
        workspace_state="clean",
        dirty=False,
    )

    assert context.local.sha == LOCAL_SHA
    assert context.local.source == "frontend.commit_id"
    assert context.remote.sha == LOCAL_SHA
    assert context.remote.ref == "frontend"
    assert context.snapshot.sha == SNAPSHOT_SHA
    assert context.local_vs_remote == "same"
    assert context.local_vs_snapshot == "different"
    assert context.remote_vs_snapshot == "different"
    assert github.resolved_refs == ["frontend"]

    diff = service.diff(context)
    assert diff.status == "available"
    assert diff.base_sha == SNAPSHOT_SHA
    assert diff.target_sha == LOCAL_SHA
    assert diff.file_count == 1
    assert diff.files[0].path == "backend/app.py"
    assert diff.patch_basis == "snapshot"
    assert diff.safe_to_apply_to_snapshot is True


def test_same_commit_with_dirty_workspace_keeps_revision_equal():
    github = FakeGithubRevisions(remote_sha=SNAPSHOT_SHA)
    service = RevisionContextService(github, FakeProjectSnapshots())

    context = service.resolve(
        project_id="h5vision/vision",
        local_head_sha=SNAPSHOT_SHA,
        branch="main",
        snapshot_id="snap_current",
        workspace_state="modified",
        dirty=True,
        working_tree_count=2,
        staged_count=1,
    )

    assert context.local_vs_remote == "same"
    assert context.local_vs_snapshot == "same"
    assert context.workspace.state == "modified"
    assert context.workspace.dirty is True
    assert service.diff(context).status == "not_needed"


def test_diverged_compare_is_context_only_not_marked_apply_safe():
    github = FakeGithubRevisions(
        remote_sha=LOCAL_SHA,
        github_relation="diverged",
        merge_base_sha="e" * 40,
    )
    service = RevisionContextService(github, FakeProjectSnapshots())
    context = service.resolve(
        project_id="h5vision/vision",
        local_head_sha=LOCAL_SHA,
        branch="frontend",
        snapshot_id=None,
        workspace_state="clean",
        dirty=False,
    )

    diff = service.diff(context)
    assert diff.status == "available"
    assert diff.patch_basis == "merge_base"
    assert diff.safe_to_apply_to_snapshot is False


def test_unpushed_local_commit_is_explicitly_unavailable_not_fabricated():
    github = FakeGithubRevisions(
        remote_sha=SNAPSHOT_SHA,
        diff_status="target_object_unavailable",
    )
    service = RevisionContextService(github, FakeProjectSnapshots())
    context = service.resolve(
        project_id="h5vision/vision",
        local_head_sha=LOCAL_SHA,
        branch="main",
        snapshot_id="snap_current",
        workspace_state="clean",
        dirty=False,
        ahead=1,
    )

    diff = service.diff(context)
    assert diff.status == "target_object_unavailable"
    assert diff.files == []
    assert diff.reason


def test_missing_branch_keeps_remote_relation_unknown():
    github = FakeGithubRevisions(remote_sha=LOCAL_SHA)
    service = RevisionContextService(github, FakeProjectSnapshots())
    context = service.resolve(
        project_id="h5vision/vision",
        local_head_sha=LOCAL_SHA,
        branch=None,
        snapshot_id=None,
        workspace_state="unknown",
    )

    assert context.remote.ref is None
    assert context.remote.sha is None
    assert context.local_vs_remote == "unknown"
    assert context.remote_resolution_reason == "branch_unproven"
    assert github.resolved_refs == []


def test_github_snapshot_id_is_bridged_to_canonical_project_snapshot():
    service = RevisionContextService(FakeGithubRevisions(remote_sha=SNAPSHOT_SHA), FakeProjectSnapshots())
    context = service.resolve(
        project_id="h5vision/vision",
        local_head_sha=SNAPSHOT_SHA,
        branch="main",
        snapshot_id="snap_github",
        workspace_state="clean",
        dirty=False,
    )

    assert context.snapshot_id == "snap_current"
    assert context.snapshot.sha == SNAPSHOT_SHA
    assert context.local_vs_snapshot == "same"


def test_revision_observation_round_trips_context_and_diff():
    coordinator = FakeCoordinator()
    store = RevisionObservationStore(coordinator, ttl_seconds=900)
    service = RevisionContextService(FakeGithubRevisions(), FakeProjectSnapshots())
    context = service.resolve(
        project_id="h5vision/vision",
        local_head_sha=LOCAL_SHA,
        branch="frontend",
        snapshot_id=None,
        workspace_state="clean",
        dirty=False,
    )
    diff = service.diff(context)

    store.record(owner_client_id="client-1", context=context, diff=diff)
    observed = store.get(
        owner_client_id="client-1",
        project_id="h5vision/vision",
        local_head_sha=LOCAL_SHA,
    )

    assert observed is not None
    assert observed.context.local.sha == LOCAL_SHA
    assert observed.diff.status == "available"


def test_revision_observation_does_not_match_a_new_local_head():
    coordinator = FakeCoordinator()
    store = RevisionObservationStore(coordinator, ttl_seconds=900)
    service = RevisionContextService(FakeGithubRevisions(), FakeProjectSnapshots())
    context = service.resolve(
        project_id="h5vision/vision",
        local_head_sha=LOCAL_SHA,
        branch="frontend",
        snapshot_id=None,
        workspace_state="modified",
        dirty=True,
    )
    store.record(owner_client_id="client-1", context=context, diff=service.diff(context))

    assert store.get(
        owner_client_id="client-1",
        project_id="h5vision/vision",
        local_head_sha="e" * 40,
    ) is None


def test_github_snapshot_bridge_rejects_another_repository():
    github = FakeGithubRevisions(remote_sha=SNAPSHOT_SHA)
    original = github.get_snapshot

    def wrong_repository(snapshot_id: str):
        value = original(snapshot_id)
        return SimpleNamespace(
            snapshot_id=value.snapshot_id,
            repository_id="repo_other",
            commit_sha=value.commit_sha,
        )

    github.get_snapshot = wrong_repository  # type: ignore[method-assign]
    service = RevisionContextService(github, FakeProjectSnapshots())
    context = service.resolve(
        project_id="h5vision/vision",
        local_head_sha=SNAPSHOT_SHA,
        branch="main",
        snapshot_id="snap_github",
        workspace_state="clean",
    )

    assert context.snapshot_id is None
    assert context.snapshot.sha is None


class RemoteResolutionError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"remote failed: {status_code}")
        self.status_code = status_code


def test_remote_resolution_failure_is_diagnostic_not_an_api_failure():
    github = FakeGithubRevisions()

    def unavailable(repository_id: str, ref: str | None = None):
        raise RemoteResolutionError(429)

    github.resolve_revision = unavailable  # type: ignore[method-assign]
    context = RevisionContextService(github, FakeProjectSnapshots()).resolve(
        project_id="h5vision/vision",
        local_head_sha=LOCAL_SHA,
        branch="main",
        snapshot_id=None,
        workspace_state="clean",
    )

    assert context.remote.sha is None
    assert context.local_vs_remote == "unknown"
    assert context.remote_resolution_reason == "rate_limited"


def test_repository_catalog_failure_is_reported_as_github_unavailable():
    github = FakeGithubRevisions()

    def unavailable(*, limit=100, offset=0):
        raise RemoteResolutionError(503)

    github.list_repositories = unavailable  # type: ignore[method-assign]
    context = RevisionContextService(github, FakeProjectSnapshots()).resolve(
        project_id="h5vision/vision",
        local_head_sha=LOCAL_SHA,
        branch="main",
        snapshot_id=None,
        workspace_state="clean",
    )

    assert context.repository_id is None
    assert context.remote.sha is None
    assert context.remote_resolution_reason == "github_unavailable"
