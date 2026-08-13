from __future__ import annotations


import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone


from ..config import Settings
from .adapters.base import GithubSourceAdapter
from .adapters.github import GithubAdapterError, PublicGithubAdapter
from .contracts import (
    AccessPlan,
    LocatorRecord,
    RepositoryRecord,
    SnapshotFileResponse,
    SnapshotListResponse,
    SnapshotRecord,
    SnapshotRegistrationResponse,
    SnapshotTreeResponse,
    locator_id_for_snapshot,
    normalize_repository_path,
    repository_id_from_provider_id,
    snapshot_fingerprint,
    snapshot_id_from_fingerprint,
)
from .repository import (
    PostgresGithubSnapshotRepository,
    SnapshotIntegrityError,
    SnapshotRepositoryError,
)
from .resolver import GithubSnapshotResolver, SnapshotResolutionError




class GithubSnapshotServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


logger = logging.getLogger(__name__)




@dataclass(frozen=True)
class SnapshotMvpRuntimeConfig:
    tenant_id: str
    allowed_repositories: frozenset[str]
    github_timeout_seconds: int
    max_file_bytes: int


    @classmethod
    def from_environment(cls) -> "SnapshotMvpRuntimeConfig":
        tenant_id = os.getenv("SNAPSHOT_TENANT_ID", "vision-default").strip()
        if not tenant_id:
            raise GithubSnapshotServiceError(
                "SNAPSHOT_TENANT_ID must not be blank",
                status_code=503,
            )
        allowed = frozenset(
            item.strip().casefold()
            for item in os.getenv(
                "SNAPSHOT_ALLOWED_REPOSITORIES",
                "h5vision/api_test",
            ).split(",")
            if item.strip()
        )
        if not allowed:
            raise GithubSnapshotServiceError(
                "SNAPSHOT_ALLOWED_REPOSITORIES must contain at least one repository",
                status_code=503,
            )
        try:
            timeout_seconds = max(
                1,
                int(os.getenv("SNAPSHOT_GITHUB_TIMEOUT_SECONDS", "20")),
            )
            max_file_bytes = max(
                1024,
                int(os.getenv("SNAPSHOT_MAX_FILE_BYTES", str(2 * 1024 * 1024))),
            )
        except ValueError as exc:
            raise GithubSnapshotServiceError(
                "Snapshot numeric environment settings are invalid",
                status_code=503,
            ) from exc
        return cls(
            tenant_id=tenant_id,
            allowed_repositories=allowed,
            github_timeout_seconds=timeout_seconds,
            max_file_bytes=max_file_bytes,
        )




class GithubSnapshotService:
    def __init__(
        self,
        settings: Settings,
        *,
        runtime: SnapshotMvpRuntimeConfig | None = None,
        repository: PostgresGithubSnapshotRepository | None = None,
        github: GithubSourceAdapter | None = None,
        resolver: GithubSnapshotResolver | None = None,
    ) -> None:
        self._runtime = runtime or SnapshotMvpRuntimeConfig.from_environment()
        self._repository = repository or PostgresGithubSnapshotRepository(settings)
        self._github = github or PublicGithubAdapter(
            timeout_seconds=self._runtime.github_timeout_seconds,
            max_file_bytes=self._runtime.max_file_bytes,
        )
        self._resolver = resolver or GithubSnapshotResolver()


    @property
    def tenant_id(self) -> str:
        return self._runtime.tenant_id


    @staticmethod
    def _repository_record(row: dict) -> RepositoryRecord:
        return RepositoryRecord(
            repository_id=row["repository_id"],
            provider="github",
            provider_repository_id=row["provider_repository_id"],
            repository_full_name=row["repository_full_name"],
            repository_url=row["repository_url"],
            default_branch=row["default_branch"],
            visibility="public",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


    @staticmethod
    def _snapshot_record(row: dict) -> SnapshotRecord:
        return SnapshotRecord(
            snapshot_id=row["snapshot_id"],
            repository_id=row["repository_id"],
            snapshot_type="commit",
            commit_sha=row["commit_sha"],
            tree_sha=row["tree_sha"],
            fingerprint=row["fingerprint"],
            verified_by="github",
            verified_at=row["verified_at"],
            created_at=row["created_at"],
        )


    @staticmethod
    def _locator_record(row: dict | None) -> LocatorRecord | None:
        if row is None:
            return None
        return LocatorRecord(
            locator_id=row["locator_id"],
            snapshot_id=row["snapshot_id"],
            provider="github",
            access_mode="backend-proxy",
            availability=row["availability"],
            last_verified_at=row["last_verified_at"],
        )


    def _register_public_repository(
        self,
        repository_full_name: str,
    ) -> RepositoryRecord:
        info = self._github.get_repository(repository_full_name)
        if info.private:
            raise GithubSnapshotServiceError(
                "Private repositories are outside the public GitHub Snapshot MVP",
                status_code=403,
            )
        repository_id = repository_id_from_provider_id(
            self.tenant_id,
            info.provider_repository_id,
        )
        try:
            row = self._repository.upsert_repository(
                tenant_id=self.tenant_id,
                repository_id=repository_id,
                provider_repository_id=info.provider_repository_id,
                repository_full_name=info.repository_full_name,
                repository_url=info.clone_url,
                default_branch=info.default_branch,
            )
        except SnapshotRepositoryError as exc:
            logger.exception("GitHub Snapshot repository registration failed")
            raise GithubSnapshotServiceError(
                "Snapshot storage is unavailable",
                status_code=503,
            ) from exc
        return self._repository_record(row)




    def register_repository(self, repository_full_name: str) -> RepositoryRecord:
        if repository_full_name.casefold() not in self._runtime.allowed_repositories:
            raise GithubSnapshotServiceError(
                "The repository is not in SNAPSHOT_ALLOWED_REPOSITORIES",
                status_code=403,
            )
        return self._register_public_repository(repository_full_name)




    def import_public_repository_snapshot(
        self,
        repository_full_name: str,
        ref: str | None = None,
    ) -> tuple[RepositoryRecord, SnapshotRegistrationResponse, str]:
        repository = self._register_public_repository(repository_full_name)
        resolved_ref = (ref or "").strip() or repository.default_branch
        registration = self.create_snapshot(repository.repository_id, resolved_ref)
        return repository, registration, resolved_ref


    def get_repository(self, repository_id: str) -> RepositoryRecord:
        try:
            row = self._repository.get_repository(self.tenant_id, repository_id)
        except SnapshotRepositoryError as exc:
            logger.exception("GitHub Snapshot repository lookup failed")
            raise GithubSnapshotServiceError(
                "Snapshot storage is unavailable",
                status_code=503,
            ) from exc
        if row is None:
            raise GithubSnapshotServiceError("Repository was not found", status_code=404)
        return self._repository_record(row)


    def list_repositories(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RepositoryRecord]:
        try:
            rows = self._repository.list_repositories(
                self.tenant_id,
                limit=limit,
                offset=offset,
            )
        except SnapshotRepositoryError as exc:
            logger.exception("GitHub Snapshot repository listing failed")
            raise GithubSnapshotServiceError(
                "Snapshot storage is unavailable",
                status_code=503,
            ) from exc
        return [self._repository_record(row) for row in rows]


    def list_snapshots_for_tenant(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SnapshotRecord]:
        try:
            rows = self._repository.list_snapshots_for_tenant(
                self.tenant_id,
                limit=limit,
                offset=offset,
            )
        except SnapshotRepositoryError as exc:
            logger.exception("GitHub Snapshot tenant listing failed")
            raise GithubSnapshotServiceError(
                "Snapshot storage is unavailable",
                status_code=503,
            ) from exc
        return [self._snapshot_record(row) for row in rows]


    def admin_status(self) -> dict[str, int]:
        try:
            return self._repository.admin_status(self.tenant_id)
        except SnapshotRepositoryError as exc:
            logger.exception("GitHub Snapshot admin status failed")
            raise GithubSnapshotServiceError(
                "Snapshot storage is unavailable",
                status_code=503,
            ) from exc


    def resolve_revision(
        self,
        repository_id: str,
        ref: str | None = None,
    ) -> dict[str, str]:
        """Resolve a repository ref without registering a Snapshot."""
        repository = self.get_repository(repository_id)
        resolved_ref = (ref or "").strip() or repository.default_branch
        try:
            commit = self._github.resolve_commit(
                repository.repository_full_name,
                resolved_ref,
            )
        except GithubAdapterError as exc:
            raise GithubSnapshotServiceError(
                str(exc),
                status_code=exc.status_code,
            ) from exc
        return {
            "repository_id": repository.repository_id,
            "repository_full_name": repository.repository_full_name,
            "ref": resolved_ref,
            "commit_sha": commit.commit_sha,
            "tree_sha": commit.tree_sha,
        }

    def compare_revisions(
        self,
        repository_id: str,
        base_sha: str,
        target_sha: str,
    ) -> dict:
        """Compare two GitHub-known revisions without mutating Snapshot storage."""
        repository = self.get_repository(repository_id)
        if base_sha.strip().lower() == target_sha.strip().lower():
            return {
                "status": "not_needed",
                "base_sha": base_sha.strip().lower(),
                "target_sha": target_sha.strip().lower(),
                "merge_base_sha": base_sha.strip().lower(),
                "ahead_by": 0,
                "behind_by": 0,
                "total_commits": 0,
                "files": [],
                "file_count": 0,
                "truncated": False,
                "reason": None,
            }
        try:
            result = self._github.compare_commits(
                repository.repository_full_name,
                base_sha,
                target_sha,
            )
            return {
                **result,
                "status": "available",
                "github_relation": result.get("status"),
                "reason": result.get("reason"),
            }
        except GithubAdapterError as exc:
            if exc.status_code != 404:
                return {
                    "status": "failed",
                    "base_sha": base_sha.strip().lower(),
                    "target_sha": target_sha.strip().lower(),
                    "files": [],
                    "file_count": 0,
                    "truncated": False,
                    "reason": str(exc),
                }

            # A compare 404 is ambiguous. Resolve both objects separately so an
            # unpushed local target is distinguishable from a missing baseline.
            try:
                self._github.resolve_commit(repository.repository_full_name, base_sha)
            except GithubAdapterError as base_exc:
                if base_exc.status_code == 404:
                    return {
                        "status": "base_object_unavailable",
                        "base_sha": base_sha.strip().lower(),
                        "target_sha": target_sha.strip().lower(),
                        "files": [],
                        "file_count": 0,
                        "truncated": False,
                        "reason": str(base_exc),
                    }
                return {
                    "status": "failed",
                    "base_sha": base_sha.strip().lower(),
                    "target_sha": target_sha.strip().lower(),
                    "files": [],
                    "file_count": 0,
                    "truncated": False,
                    "reason": str(base_exc),
                }
            try:
                self._github.resolve_commit(repository.repository_full_name, target_sha)
            except GithubAdapterError as target_exc:
                if target_exc.status_code == 404:
                    return {
                        "status": "target_object_unavailable",
                        "base_sha": base_sha.strip().lower(),
                        "target_sha": target_sha.strip().lower(),
                        "files": [],
                        "file_count": 0,
                        "truncated": False,
                        "reason": str(target_exc),
                    }
                return {
                    "status": "failed",
                    "base_sha": base_sha.strip().lower(),
                    "target_sha": target_sha.strip().lower(),
                    "files": [],
                    "file_count": 0,
                    "truncated": False,
                    "reason": str(target_exc),
                }
            return {
                "status": "failed",
                "base_sha": base_sha.strip().lower(),
                "target_sha": target_sha.strip().lower(),
                "files": [],
                "file_count": 0,
                "truncated": False,
                "reason": str(exc),
            }

    def create_snapshot(
        self,
        repository_id: str,
        ref: str,
    ) -> SnapshotRegistrationResponse:
        repository = self.get_repository(repository_id)
        resolved_ref = ref.strip() or repository.default_branch
        commit = self._github.resolve_commit(
            repository.repository_full_name,
            resolved_ref,
        )
        fingerprint = snapshot_fingerprint(
            tenant_id=self.tenant_id,
            provider_repository_id=repository.provider_repository_id,
            commit_sha=commit.commit_sha,
        )
        snapshot_id = snapshot_id_from_fingerprint(fingerprint)
        locator_id = locator_id_for_snapshot(self.tenant_id, snapshot_id)
        verified_at = datetime.now(timezone.utc)
        try:
            snapshot_row, _locator_row, deduplicated = (
                self._repository.register_verified_snapshot(
                    tenant_id=self.tenant_id,
                    snapshot_id=snapshot_id,
                    repository_id=repository.repository_id,
                    commit_sha=commit.commit_sha,
                    tree_sha=commit.tree_sha,
                    fingerprint=fingerprint,
                    locator_id=locator_id,
                    locator_details={
                        "repository_full_name": repository.repository_full_name,
                        "commit_sha": commit.commit_sha,
                        "tree_sha": commit.tree_sha,
                    },
                    verified_at=verified_at,
                )
            )
        except SnapshotIntegrityError as exc:
            logger.warning("Verified GitHub Snapshot identity conflict: %s", exc)
            raise GithubSnapshotServiceError(
                "Stored Snapshot identity conflicts with GitHub",
                status_code=409,
            ) from exc
        except SnapshotRepositoryError as exc:
            logger.exception("Verified GitHub Snapshot persistence failed")
            raise GithubSnapshotServiceError(
                "Snapshot storage is unavailable",
                status_code=503,
            ) from exc
        return SnapshotRegistrationResponse(
            snapshot=self._snapshot_record(snapshot_row),
            deduplicated=deduplicated,
        )


    def get_snapshot(self, snapshot_id: str) -> SnapshotRecord:
        try:
            row = self._repository.get_snapshot(self.tenant_id, snapshot_id)
        except SnapshotRepositoryError as exc:
            logger.exception("GitHub Snapshot lookup failed")
            raise GithubSnapshotServiceError(
                "Snapshot storage is unavailable",
                status_code=503,
            ) from exc
        if row is None:
            raise GithubSnapshotServiceError("Snapshot was not found", status_code=404)
        return self._snapshot_record(row)


    def get_locator(self, snapshot_id: str) -> LocatorRecord | None:
        self.get_snapshot(snapshot_id)
        try:
            row = self._repository.get_github_locator(self.tenant_id, snapshot_id)
        except SnapshotRepositoryError as exc:
            logger.exception("GitHub Snapshot locator lookup failed")
            raise GithubSnapshotServiceError(
                "Snapshot storage is unavailable",
                status_code=503,
            ) from exc
        return self._locator_record(row)


    def list_snapshots(
        self,
        repository_id: str,
        *,
        limit: int = 100,
    ) -> SnapshotListResponse:
        self.get_repository(repository_id)
        try:
            rows = self._repository.list_snapshots(
                self.tenant_id,
                repository_id,
                limit=limit,
            )
        except SnapshotRepositoryError as exc:
            logger.exception("GitHub Snapshot list failed")
            raise GithubSnapshotServiceError(
                "Snapshot storage is unavailable",
                status_code=503,
            ) from exc
        snapshots = [self._snapshot_record(row) for row in rows]
        return SnapshotListResponse(
            repository_id=repository_id,
            snapshots=snapshots,
            total=len(snapshots),
        )


    def _snapshot_repository_locator(
        self,
        snapshot_id: str,
    ) -> tuple[SnapshotRecord, RepositoryRecord, LocatorRecord | None]:
        snapshot = self.get_snapshot(snapshot_id)
        repository = self.get_repository(snapshot.repository_id)
        try:
            locator_row = self._repository.get_github_locator(
                self.tenant_id,
                snapshot.snapshot_id,
            )
        except SnapshotRepositoryError as exc:
            logger.exception("GitHub Snapshot locator lookup failed")
            raise GithubSnapshotServiceError(
                "Snapshot storage is unavailable",
                status_code=503,
            ) from exc
        return snapshot, repository, self._locator_record(locator_row)


    def _resolve_locator(
        self,
        snapshot: SnapshotRecord,
        locator: LocatorRecord | None,
    ) -> AccessPlan:
        try:
            return self._resolver.resolve(snapshot, locator)
        except SnapshotResolutionError as exc:
            raise GithubSnapshotServiceError(
                "Stored Snapshot locator is inconsistent",
                status_code=409,
            ) from exc


    def resolve(self, snapshot_id: str) -> AccessPlan:
        snapshot, _repository, locator = self._snapshot_repository_locator(snapshot_id)
        return self._resolve_locator(snapshot, locator)


    def tree(self, snapshot_id: str) -> SnapshotTreeResponse:
        snapshot, repository, locator = self._snapshot_repository_locator(snapshot_id)
        plan = self._resolve_locator(snapshot, locator)
        if not plan.available:
            raise GithubSnapshotServiceError(
                plan.reason or "Snapshot is unavailable",
                status_code=409,
            )
        self._github.verify_commit_tree(
            repository.repository_full_name,
            snapshot.commit_sha,
            snapshot.tree_sha,
        )
        entries = self._github.get_tree(
            repository.repository_full_name,
            snapshot.tree_sha,
        )
        return SnapshotTreeResponse(
            snapshot_id=snapshot.snapshot_id,
            repository_id=snapshot.repository_id,
            commit_sha=snapshot.commit_sha,
            tree_sha=snapshot.tree_sha,
            entries=entries,
            total=len(entries),
        )


    def file(self, snapshot_id: str, path: str) -> SnapshotFileResponse:
        snapshot, repository, locator = self._snapshot_repository_locator(snapshot_id)
        plan = self._resolve_locator(snapshot, locator)
        if not plan.available:
            raise GithubSnapshotServiceError(
                plan.reason or "Snapshot is unavailable",
                status_code=409,
            )
        normalized_path = normalize_repository_path(path)
        self._github.verify_commit_tree(
            repository.repository_full_name,
            snapshot.commit_sha,
            snapshot.tree_sha,
        )
        source_file = self._github.get_file(
            repository.repository_full_name,
            snapshot.commit_sha,
            normalized_path,
        )
        return SnapshotFileResponse(
            snapshot_id=snapshot.snapshot_id,
            repository_id=snapshot.repository_id,
            commit_sha=snapshot.commit_sha,
            tree_sha=snapshot.tree_sha,
            path=source_file.path,
            blob_sha=source_file.blob_sha,
            size=source_file.size,
            content=source_file.content,
        )
