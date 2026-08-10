from __future__ import annotations

from .adapters.github import GitHubAdapter, GitHubAdapterError
from .contracts import (
    SnapshotImportResponse,
    repository_full_name_from_url,
    repository_id_from_provider_id,
    snapshot_fingerprint,
    snapshot_id_from_fingerprint,
)
from .repository import PostgresSnapshotRepository, SnapshotRepositoryError
from ..config import Settings


class SnapshotServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class SnapshotService:
    def __init__(
        self,
        settings: Settings,
        *,
        repository: PostgresSnapshotRepository | None = None,
        github: GitHubAdapter | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository or PostgresSnapshotRepository(settings)
        self._github = github or GitHubAdapter(
            token=settings.snapshot_github_token,
            timeout_seconds=settings.snapshot_github_timeout_seconds,
        )

    @property
    def github_authenticated(self) -> bool:
        return self._github.authenticated

    def import_github_snapshot(self, repository_url: str, ref: str | None = None) -> SnapshotImportResponse:
        try:
            repository_full_name = repository_full_name_from_url(repository_url)
            allowed = self._settings.snapshot_allowed_repositories
            if allowed and repository_full_name.casefold() not in allowed:
                raise SnapshotServiceError("Repository is not in SNAPSHOT_ALLOWED_REPOSITORIES", status_code=403)
            info = self._github.get_repository(repository_full_name)
            repository_id = repository_id_from_provider_id(
                self._settings.snapshot_tenant_id,
                "github",
                info.provider_repository_id,
            )
            repository = self._repository.upsert_repository(
                repository_id=repository_id,
                tenant_id=self._settings.snapshot_tenant_id,
                project_id=info.repository_full_name,
                source_type="github",
                repository_url=info.clone_url,
                default_branch=info.default_branch,
                provider_repository_id=info.provider_repository_id,
            )
            resolved_ref = (ref or "").strip() or info.default_branch
            commit = self._github.resolve_commit(info.repository_full_name, resolved_ref)
            fingerprint = snapshot_fingerprint(
                tenant_id=self._settings.snapshot_tenant_id,
                repository_id=repository.repository_id,
                snapshot_kind="git-commit",
                revision=commit.commit_sha,
                tree_sha=commit.tree_sha,
            )
            snapshot_id = snapshot_id_from_fingerprint(fingerprint)
            snapshot, deduplicated = self._repository.register_snapshot(
                snapshot_id=snapshot_id,
                tenant_id=self._settings.snapshot_tenant_id,
                repository_id=repository.repository_id,
                project_id=repository.project_id,
                snapshot_kind="git-commit",
                revision=commit.commit_sha,
                branch=resolved_ref if resolved_ref != commit.commit_sha else None,
                dirty=False,
                committed_at=None,
                tree_sha=commit.tree_sha,
                manifest_sha256=None,
                fingerprint=fingerprint,
                verified_by="github",
                locator={
                    "provider": "github",
                    "repository_full_name": info.repository_full_name,
                    "commit_sha": commit.commit_sha,
                    "tree_sha": commit.tree_sha,
                    "access_mode": "backend-proxy",
                },
                status="captured",
            )
            return SnapshotImportResponse(
                repository=repository,
                snapshot=snapshot,
                deduplicated=deduplicated,
                resolved_ref=resolved_ref,
                github_authentication="authenticated" if self.github_authenticated else "anonymous",
            )
        except SnapshotServiceError:
            raise
        except GitHubAdapterError as exc:
            raise SnapshotServiceError(str(exc), status_code=exc.status_code) from exc
        except SnapshotRepositoryError as exc:
            raise SnapshotServiceError(str(exc), status_code=503) from exc

