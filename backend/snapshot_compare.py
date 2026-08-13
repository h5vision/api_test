from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .domains.snapshots.revision_context import (
    NormalizedRevisionContext,
    RevisionContextError,
    RevisionContextService,
    RevisionDiff,
)


SnapshotComparison = Literal["same", "different", "unknown"]
SnapshotBaselineSource = Literal["github_commit", "project_registry", "none"]
WorkspaceState = Literal["clean", "modified", "conflicted", "unknown"]


class SnapshotGitState(BaseModel):
    """Observed Git working-tree state supplied by the IDE.

    This state never changes revision equality. It only answers whether the current
    workspace contents still match an otherwise-equal immutable Snapshot.
    """

    model_config = ConfigDict(extra="ignore")

    branch: str | None = Field(default=None, max_length=255)
    dirty: bool | None = None
    working_tree_count: int = Field(default=0, ge=0, le=1_000_000)
    staged_count: int = Field(default=0, ge=0, le=1_000_000)
    merge_count: int = Field(default=0, ge=0, le=1_000_000)
    ahead: int = Field(default=0, ge=0, le=1_000_000)
    behind: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("branch", mode="before")
    @classmethod
    def normalize_branch(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    def workspace_state(self) -> WorkspaceState:
        if self.merge_count > 0:
            return "conflicted"
        if self.dirty is True or self.working_tree_count > 0 or self.staged_count > 0:
            return "modified"
        if self.dirty is False:
            return "clean"
        return "unknown"


class SnapshotCompareRequest(BaseModel):
    """Frontend identity used to compare a workspace with the Backend baseline."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "examples": [
                {
                    "project_id": "h5vision/fest-api",
                    "commit_id": "afe41126f624af30038cc8e17b2aaf60ebd4b838",
                    "snapshot_id": None,
                    "git_state": {
                        "branch": "main",
                        "dirty": False,
                        "working_tree_count": 0,
                        "staged_count": 0,
                        "merge_count": 0,
                        "ahead": 0,
                        "behind": 0,
                    },
                }
            ]
        },
    )

    project_id: str = Field(..., min_length=1, max_length=255)
    commit_id: str | None = Field(
        default=None,
        pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$",
    )
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=255)
    git_state: SnapshotGitState | None = None

    @field_validator("project_id")
    @classmethod
    def normalize_project_id(cls, value: str) -> str:
        normalized = value.strip().strip("/")
        if not normalized:
            raise ValueError("project_id must not be blank")
        return normalized

    @field_validator("commit_id", "snapshot_id", mode="before")
    @classmethod
    def normalize_optional_identity(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.casefold() in {"", "none", "null", "undefined"}:
                return None
            return normalized
        return value

    @field_validator("commit_id")
    @classmethod
    def normalize_commit_id(cls, value: str | None) -> str | None:
        return value.lower() if value else None


class SnapshotCompareResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    checked_at: datetime
    project_id: str
    comparison: SnapshotComparison
    same_version: bool | None
    workspace_state: WorkspaceState = "unknown"
    workspace_matches_snapshot: bool | None = None
    update_warning: bool
    registration_required: bool
    baseline_source: SnapshotBaselineSource
    baseline_snapshot_id: str | None = None
    baseline_commit_id: str | None = None
    requested_snapshot_id: str | None = None
    requested_commit_id: str | None = None
    matched_snapshot_id: str | None = None
    reason_code: str
    message: str
    revision_context: NormalizedRevisionContext | None = None
    revision_diff: RevisionDiff | None = None


class GithubSnapshotServiceLike(Protocol):
    def list_repositories(self, *, limit: int = 100, offset: int = 0) -> list[Any]: ...

    def list_snapshots(self, repository_id: str, *, limit: int = 100) -> Any: ...

    def get_snapshot(self, snapshot_id: str) -> Any: ...

    def resolve_revision(self, repository_id: str, ref: str | None = None) -> dict[str, str]: ...

    def compare_revisions(
        self,
        repository_id: str,
        base_sha: str,
        target_sha: str,
    ) -> dict[str, Any]: ...


class ProjectSnapshotStoreLike(Protocol):
    def get_current_snapshot_context(self, project_id: str) -> dict[str, Any] | None: ...

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None: ...

    def find_snapshots(
        self,
        *,
        project_id: str | None = None,
        revision: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...


class SnapshotComparisonError(RuntimeError):
    pass


class SnapshotComparisonService:
    """Bridge the GitHub Commit Control Plane and the legacy project registry."""

    def __init__(
        self,
        github_snapshots: GithubSnapshotServiceLike,
        project_snapshots: ProjectSnapshotStoreLike,
        *,
        revision_context_service: RevisionContextService | None = None,
    ) -> None:
        self._github = github_snapshots
        self._projects = project_snapshots
        self._revisions = revision_context_service or RevisionContextService(
            github_snapshots,
            project_snapshots,
        )

    @staticmethod
    def _value(item: Any, key: str) -> Any:
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    def _github_repository(self, project_id: str) -> tuple[Any | None, bool]:
        repositories = self._github.list_repositories(limit=500, offset=0)
        normalized = project_id.casefold()
        exact = [
            item
            for item in repositories
            if str(self._value(item, "repository_full_name") or "").casefold()
            == normalized
        ]
        if exact:
            return exact[0], False
        if "/" in project_id:
            return None, False
        suffix = f"/{normalized}"
        short_matches = [
            item
            for item in repositories
            if str(self._value(item, "repository_full_name") or "")
            .casefold()
            .endswith(suffix)
        ]
        if len(short_matches) == 1:
            return short_matches[0], False
        return None, len(short_matches) > 1

    def _github_baseline(
        self,
        project_id: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bool]:
        repository, ambiguous = self._github_repository(project_id)
        if repository is None:
            return None, [], ambiguous
        repository_id = str(self._value(repository, "repository_id") or "")
        result = self._github.list_snapshots(repository_id, limit=500)
        records = [
            {
                "snapshot_id": str(self._value(item, "snapshot_id") or ""),
                "project_id": str(
                    self._value(repository, "repository_full_name") or project_id
                ),
                "commit_id": str(self._value(item, "commit_sha") or "").lower(),
                "tree_sha": str(self._value(item, "tree_sha") or "").lower(),
                "fingerprint": str(self._value(item, "fingerprint") or ""),
            }
            for item in list(self._value(result, "snapshots") or [])
        ]
        return (records[0] if records else None), records, False

    def _project_baseline(
        self,
        project_id: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        current = self._projects.get_current_snapshot_context(project_id)
        records = self._projects.find_snapshots(project_id=project_id, limit=100)
        completed = [item for item in records if str(item.get("status")) == "completed"]
        baseline = current or (completed[0] if completed else None)
        normalized = [
            {
                "snapshot_id": str(item.get("snapshot_id") or ""),
                "project_id": str(item.get("project_id") or project_id),
                "commit_id": str(item.get("revision") or "").lower(),
                "manifest_sha256": str(item.get("manifest_sha256") or ""),
                "status": str(item.get("status") or ""),
            }
            for item in records
        ]
        if baseline is not None:
            normalized_baseline = {
                "snapshot_id": str(baseline.get("snapshot_id") or ""),
                "project_id": str(baseline.get("project_id") or project_id),
                "commit_id": str(baseline.get("revision") or "").lower(),
                "manifest_sha256": str(baseline.get("manifest_sha256") or ""),
                "status": str(baseline.get("status") or "completed"),
            }
        else:
            normalized_baseline = None
        return normalized_baseline, normalized

    def revision_context(
        self,
        payload: SnapshotCompareRequest,
    ) -> NormalizedRevisionContext:
        git_state = payload.git_state
        workspace_state: WorkspaceState = (
            git_state.workspace_state() if git_state is not None else "unknown"
        )
        return self._revisions.resolve(
            project_id=payload.project_id,
            local_head_sha=payload.commit_id,
            branch=git_state.branch if git_state is not None else None,
            snapshot_id=payload.snapshot_id,
            workspace_state=workspace_state,
            dirty=git_state.dirty if git_state is not None else None,
            working_tree_count=git_state.working_tree_count if git_state is not None else 0,
            staged_count=git_state.staged_count if git_state is not None else 0,
            merge_count=git_state.merge_count if git_state is not None else 0,
            ahead=git_state.ahead if git_state is not None else 0,
            behind=git_state.behind if git_state is not None else 0,
        )

    def _response(
        self,
        *,
        request_id: str,
        payload: SnapshotCompareRequest,
        comparison: SnapshotComparison,
        baseline_source: SnapshotBaselineSource,
        baseline: dict[str, Any] | None,
        matched_snapshot_id: str | None,
        reason_code: str,
        message: str,
        registration_required: bool = False,
    ) -> SnapshotCompareResponse:
        workspace_state: WorkspaceState = (
            payload.git_state.workspace_state() if payload.git_state is not None else "unknown"
        )
        if comparison == "different":
            workspace_matches_snapshot: bool | None = False
        elif comparison == "unknown":
            workspace_matches_snapshot = None
        elif workspace_state == "clean":
            workspace_matches_snapshot = True
        elif workspace_state in {"modified", "conflicted"}:
            workspace_matches_snapshot = False
        else:
            workspace_matches_snapshot = None

        effective_reason = reason_code
        effective_message = message
        if comparison == "same" and workspace_state == "modified":
            effective_reason = "working_tree_modified"
            effective_message = (
                "Git revision은 Backend 기준 Snapshot과 같지만 working tree 또는 staged "
                "파일이 수정되어 현재 Workspace 내용은 Snapshot과 다릅니다."
            )
        elif comparison == "same" and workspace_state == "conflicted":
            effective_reason = "working_tree_conflicted"
            effective_message = (
                "Git revision은 Backend 기준 Snapshot과 같지만 merge conflict가 있어 "
                "현재 Workspace 내용은 Snapshot과 다릅니다."
            )

        try:
            revision_context = self.revision_context(payload)
            revision_diff = self._revisions.diff(revision_context)
        except RevisionContextError:
            revision_context = None
            revision_diff = None
        except Exception:
            # Live GitHub resolution is diagnostic/enrichment and must not break
            # the frozen Snapshot comparison contract.
            revision_context = None
            revision_diff = None

        remote_update_available = bool(
            revision_context is not None
            and revision_context.local_vs_remote == "different"
        )
        if (
            comparison == "same"
            and remote_update_available
            and workspace_state not in {"modified", "conflicted"}
        ):
            effective_reason = "remote_revision_different"
            effective_message = (
                "Frontend HEAD는 Backend 기준 Snapshot과 같지만 현재 Git branch의 "
                "원격 revision과 다릅니다. 갱신 여부를 확인하세요."
            )

        return SnapshotCompareResponse(
            request_id=request_id,
            checked_at=datetime.now(timezone.utc),
            project_id=payload.project_id,
            comparison=comparison,
            same_version=(
                True if comparison == "same" else False if comparison == "different" else None
            ),
            workspace_state=workspace_state,
            workspace_matches_snapshot=workspace_matches_snapshot,
            update_warning=(
                comparison == "different"
                or workspace_matches_snapshot is False
                or remote_update_available
            ),
            registration_required=registration_required,
            baseline_source=baseline_source,
            baseline_snapshot_id=(baseline or {}).get("snapshot_id") or None,
            baseline_commit_id=(baseline or {}).get("commit_id") or None,
            requested_snapshot_id=payload.snapshot_id,
            requested_commit_id=payload.commit_id,
            matched_snapshot_id=matched_snapshot_id,
            reason_code=effective_reason,
            message=effective_message,
            revision_context=revision_context,
            revision_diff=revision_diff,
        )

    def compare(
        self,
        payload: SnapshotCompareRequest,
        *,
        request_id: str,
    ) -> SnapshotCompareResponse:
        try:
            baseline, records, ambiguous = self._github_baseline(payload.project_id)
            source: SnapshotBaselineSource = "github_commit"
            if ambiguous:
                return self._response(
                    request_id=request_id,
                    payload=payload,
                    comparison="unknown",
                    baseline_source="none",
                    baseline=None,
                    matched_snapshot_id=None,
                    reason_code="ambiguous_project",
                    message="project_id가 여러 GitHub Repository와 일치합니다. owner/name을 보내세요.",
                )
            if baseline is None:
                baseline, records = self._project_baseline(payload.project_id)
                source = "project_registry" if baseline is not None else "none"
        except Exception as exc:
            raise SnapshotComparisonError("Snapshot 기준 조회에 실패했습니다.") from exc

        if baseline is None:
            return self._response(
                request_id=request_id,
                payload=payload,
                comparison="unknown",
                baseline_source="none",
                baseline=None,
                matched_snapshot_id=None,
                reason_code="baseline_not_found",
                message="Backend에 비교할 기준 Snapshot이 없습니다.",
                registration_required=True,
            )

        if payload.snapshot_id:
            requested = next(
                (
                    item
                    for item in records
                    if item.get("snapshot_id") == payload.snapshot_id
                ),
                None,
            )
            if requested is None:
                return self._response(
                    request_id=request_id,
                    payload=payload,
                    comparison="unknown",
                    baseline_source=source,
                    baseline=baseline,
                    matched_snapshot_id=None,
                    reason_code="snapshot_not_found",
                    message="요청한 snapshot_id를 해당 프로젝트에서 찾을 수 없습니다.",
                    registration_required=True,
                )
            requested_commit = str(requested.get("commit_id") or "") or None
            if payload.commit_id and requested_commit and payload.commit_id != requested_commit:
                return self._response(
                    request_id=request_id,
                    payload=payload,
                    comparison="unknown",
                    baseline_source=source,
                    baseline=baseline,
                    matched_snapshot_id=str(requested.get("snapshot_id") or "") or None,
                    reason_code="identity_conflict",
                    message="commit_id와 snapshot_id가 서로 다른 상태를 가리킵니다.",
                )
            if payload.snapshot_id == baseline.get("snapshot_id"):
                return self._response(
                    request_id=request_id,
                    payload=payload,
                    comparison="same",
                    baseline_source=source,
                    baseline=baseline,
                    matched_snapshot_id=payload.snapshot_id,
                    reason_code="same_snapshot",
                    message="Frontend와 Backend의 기준 Snapshot이 같습니다.",
                )
            return self._response(
                request_id=request_id,
                payload=payload,
                comparison="different",
                baseline_source=source,
                baseline=baseline,
                matched_snapshot_id=payload.snapshot_id,
                reason_code="different_snapshot",
                message="Frontend Snapshot이 Backend 기준 Snapshot과 다릅니다. 갱신이 필요합니다.",
            )

        if not payload.commit_id:
            return self._response(
                request_id=request_id,
                payload=payload,
                comparison="unknown",
                baseline_source=source,
                baseline=baseline,
                matched_snapshot_id=None,
                reason_code="identity_missing",
                message="비교하려면 commit_id 또는 snapshot_id가 필요합니다.",
            )

        matching = next(
            (item for item in records if item.get("commit_id") == payload.commit_id),
            None,
        )
        if payload.commit_id == baseline.get("commit_id"):
            return self._response(
                request_id=request_id,
                payload=payload,
                comparison="same",
                baseline_source=source,
                baseline=baseline,
                # The active/verified baseline owns equality. Historical failed or
                # building records can share a revision in the legacy registry.
                matched_snapshot_id=baseline.get("snapshot_id") or None,
                reason_code="same_commit",
                message="Frontend Commit이 Backend 기준 Snapshot의 Commit과 같습니다.",
            )
        return self._response(
            request_id=request_id,
            payload=payload,
            comparison="different",
            baseline_source=source,
            baseline=baseline,
            matched_snapshot_id=(matching or {}).get("snapshot_id") or None,
            reason_code="different_commit",
            message="Frontend Commit이 Backend 기준 Snapshot과 다릅니다. 갱신 경고를 표시하세요.",
        )
