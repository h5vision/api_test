from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


RevisionRelation = Literal["same", "different", "unknown"]
RevisionAuthority = Literal["observed", "authoritative", "canonical"]
RemoteResolutionReason = Literal[
    "repository_unbound",
    "branch_unproven",
    "ref_not_found",
    "github_unavailable",
    "rate_limited",
]
RevisionDiffStatus = Literal[
    "not_needed",
    "available",
    "unavailable",
    "base_object_unavailable",
    "target_object_unavailable",
    "failed",
]


class RevisionIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha: str | None = None
    source: str
    authority: RevisionAuthority
    ref: str | None = None
    tree_sha: str | None = None


class RevisionWorkspaceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["clean", "modified", "conflicted", "unknown"] = "unknown"
    dirty: bool | None = None
    working_tree_count: int = Field(default=0, ge=0)
    staged_count: int = Field(default=0, ge=0)
    merge_count: int = Field(default=0, ge=0)
    ahead: int = Field(default=0, ge=0)
    behind: int = Field(default=0, ge=0)


class NormalizedRevisionContext(BaseModel):
    """Backend-only semantic interpretation of the existing Frontend SHA envelope."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    project_id: str
    repository_id: str | None = None
    repository_full_name: str | None = None
    local: RevisionIdentity
    remote: RevisionIdentity
    snapshot_id: str | None = None
    snapshot: RevisionIdentity
    local_vs_remote: RevisionRelation
    local_vs_snapshot: RevisionRelation
    remote_vs_snapshot: RevisionRelation
    remote_resolution_reason: RemoteResolutionReason | None = None
    workspace: RevisionWorkspaceState
    resolved_at: datetime


class RevisionFileChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    old_path: str | None = None
    change_type: Literal["added", "modified", "deleted", "renamed", "copied"]
    blob_sha: str | None = None
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    changes: int = Field(default=0, ge=0)
    patch: str | None = None
    patch_truncated: bool = False


class RevisionDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    status: RevisionDiffStatus
    base_sha: str | None = None
    target_sha: str | None = None
    merge_base_sha: str | None = None
    github_relation: str | None = None
    patch_basis: Literal["none", "snapshot", "merge_base"] = "none"
    safe_to_apply_to_snapshot: bool = False
    ahead_by: int = Field(default=0, ge=0)
    behind_by: int = Field(default=0, ge=0)
    total_commits: int = Field(default=0, ge=0)
    files: list[RevisionFileChange] = Field(default_factory=list)
    file_count: int = Field(default=0, ge=0)
    truncated: bool = False
    reason: str | None = None


class GithubRevisionServiceLike(Protocol):
    def list_repositories(self, *, limit: int = 100, offset: int = 0) -> list[Any]: ...

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


class EphemeralJsonStoreLike(Protocol):
    def set_ephemeral_json(
        self,
        namespace: str,
        key: str,
        value: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> None: ...

    def get_ephemeral_json(self, namespace: str, key: str) -> dict[str, Any] | None: ...


class RevisionContextError(RuntimeError):
    pass


class RevisionObservationError(RuntimeError):
    pass


class RevisionContextService:
    def __init__(
        self,
        github_snapshots: GithubRevisionServiceLike,
        project_snapshots: ProjectSnapshotStoreLike,
    ) -> None:
        self._github = github_snapshots
        self._projects = project_snapshots

    @staticmethod
    def _value(item: Any, key: str) -> Any:
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    @staticmethod
    def _relation(left: str | None, right: str | None) -> RevisionRelation:
        if not left or not right:
            return "unknown"
        return "same" if left == right else "different"

    @staticmethod
    def _remote_error_reason(exc: Exception) -> RemoteResolutionReason:
        status_code = getattr(exc, "status_code", None)
        if status_code == 404:
            return "ref_not_found"
        if status_code == 429:
            return "rate_limited"
        return "github_unavailable"

    def _repository(self, project_id: str) -> tuple[Any | None, bool]:
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

    def _canonical_snapshot(
        self,
        project_id: str,
        snapshot_id: str | None,
        repository_id: str | None,
    ) -> dict[str, Any] | None:
        if not snapshot_id:
            return self._projects.get_current_snapshot_context(project_id)

        direct = self._projects.get_snapshot(snapshot_id)
        if direct is not None:
            direct_repository_id = str(
                direct.get("source_id") or direct.get("repository_id") or ""
            ).strip()
            if (
                repository_id
                and direct_repository_id
                and direct_repository_id != repository_id
            ):
                return None
            return direct

        # Snapshot comparison may expose a GitHub-MVP Snapshot ID. Hydration and
        # project-grounded Chat use project_snapshots, so bridge by immutable commit SHA.
        try:
            github_snapshot = self._github.get_snapshot(snapshot_id)
        except Exception:
            return None
        github_repository_id = (
            str(self._value(github_snapshot, "repository_id") or "").strip() or None
        )
        if not repository_id or github_repository_id != repository_id:
            return None
        revision = (
            str(self._value(github_snapshot, "commit_sha") or "").strip().lower()
            or None
        )
        if not revision:
            return None
        matches = self._projects.find_snapshots(
            project_id=project_id,
            revision=revision,
            limit=10,
        )
        completed = [
            row
            for row in matches
            if str(row.get("status") or "") == "completed"
            and str(row.get("source_id") or row.get("repository_id") or "").strip()
            == repository_id
        ]
        if not completed:
            return None
        current = self._projects.get_current_snapshot_context(project_id)
        current_id = str((current or {}).get("snapshot_id") or "").strip()
        return next(
            (row for row in completed if str(row.get("snapshot_id") or "").strip() == current_id),
            min(completed, key=lambda row: str(row.get("snapshot_id") or "")),
        )

    def resolve(
        self,
        *,
        project_id: str,
        local_head_sha: str | None,
        branch: str | None,
        snapshot_id: str | None,
        workspace_state: Literal["clean", "modified", "conflicted", "unknown"],
        dirty: bool | None = None,
        working_tree_count: int = 0,
        staged_count: int = 0,
        merge_count: int = 0,
        ahead: int = 0,
        behind: int = 0,
    ) -> NormalizedRevisionContext:
        repository_lookup_reason: RemoteResolutionReason | None = None
        try:
            repository, ambiguous = self._repository(project_id)
        except Exception as exc:
            repository = None
            ambiguous = False
            repository_lookup_reason = self._remote_error_reason(exc)
        if ambiguous:
            raise RevisionContextError(
                "project_id가 여러 GitHub Repository와 일치합니다. owner/name을 보내세요."
            )

        repository_id = (
            str(self._value(repository, "repository_id") or "").strip() or None
            if repository is not None
            else None
        )
        repository_full_name = (
            str(self._value(repository, "repository_full_name") or "").strip() or None
            if repository is not None
            else None
        )
        # A missing local branch does not prove that the workspace tracks the
        # registered default branch. Keep the remote axis unknown rather than
        # silently comparing a feature-branch HEAD with default_branch.
        remote_ref = (branch or "").strip() or None
        remote_sha: str | None = None
        remote_tree_sha: str | None = None
        remote_resolution_reason: RemoteResolutionReason | None = None
        if not repository_id:
            remote_resolution_reason = repository_lookup_reason or "repository_unbound"
        elif not remote_ref:
            remote_resolution_reason = "branch_unproven"
        if repository_id and remote_ref:
            try:
                resolved = self._github.resolve_revision(repository_id, remote_ref)
            except Exception as exc:
                # Remote availability is an independent observation. Snapshot comparison
                # must remain usable when GitHub is temporarily unavailable.
                resolved = None
                remote_resolution_reason = self._remote_error_reason(exc)
            if resolved:
                remote_sha = str(resolved.get("commit_sha") or "").strip().lower() or None
                remote_tree_sha = str(resolved.get("tree_sha") or "").strip().lower() or None
                remote_ref = str(resolved.get("ref") or remote_ref).strip() or remote_ref
                remote_resolution_reason = None

        snapshot: dict[str, Any] | None = None
        try:
            snapshot = self._canonical_snapshot(project_id, snapshot_id, repository_id)
        except Exception:
            snapshot = None
        if snapshot is not None:
            snapshot_project = str(snapshot.get("project_id") or project_id).strip()
            if snapshot_project and snapshot_project != project_id:
                snapshot = None

        canonical_snapshot_id = (
            str((snapshot or {}).get("snapshot_id") or "").strip() or None
        )
        snapshot_sha = (
            str((snapshot or {}).get("revision") or "").strip().lower() or None
        )
        local_sha = (local_head_sha or "").strip().lower() or None

        return NormalizedRevisionContext(
            project_id=project_id,
            repository_id=repository_id,
            repository_full_name=repository_full_name,
            local=RevisionIdentity(
                sha=local_sha,
                source="frontend.commit_id",
                authority="observed",
                ref=branch,
            ),
            remote=RevisionIdentity(
                sha=remote_sha,
                source="github.ref",
                authority="authoritative",
                ref=remote_ref,
                tree_sha=remote_tree_sha,
            ),
            snapshot_id=canonical_snapshot_id,
            snapshot=RevisionIdentity(
                sha=snapshot_sha,
                source="project_snapshot",
                authority="canonical",
                ref=str((snapshot or {}).get("git_branch") or "").strip() or None,
                tree_sha=str((snapshot or {}).get("tree_sha") or "").strip().lower() or None,
            ),
            local_vs_remote=self._relation(local_sha, remote_sha),
            local_vs_snapshot=self._relation(local_sha, snapshot_sha),
            remote_vs_snapshot=self._relation(remote_sha, snapshot_sha),
            remote_resolution_reason=remote_resolution_reason,
            workspace=RevisionWorkspaceState(
                state=workspace_state,
                dirty=dirty,
                working_tree_count=working_tree_count,
                staged_count=staged_count,
                merge_count=merge_count,
                ahead=ahead,
                behind=behind,
            ),
            resolved_at=datetime.now(timezone.utc),
        )

    def diff(self, context: NormalizedRevisionContext) -> RevisionDiff:
        base_sha = context.snapshot.sha
        target_sha = context.local.sha
        if not base_sha or not target_sha:
            return RevisionDiff(
                status="unavailable",
                base_sha=base_sha,
                target_sha=target_sha,
                reason="snapshot revision 또는 local HEAD SHA가 없습니다.",
            )
        if base_sha == target_sha:
            return RevisionDiff(
                status="not_needed",
                base_sha=base_sha,
                target_sha=target_sha,
                merge_base_sha=base_sha,
                patch_basis="snapshot",
                safe_to_apply_to_snapshot=True,
            )
        if not context.repository_id:
            return RevisionDiff(
                status="unavailable",
                base_sha=base_sha,
                target_sha=target_sha,
                reason="GitHub Repository binding을 찾을 수 없습니다.",
            )
        try:
            raw = self._github.compare_revisions(
                context.repository_id,
                base_sha,
                target_sha,
            )
        except Exception as exc:
            return RevisionDiff(
                status="failed",
                base_sha=base_sha,
                target_sha=target_sha,
                reason=str(exc),
            )

        status = str(raw.get("status") or "failed")
        if status not in {
            "not_needed",
            "available",
            "base_object_unavailable",
            "target_object_unavailable",
            "failed",
        }:
            status = "failed"
        files: list[RevisionFileChange] = []
        for item in raw.get("files") or []:
            if not isinstance(item, dict):
                continue
            try:
                files.append(RevisionFileChange.model_validate(item))
            except Exception:
                continue
        resolved_base_sha = str(raw.get("base_sha") or base_sha).strip().lower() or None
        merge_base_sha = str(raw.get("merge_base_sha") or "").strip().lower() or None
        github_relation = str(raw.get("github_relation") or "").strip() or None
        safe_to_apply = bool(
            status in {"available", "not_needed"}
            and resolved_base_sha
            and merge_base_sha == resolved_base_sha
        )
        patch_basis: Literal["none", "snapshot", "merge_base"] = (
            "snapshot"
            if safe_to_apply
            else "merge_base"
            if merge_base_sha
            else "none"
        )
        return RevisionDiff(
            status=status,  # type: ignore[arg-type]
            base_sha=resolved_base_sha,
            target_sha=str(raw.get("target_sha") or target_sha).strip().lower() or None,
            merge_base_sha=merge_base_sha,
            github_relation=github_relation,
            patch_basis=patch_basis,
            safe_to_apply_to_snapshot=safe_to_apply,
            ahead_by=max(0, int(raw.get("ahead_by") or 0)),
            behind_by=max(0, int(raw.get("behind_by") or 0)),
            total_commits=max(0, int(raw.get("total_commits") or 0)),
            files=files,
            file_count=max(len(files), int(raw.get("file_count") or 0)),
            truncated=bool(raw.get("truncated")),
            reason=str(raw.get("reason") or "").strip() or None,
        )

    @staticmethod
    def ai_payload(
        context: NormalizedRevisionContext,
        diff: RevisionDiff,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "project_id": context.project_id,
            "snapshot_id": context.snapshot_id,
            "revision": {
                "snapshot_sha": context.snapshot.sha,
                "remote_sha": context.remote.sha,
                "local_sha": context.local.sha,
                "remote_ref": context.remote.ref,
                "local_vs_remote": context.local_vs_remote,
                "local_vs_snapshot": context.local_vs_snapshot,
                "remote_vs_snapshot": context.remote_vs_snapshot,
                "remote_resolution_reason": context.remote_resolution_reason,
            },
            "workspace": context.workspace.model_dump(mode="json"),
            "diff": diff.model_dump(mode="json"),
        }


class RevisionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    context: NormalizedRevisionContext
    diff: RevisionDiff
    observed_at: datetime


class RevisionObservationStore:
    namespace = "revision-observation"

    def __init__(self, coordinator: EphemeralJsonStoreLike, *, ttl_seconds: int = 900) -> None:
        self._coordinator = coordinator
        self._ttl_seconds = max(60, int(ttl_seconds))

    @staticmethod
    def _key(owner_client_id: str, project_id: str, local_head_sha: str | None) -> str:
        owner = owner_client_id.strip()
        project = project_id.strip().casefold()
        revision = (local_head_sha or "").strip().lower()
        if not owner or not project or not revision:
            raise RevisionObservationError("client_id, project_id, local HEAD SHA가 필요합니다.")
        return f"{owner}:{project}:{revision}"

    def record(
        self,
        *,
        owner_client_id: str,
        context: NormalizedRevisionContext,
        diff: RevisionDiff,
    ) -> None:
        observation = RevisionObservation(
            context=context,
            diff=diff,
            observed_at=datetime.now(timezone.utc),
        )
        try:
            self._coordinator.set_ephemeral_json(
                self.namespace,
                self._key(owner_client_id, context.project_id, context.local.sha),
                observation.model_dump(mode="json"),
                ttl_seconds=self._ttl_seconds,
            )
        except Exception as exc:
            raise RevisionObservationError("Revision observation 저장에 실패했습니다.") from exc

    def get(
        self,
        *,
        owner_client_id: str,
        project_id: str,
        local_head_sha: str | None,
    ) -> RevisionObservation | None:
        try:
            raw = self._coordinator.get_ephemeral_json(
                self.namespace,
                self._key(owner_client_id, project_id, local_head_sha),
            )
        except Exception as exc:
            raise RevisionObservationError("Revision observation 조회에 실패했습니다.") from exc
        if raw is None:
            return None
        try:
            return RevisionObservation.model_validate(raw)
        except Exception as exc:
            raise RevisionObservationError("Revision observation 형식이 올바르지 않습니다.") from exc
