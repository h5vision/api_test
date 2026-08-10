from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


SnapshotComparison = Literal["same", "different", "unknown"]
SnapshotBaselineSource = Literal["github_commit", "project_registry", "none"]


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


class GithubSnapshotServiceLike(Protocol):
    def list_repositories(self, *, limit: int = 100, offset: int = 0) -> list[Any]: ...

    def list_snapshots(self, repository_id: str, *, limit: int = 100) -> Any: ...

    def get_snapshot(self, snapshot_id: str) -> Any: ...


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
    ) -> None:
        self._github = github_snapshots
        self._projects = project_snapshots

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

    @staticmethod
    def _response(
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
        return SnapshotCompareResponse(
            request_id=request_id,
            checked_at=datetime.now(timezone.utc),
            project_id=payload.project_id,
            comparison=comparison,
            same_version=(
                True if comparison == "same" else False if comparison == "different" else None
            ),
            update_warning=comparison == "different",
            registration_required=registration_required,
            baseline_source=baseline_source,
            baseline_snapshot_id=(baseline or {}).get("snapshot_id") or None,
            baseline_commit_id=(baseline or {}).get("commit_id") or None,
            requested_snapshot_id=payload.snapshot_id,
            requested_commit_id=payload.commit_id,
            matched_snapshot_id=matched_snapshot_id,
            reason_code=reason_code,
            message=message,
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
