from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ...schema_guard import SchemaStateError, require_schema
from .contracts import (
    ExternalProjectCatalogRecord,
    ProjectExternalBindingRecord,
    RagTargetRecord,
)


class ExternalProjectRegistryError(RuntimeError):
    pass


class PostgresSettingsLike(Protocol):
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_connect_timeout_seconds: int


class PostgresExternalProjectRegistry:
    """PostgreSQL authority for external RAG targets, observations and identity bindings.

    Mutable index state belongs to ``external_project_catalog``. The binding table
    intentionally stores only the logical project identity relationship and its
    verification provenance; current Snapshot/revision state is never copied into it.
    """

    def __init__(self, settings: PostgresSettingsLike) -> None:
        self._settings = settings
        self._schema_checked = False

    @property
    def configured(self) -> bool:
        return bool(self._settings.postgres_password)

    def _connect(self):
        if not self.configured:
            raise ExternalProjectRegistryError(
                "PostgreSQL external project registry is not configured"
            )
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
        if self._schema_checked:
            return
        try:
            with self._connect() as connection:
                require_schema(connection)
            self._schema_checked = True
        except (psycopg.Error, OSError, SchemaStateError) as exc:
            raise ExternalProjectRegistryError(
                "PostgreSQL schema is not on the required external-project baseline"
            ) from exc

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def upsert_target(
        self,
        *,
        target_id: str,
        name: str,
        base_url: str,
        enabled: bool = True,
        availability: str = "unknown",
        error: str | None = None,
    ) -> RagTargetRecord:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO rag_targets (
                        target_id, name, base_url, enabled, availability,
                        last_seen_at, error
                    ) VALUES (%s, %s, %s, %s, %s,
                        CASE WHEN %s = 'online' THEN NOW() ELSE NULL END, %s)
                    ON CONFLICT (target_id) DO UPDATE SET
                        name=EXCLUDED.name,
                        base_url=EXCLUDED.base_url,
                        enabled=EXCLUDED.enabled,
                        availability=EXCLUDED.availability,
                        last_seen_at=CASE
                            WHEN EXCLUDED.availability='online' THEN NOW()
                            ELSE rag_targets.last_seen_at
                        END,
                        error=EXCLUDED.error,
                        updated_at=NOW()
                    RETURNING *
                    """,
                    (
                        target_id,
                        name,
                        base_url.rstrip("/"),
                        enabled,
                        availability,
                        availability,
                        error,
                    ),
                ).fetchone()
            return RagTargetRecord.model_validate(row)
        except (psycopg.Error, OSError) as exc:
            raise ExternalProjectRegistryError("RAG target write failed") from exc

    def mark_target_offline(self, target_id: str, error: str) -> None:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE rag_targets
                    SET availability='offline', error=%s, updated_at=NOW()
                    WHERE target_id=%s
                    """,
                    (error[:2000], target_id),
                )
                connection.execute(
                    """
                    UPDATE external_project_catalog
                    SET availability='stale', updated_at=NOW()
                    WHERE target_id=%s AND availability <> 'stale'
                    """,
                    (target_id,),
                )
        except (psycopg.Error, OSError) as exc:
            raise ExternalProjectRegistryError("RAG target offline update failed") from exc

    def begin_observation(self, target_id: str) -> None:
        """Mark prior observations stale before one atomic-style refresh pass."""
        self.ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE external_project_catalog
                    SET availability='stale', updated_at=NOW()
                    WHERE target_id=%s
                    """,
                    (target_id,),
                )
        except (psycopg.Error, OSError) as exc:
            raise ExternalProjectRegistryError("External catalog refresh start failed") from exc

    def upsert_external_project(
        self,
        *,
        target_id: str,
        value: dict[str, Any],
    ) -> ExternalProjectCatalogRecord:
        self.ensure_schema()
        external_project_id = str(value.get("project_id") or "").strip()
        if not external_project_id:
            raise ExternalProjectRegistryError(
                "RAG Lab project response contains no project_id"
            )
        revision = str(value.get("commit") or value.get("revision") or "").strip() or None
        name = str(value.get("name") or external_project_id.rsplit("/", 1)[-1]).strip()
        state = str(value.get("state") or "done").strip() or None
        chunk_count = _optional_non_negative_int(value.get("chunk_count"))
        actual_chunks = _optional_non_negative_int(value.get("actual_chunks"))
        indexed_at = _optional_datetime(value.get("indexed_at"))
        fingerprint = value.get("fingerprint") if isinstance(value.get("fingerprint"), dict) else {}
        dirty = value.get("dirty") if isinstance(value.get("dirty"), bool) else None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO external_project_catalog (
                        target_id, external_project_id, name, state, revision,
                        dirty, chunk_count, actual_chunks, indexed_at, fingerprint,
                        availability, last_seen_at, raw_metadata
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'online', NOW(), %s
                    )
                    ON CONFLICT (target_id, external_project_id) DO UPDATE SET
                        name=EXCLUDED.name,
                        state=EXCLUDED.state,
                        revision=EXCLUDED.revision,
                        dirty=EXCLUDED.dirty,
                        chunk_count=EXCLUDED.chunk_count,
                        actual_chunks=EXCLUDED.actual_chunks,
                        indexed_at=EXCLUDED.indexed_at,
                        fingerprint=EXCLUDED.fingerprint,
                        availability='online',
                        last_seen_at=NOW(),
                        raw_metadata=EXCLUDED.raw_metadata,
                        updated_at=NOW()
                    RETURNING target_id, external_project_id, name, state, revision,
                              dirty, chunk_count, actual_chunks, indexed_at, fingerprint,
                              availability, last_seen_at, raw_metadata
                    """,
                    (
                        target_id,
                        external_project_id,
                        name,
                        state,
                        revision,
                        dirty,
                        chunk_count,
                        actual_chunks,
                        indexed_at,
                        Jsonb(fingerprint),
                        Jsonb(value),
                    ),
                ).fetchone()
            return ExternalProjectCatalogRecord.model_validate(row)
        except (psycopg.Error, OSError) as exc:
            raise ExternalProjectRegistryError("External project catalog write failed") from exc

    def list_external_projects(self, target_id: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT * FROM external_project_catalog
                    WHERE target_id=%s
                    ORDER BY LOWER(external_project_id), external_project_id
                    """,
                    (target_id,),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise ExternalProjectRegistryError("External project catalog read failed") from exc

    def list_vision_projects(self) -> list[dict[str, Any]]:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT p.project_id, p.current_snapshot_id,
                           p.git_commit_sha, p.git_branch, p.git_dirty,
                           ps.revision AS snapshot_revision
                    FROM projects p
                    LEFT JOIN project_snapshots ps
                      ON ps.snapshot_id = p.current_snapshot_id
                    ORDER BY LOWER(p.project_id), p.project_id
                    """
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise ExternalProjectRegistryError("Vision project list failed") from exc

    def list_bindings(self, target_id: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT * FROM project_external_bindings
                    WHERE target_id=%s
                    ORDER BY LOWER(project_id), project_id
                    """,
                    (target_id,),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise ExternalProjectRegistryError("External project bindings read failed") from exc

    def upsert_binding(
        self,
        *,
        project_id: str,
        target_id: str,
        external_project_id: str,
        binding_method: str,
        binding_strength: str,
        verification_state: str,
        error: str | None = None,
        preserve_manual: bool = True,
    ) -> ProjectExternalBindingRecord:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT * FROM project_external_bindings
                    WHERE project_id=%s AND target_id=%s
                    FOR UPDATE
                    """,
                    (project_id, target_id),
                ).fetchone()
                if preserve_manual and existing and existing.get("binding_method") == "manual":
                    return ProjectExternalBindingRecord.model_validate(existing)
                row = connection.execute(
                    """
                    INSERT INTO project_external_bindings (
                        project_id, target_id, external_project_id, binding_method,
                        binding_strength, verification_state, last_verified_at, error
                    ) VALUES (%s, %s, %s, %s, %s, %s,
                        CASE WHEN %s='verified' THEN NOW() ELSE NULL END, %s)
                    ON CONFLICT (project_id, target_id) DO UPDATE SET
                        external_project_id=EXCLUDED.external_project_id,
                        binding_method=EXCLUDED.binding_method,
                        binding_strength=EXCLUDED.binding_strength,
                        verification_state=EXCLUDED.verification_state,
                        last_verified_at=CASE
                            WHEN EXCLUDED.verification_state='verified' THEN NOW()
                            ELSE project_external_bindings.last_verified_at
                        END,
                        error=EXCLUDED.error,
                        updated_at=NOW()
                    RETURNING *
                    """,
                    (
                        project_id,
                        target_id,
                        external_project_id,
                        binding_method,
                        binding_strength,
                        verification_state,
                        verification_state,
                        error,
                    ),
                ).fetchone()
            return ProjectExternalBindingRecord.model_validate(row)
        except (psycopg.Error, OSError) as exc:
            raise ExternalProjectRegistryError("External project binding write failed") from exc


def _optional_non_negative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
