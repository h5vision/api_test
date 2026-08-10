from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..config import Settings
from ..schema_guard import SchemaStateError, require_schema
from .contracts import RepositoryRecord, SnapshotRecord


class SnapshotRepositoryError(RuntimeError):
    pass


class SnapshotIntegrityError(SnapshotRepositoryError):
    pass


class PostgresSnapshotRepository:
    """Canonical Snapshot registry backed by existing Vision repository tables.

    P1 intentionally reuses repository_sources/project_snapshots instead of
    creating a second snapshot_mvp_* schema. Current indexing and future
    Context/VectorIndex/Exchange layers therefore share one snapshot identity.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._settings.postgres_password)

    def _connect(self):
        if not self.configured:
            raise SnapshotRepositoryError("PostgreSQL Snapshot registry is not configured")
        return psycopg.connect(
            host=self._settings.postgres_host,
            port=self._settings.postgres_port,
            dbname=self._settings.postgres_db,
            user=self._settings.postgres_user,
            password=self._settings.postgres_password,
            connect_timeout=self._settings.postgres_connect_timeout_seconds,
            row_factory=dict_row,
        )

    def ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            try:
                with self._connect() as connection:
                    require_schema(connection)
                self._initialized = True
            except (psycopg.Error, OSError, SchemaStateError) as exc:
                raise SnapshotRepositoryError(
                    "PostgreSQL schema is not on the required Alembic baseline"
                ) from exc

    @staticmethod
    def _repository_record(row: dict[str, Any]) -> RepositoryRecord:
        return RepositoryRecord(
            repository_id=str(row["source_id"]),
            tenant_id=str(row.get("tenant_id") or "default"),
            project_id=str(row["project_id"]),
            source_type=str(row["source_type"]),
            repository_url=row.get("repository_url"),
            default_branch=row.get("default_branch"),
            provider_repository_id=row.get("provider_repository_id"),
            enabled=bool(row.get("enabled", True)),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _snapshot_record(row: dict[str, Any]) -> SnapshotRecord:
        return SnapshotRecord(
            snapshot_id=str(row["snapshot_id"]),
            tenant_id=str(row.get("tenant_id") or "default"),
            repository_id=str(row["source_id"]),
            project_id=str(row["project_id"]),
            snapshot_kind=str(row.get("snapshot_kind") or "working-tree"),
            revision=row.get("revision"),
            git_branch=row.get("git_branch"),
            git_dirty=row.get("git_dirty"),
            git_committed_at=row.get("git_committed_at"),
            tree_sha=row.get("tree_sha"),
            manifest_sha256=row.get("manifest_sha256"),
            fingerprint=str(row.get("fingerprint") or "0" * 64),
            verified_by=str(row.get("verified_by") or "local"),
            verified_at=row.get("verified_at"),
            file_count=max(0, int(row.get("file_count") or 0)),
            total_bytes=max(0, int(row.get("total_bytes") or 0)),
            status=str(row.get("status") or "unknown"),
            locator=dict(row.get("locator") or {}),
            created_at=row["created_at"],
            completed_at=row.get("completed_at"),
        )

    def upsert_repository(
        self,
        *,
        repository_id: str,
        tenant_id: str,
        project_id: str,
        source_type: str,
        repository_url: str | None,
        default_branch: str | None,
        provider_repository_id: str | None = None,
    ) -> RepositoryRecord:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO projects (
                        project_id, display_name, embedding_model, index_version, index_status
                    ) VALUES (%s, %s, %s, %s, 'not_indexed')
                    ON CONFLICT (project_id) DO NOTHING
                    """,
                    (
                        project_id,
                        project_id.rsplit("/", 1)[-1],
                        self._settings.embedding_model,
                        self._settings.index_version,
                    ),
                )
                row = connection.execute(
                    """
                    INSERT INTO repository_sources (
                        source_id, project_id, source_type, root_relative_path,
                        repository_url, default_branch, enabled,
                        tenant_id, provider_repository_id
                    ) VALUES (%s, %s, %s, '', %s, %s, TRUE, %s, %s)
                    ON CONFLICT (source_id) DO UPDATE SET
                        project_id=EXCLUDED.project_id,
                        source_type=EXCLUDED.source_type,
                        repository_url=EXCLUDED.repository_url,
                        default_branch=EXCLUDED.default_branch,
                        tenant_id=EXCLUDED.tenant_id,
                        provider_repository_id=EXCLUDED.provider_repository_id,
                        updated_at=NOW()
                    RETURNING *
                    """,
                    (
                        repository_id,
                        project_id,
                        source_type,
                        repository_url,
                        default_branch,
                        tenant_id,
                        provider_repository_id,
                    ),
                ).fetchone()
            if row is None:
                raise SnapshotRepositoryError("Repository registration returned no row")
            return self._repository_record(row)
        except SnapshotRepositoryError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise SnapshotRepositoryError("Snapshot repository registration failed") from exc

    def get_repository(self, repository_id: str) -> RepositoryRecord | None:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM repository_sources WHERE source_id=%s",
                    (repository_id,),
                ).fetchone()
            return self._repository_record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise SnapshotRepositoryError("Snapshot repository lookup failed") from exc

    def list_repositories(self, *, limit: int = 100) -> list[RepositoryRecord]:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM repository_sources
                    ORDER BY created_at DESC, source_id
                    LIMIT %s
                    """,
                    (max(1, min(limit, 500)),),
                ).fetchall()
            return [self._repository_record(row) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise SnapshotRepositoryError("Snapshot repository list failed") from exc

    def register_snapshot(
        self,
        *,
        snapshot_id: str,
        tenant_id: str,
        repository_id: str,
        project_id: str,
        snapshot_kind: str,
        revision: str | None,
        branch: str | None,
        dirty: bool | None,
        committed_at: datetime | None,
        tree_sha: str | None,
        manifest_sha256: str | None,
        fingerprint: str,
        verified_by: str,
        locator: dict[str, Any] | None = None,
        status: str = "captured",
        file_count: int = 0,
        total_bytes: int = 0,
    ) -> tuple[SnapshotRecord, bool]:
        self.ensure_schema()
        verified_at = datetime.now(timezone.utc)
        try:
            with self._connect() as connection:
                if snapshot_kind == "upload":
                    existing = connection.execute(
                        "SELECT * FROM project_snapshots WHERE snapshot_id=%s LIMIT 1",
                        (snapshot_id,),
                    ).fetchone()
                else:
                    existing = connection.execute(
                        "SELECT * FROM project_snapshots WHERE snapshot_id=%s OR fingerprint=%s LIMIT 1",
                        (snapshot_id, fingerprint),
                    ).fetchone()
                if existing is not None:
                    if existing.get("fingerprint") not in {None, fingerprint}:
                        raise SnapshotIntegrityError("Snapshot ID is already bound to a different fingerprint")
                    if str(existing["source_id"]) != repository_id:
                        raise SnapshotIntegrityError("Snapshot fingerprint is already bound to a different repository")
                    if existing.get("fingerprint") is None:
                        existing = connection.execute(
                            """
                            UPDATE project_snapshots
                            SET tenant_id=%s, snapshot_kind=%s, tree_sha=%s,
                                fingerprint=%s, verified_by=%s, verified_at=%s,
                                locator=%s
                            WHERE snapshot_id=%s
                            RETURNING *
                            """,
                            (
                                tenant_id, snapshot_kind, tree_sha, fingerprint,
                                verified_by, verified_at, Jsonb(locator or {}),
                                existing["snapshot_id"],
                            ),
                        ).fetchone()
                    return self._snapshot_record(existing), True
                row = connection.execute(
                    """
                    INSERT INTO project_snapshots (
                        snapshot_id, project_id, source_id, revision, git_branch,
                        git_dirty, git_committed_at, manifest_sha256, file_count,
                        total_bytes, status, tenant_id, snapshot_kind, tree_sha,
                        fingerprint, verified_by, verified_at, locator
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    RETURNING *
                    """,
                    (
                        snapshot_id,
                        project_id,
                        repository_id,
                        revision,
                        branch,
                        dirty,
                        committed_at,
                        manifest_sha256,
                        max(0, file_count),
                        max(0, total_bytes),
                        status,
                        tenant_id,
                        snapshot_kind,
                        tree_sha,
                        fingerprint,
                        verified_by,
                        verified_at,
                        Jsonb(locator or {}),
                    ),
                ).fetchone()
            if row is None:
                raise SnapshotRepositoryError("Snapshot registration returned no row")
            return self._snapshot_record(row), False
        except SnapshotRepositoryError:
            raise
        except psycopg.errors.UniqueViolation as exc:
            raise SnapshotIntegrityError("Snapshot identity conflicts with an existing record") from exc
        except (psycopg.Error, OSError) as exc:
            raise SnapshotRepositoryError("Snapshot registration failed") from exc

    def get_snapshot(self, snapshot_id: str) -> SnapshotRecord | None:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM project_snapshots WHERE snapshot_id=%s",
                    (snapshot_id,),
                ).fetchone()
            return self._snapshot_record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise SnapshotRepositoryError("Snapshot lookup failed") from exc

    def list_snapshots(self, *, limit: int = 100) -> list[SnapshotRecord]:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM project_snapshots
                    ORDER BY created_at DESC, snapshot_id
                    LIMIT %s
                    """,
                    (max(1, min(limit, 500)),),
                ).fetchall()
            return [self._snapshot_record(row) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise SnapshotRepositoryError("Snapshot list failed") from exc

