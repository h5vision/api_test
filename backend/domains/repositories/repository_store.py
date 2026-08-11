from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings
from .schema_guard import SchemaStateError, require_schema
from .project_snapshots.contracts import canonical_snapshot_kind, snapshot_fingerprint


class RepositoryStoreError(RuntimeError):
    pass


class PostgresRepositoryStore:
    """Durable registry for repository sources, snapshots and index generations."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._initialize_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._settings.postgres_password)

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(
            host=self._settings.postgres_host,
            port=self._settings.postgres_port,
            dbname=self._settings.postgres_db,
            user=self._settings.postgres_user,
            password=self._settings.postgres_password,
            connect_timeout=self._settings.postgres_connect_timeout_seconds,
            row_factory=dict_row,
        )

    def _ensure_schema(self) -> None:
        if not self.configured:
            raise RepositoryStoreError("PostgreSQL repository store is not configured")
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            try:
                with self._connect() as connection:
                    require_schema(connection)
                self._initialized = True
            except (psycopg.Error, OSError, SchemaStateError) as exc:
                raise RepositoryStoreError(
                    "PostgreSQL schema is not on the required Alembic baseline"
                ) from exc

    def upsert_source(self, values: dict[str, Any]) -> dict[str, Any]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    INSERT INTO repository_sources (
                        source_id, project_id, source_type, root_relative_path,
                        repository_url, default_branch, enabled, tenant_id,
                        provider_repository_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_id) DO UPDATE SET
                        project_id = EXCLUDED.project_id,
                        source_type = EXCLUDED.source_type,
                        root_relative_path = EXCLUDED.root_relative_path,
                        repository_url = EXCLUDED.repository_url,
                        default_branch = EXCLUDED.default_branch,
                        enabled = EXCLUDED.enabled,
                        tenant_id = EXCLUDED.tenant_id,
                        provider_repository_id = COALESCE(
                            EXCLUDED.provider_repository_id,
                            repository_sources.provider_repository_id
                        ),
                        updated_at = NOW()
                    RETURNING *
                    """,
                    (
                        values["source_id"],
                        values["project_id"],
                        values["source_type"],
                        values["root_relative_path"],
                        values.get("repository_url"),
                        values.get("default_branch"),
                        values.get("enabled", True),
                        values.get("tenant_id", self._settings.snapshot_tenant_id),
                        values.get("provider_repository_id"),
                    ),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Repository source write failed") from exc

    def list_sources(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT * FROM repository_sources
                    ORDER BY LOWER(project_id), source_id
                    """
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Repository source list failed") from exc

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    "SELECT * FROM repository_sources WHERE source_id = %s",
                    (source_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Repository source lookup failed") from exc

    def create_job(self, job_id: str, source: dict[str, Any], force: bool) -> dict[str, Any]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE repository_index_jobs
                    SET status = 'failed',
                        stage = 'stalled',
                        error = COALESCE(
                            error,
                            'Indexing worker heartbeat expired before completion'
                        ),
                        completed_at = NOW(),
                        updated_at = NOW()
                    WHERE source_id = %s
                      AND status NOT IN ('completed', 'failed', 'paused')
                      AND updated_at < NOW() - INTERVAL '15 minutes'
                    """,
                    (source["source_id"],),
                )
                active = connection.execute(
                    """
                    SELECT job_id FROM repository_index_jobs
                    WHERE source_id = %s
                      AND status NOT IN ('completed', 'failed')
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (source["source_id"],),
                ).fetchone()
                if active:
                    raise RepositoryStoreError(
                        f"Source already has active indexing job: {active['job_id']}"
                    )
                return connection.execute(
                    """
                    INSERT INTO repository_index_jobs (
                        job_id, source_id, project_id, status, stage, force_run
                    ) VALUES (%s, %s, %s, 'queued', 'queued', %s)
                    RETURNING *
                    """,
                    (job_id, source["source_id"], source["project_id"], force),
                ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise RepositoryStoreError(
                f"Source already has active indexing job: {source['source_id']}"
            ) from exc
        except RepositoryStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Repository indexing job creation failed") from exc

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    "SELECT * FROM repository_index_jobs WHERE job_id = %s",
                    (job_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Repository indexing job lookup failed") from exc

    def list_jobs(
        self,
        *,
        project_id: str | None = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        clauses: list[str] = []
        parameters: list[Any] = []
        if project_id:
            clauses.append("project_id = %s")
            parameters.append(project_id)
        if active_only:
            clauses.append("status NOT IN ('completed', 'failed')")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        try:
            with self._connect() as connection:
                return connection.execute(
                    f"""
                    SELECT * FROM repository_index_jobs
                    {where}
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT %s
                    """,
                    tuple(parameters),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Repository indexing job list failed") from exc

    def update_job(self, job_id: str, **values: Any) -> None:
        self._ensure_schema()
        allowed = {
            "snapshot_id",
            "generation_id",
            "status",
            "stage",
            "files_total",
            "files_processed",
            "chunks_stored",
            "bytes_total",
            "error",
            "completed_at",
        }
        assignments: list[str] = []
        parameters: list[Any] = []
        for key, value in values.items():
            if key not in allowed:
                raise RepositoryStoreError(f"Unsupported job field: {key}")
            assignments.append(f"{key} = %s")
            parameters.append(value)
        if not assignments:
            return
        parameters.append(job_id)
        try:
            with self._connect() as connection:
                connection.execute(
                    f"""
                    UPDATE repository_index_jobs
                    SET {", ".join(assignments)}, updated_at = NOW()
                    WHERE job_id = %s
                    """,
                    tuple(parameters),
                )
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Repository indexing job update failed") from exc

    def prepare_job_resume(self, job_id: str) -> dict[str, Any]:
        """Atomically claim an interrupted job for a single resume attempt."""
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM repository_index_jobs
                    WHERE job_id = %s
                    FOR UPDATE
                    """,
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise RepositoryStoreError("Repository indexing job was not found")
                if row["status"] == "completed":
                    raise RepositoryStoreError("Completed indexing job cannot be resumed")
                if not row.get("snapshot_id") or not row.get("generation_id"):
                    raise RepositoryStoreError(
                        "Indexing job has no durable snapshot checkpoint"
                    )
                if (
                    row["status"] not in {"paused", "failed"}
                    and row["updated_at"]
                    >= datetime.now(row["updated_at"].tzinfo)
                    - timedelta(minutes=1)
                ):
                    raise RepositoryStoreError(
                        "Indexing job still appears to be running"
                    )
                resumed = connection.execute(
                    """
                    UPDATE repository_index_jobs
                    SET status = 'queued',
                        stage = 'resume_queued',
                        error = NULL,
                        completed_at = NULL,
                        updated_at = NOW()
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (job_id,),
                ).fetchone()
                connection.execute(
                    """
                    UPDATE project_snapshots
                    SET status = 'building', completed_at = NULL
                    WHERE snapshot_id = %s
                    """,
                    (row["snapshot_id"],),
                )
                connection.execute(
                    """
                    UPDATE index_generations
                    SET status = 'building', error = NULL
                    WHERE generation_id = %s
                    """,
                    (row["generation_id"],),
                )
                connection.execute(
                    """
                    UPDATE projects
                    SET index_status = 'indexing', updated_at = NOW()
                    WHERE project_id = %s
                    """,
                    (row["project_id"],),
                )
                return resumed
        except RepositoryStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError(
                "Repository indexing job resume claim failed"
            ) from exc

    def begin_snapshot(
        self,
        *,
        source: dict[str, Any],
        snapshot_id: str,
        generation_id: str,
        revision: str | None,
        branch: str | None,
        dirty: bool | None,
        committed_at: datetime | None,
        manifest_sha256: str,
        entries: list[dict[str, Any]],
        total_bytes: int,
    ) -> None:
        self._ensure_schema()
        project_id = source["project_id"]
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO projects (
                        project_id, display_name, embedding_model,
                        embedding_model_id, index_version, index_status
                    ) VALUES (%s, %s, %s, %s, %s, 'indexing')
                    ON CONFLICT (project_id) DO UPDATE SET
                        embedding_model = EXCLUDED.embedding_model,
                        embedding_model_id = EXCLUDED.embedding_model_id,
                        index_version = EXCLUDED.index_version,
                        index_status = 'indexing',
                        updated_at = NOW()
                    """,
                    (
                        project_id,
                        project_id.rsplit("/", 1)[-1],
                        self._settings.embedding_model,
                        self._settings.embedding_model_id,
                        self._settings.index_version,
                    ),
                )
                snapshot_kind = canonical_snapshot_kind(revision, dirty)
                fingerprint = snapshot_fingerprint(
                    tenant_id=str(
                        source.get("tenant_id") or self._settings.snapshot_tenant_id
                    ),
                    repository_id=str(source["source_id"]),
                    snapshot_kind=snapshot_kind,
                    revision=revision,
                    manifest_sha256=manifest_sha256,
                )
                existing_snapshot = connection.execute(
                    """
                    SELECT snapshot_id, source_id, fingerprint
                    FROM project_snapshots
                    WHERE snapshot_id = %s OR fingerprint = %s
                    LIMIT 1
                    """,
                    (snapshot_id, fingerprint),
                ).fetchone()
                if existing_snapshot is not None:
                    if str(existing_snapshot["source_id"]) != str(source["source_id"]):
                        raise RepositoryStoreError(
                            "Snapshot identity is already bound to another repository source"
                        )
                    if (
                        existing_snapshot.get("fingerprint")
                        and existing_snapshot["fingerprint"] != fingerprint
                    ):
                        raise RepositoryStoreError(
                            "Snapshot ID is already bound to a different fingerprint"
                        )
                    snapshot_id = str(existing_snapshot["snapshot_id"])
                    connection.execute(
                        """
                        UPDATE project_snapshots
                        SET status='building', completed_at=NULL,
                            revision=%s, git_branch=%s, git_dirty=%s,
                            git_committed_at=%s, manifest_sha256=%s,
                            file_count=%s, total_bytes=%s,
                            tenant_id=%s, snapshot_kind=%s, fingerprint=%s,
                            verified_by='local', verified_at=NOW(),
                            locator=%s
                        WHERE snapshot_id=%s
                        """,
                        (
                            revision, branch, dirty, committed_at, manifest_sha256,
                            sum(entry["entry_type"] == "file" for entry in entries),
                            total_bytes,
                            str(source.get("tenant_id") or self._settings.snapshot_tenant_id),
                            snapshot_kind,
                            fingerprint,
                            Jsonb({
                                "source_type": source.get("source_type"),
                                "root_relative_path": source.get("root_relative_path"),
                                "repository_url": source.get("repository_url"),
                            }),
                            snapshot_id,
                        ),
                    )
                    connection.execute(
                        "DELETE FROM snapshot_entries WHERE snapshot_id=%s",
                        (snapshot_id,),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO project_snapshots (
                            snapshot_id, project_id, source_id, revision, git_branch,
                            git_dirty, git_committed_at, manifest_sha256, file_count,
                            total_bytes, status, tenant_id, snapshot_kind, fingerprint,
                            verified_by, verified_at, locator
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'building',
                            %s, %s, %s, 'local', NOW(), %s
                        )
                        """,
                        (
                            snapshot_id,
                            project_id,
                            source["source_id"],
                            revision,
                            branch,
                            dirty,
                            committed_at,
                            manifest_sha256,
                            sum(entry["entry_type"] == "file" for entry in entries),
                            total_bytes,
                            str(source.get("tenant_id") or self._settings.snapshot_tenant_id),
                            snapshot_kind,
                            fingerprint,
                            Jsonb({
                                "source_type": source.get("source_type"),
                                "root_relative_path": source.get("root_relative_path"),
                                "repository_url": source.get("repository_url"),
                            }),
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO index_generations (
                        generation_id, project_id, snapshot_id, collection_name,
                        embedding_model, embedding_model_id, index_version, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'building')
                    """,
                    (
                        generation_id,
                        project_id,
                        snapshot_id,
                        self._settings.qdrant_collection,
                        self._settings.embedding_model,
                        self._settings.embedding_model_id,
                        self._settings.index_version,
                    ),
                )
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO snapshot_entries (
                            snapshot_id, relative_path, name, entry_type, language,
                            size_bytes, content_sha256, content, indexable, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                snapshot_id,
                                entry["relative_path"],
                                entry["name"],
                                entry["entry_type"],
                                entry.get("language"),
                                entry.get("size_bytes", 0),
                                entry.get("content_sha256"),
                                entry.get("content"),
                                entry.get("indexable", False),
                                Jsonb(entry.get("metadata", {})),
                            )
                            for entry in entries
                        ],
                    )
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Repository snapshot creation failed") from exc

    def bind_generation_vector_index(
        self,
        generation_id: str,
        vector_index_id: str,
    ) -> None:
        """Persist the P2-F provenance edge without allowing silent rebinding."""
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE index_generations
                    SET vector_index_id = %s
                    WHERE generation_id = %s
                      AND (vector_index_id IS NULL OR vector_index_id = %s)
                    RETURNING generation_id, vector_index_id
                    """,
                    (vector_index_id, generation_id, vector_index_id),
                ).fetchone()
                if row is None:
                    existing = connection.execute(
                        """
                        SELECT vector_index_id
                        FROM index_generations
                        WHERE generation_id = %s
                        """,
                        (generation_id,),
                    ).fetchone()
                    if existing is None:
                        raise RepositoryStoreError(
                            "Index generation does not exist for VectorIndex binding"
                        )
                    raise RepositoryStoreError(
                        "Index generation is already bound to a different VectorIndex"
                    )
        except RepositoryStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError(
                "Index generation VectorIndex binding failed"
            ) from exc

    def append_generation_chunks(
        self, generation_id: str, records: Iterable[dict[str, Any]]
    ) -> int:
        rows = list(records)
        if not rows:
            return 0
        self._ensure_schema()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO generation_chunks (
                            generation_id, chunk_id, document_id, relative_path,
                            external_point_id, content_sha256, content, line_start,
                            line_end, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (generation_id, chunk_id) DO UPDATE SET
                            document_id = EXCLUDED.document_id,
                            relative_path = EXCLUDED.relative_path,
                            external_point_id = EXCLUDED.external_point_id,
                            content_sha256 = EXCLUDED.content_sha256,
                            content = EXCLUDED.content,
                            line_start = EXCLUDED.line_start,
                            line_end = EXCLUDED.line_end,
                            metadata = EXCLUDED.metadata
                        """,
                        [
                            (
                                generation_id,
                                row["chunk_id"],
                                row["document_id"],
                                row["relative_path"],
                                row["external_point_id"],
                                row["content_sha256"],
                                row["content"],
                                row.get("line_start"),
                                row.get("line_end"),
                                Jsonb(row.get("metadata", {})),
                            )
                            for row in rows
                        ],
                    )
            return len(rows)
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Generation chunk mapping write failed") from exc

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT * FROM project_snapshots
                    WHERE snapshot_id = %s
                    """,
                    (snapshot_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Repository snapshot lookup failed") from exc

    def find_snapshots(
        self,
        *,
        project_id: str | None = None,
        revision: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Find immutable Snapshot identities without selecting an active index."""

        self._ensure_schema()
        clauses: list[str] = []
        parameters: list[Any] = []
        if project_id:
            clauses.append("project_id = %s")
            parameters.append(project_id)
        if revision:
            clauses.append("LOWER(revision) = LOWER(%s)")
            parameters.append(revision)
        where = " AND ".join(clauses) if clauses else "TRUE"
        parameters.append(max(1, min(int(limit), 100)))
        try:
            with self._connect() as connection:
                return connection.execute(
                    f"""
                    SELECT snapshot_id, project_id, revision, manifest_sha256,
                           status, created_at, completed_at
                    FROM project_snapshots
                    WHERE {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    tuple(parameters),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Repository Snapshot search failed") from exc

    def get_generation(self, generation_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT * FROM index_generations
                    WHERE generation_id = %s
                    """,
                    (generation_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Index generation lookup failed") from exc

    def list_snapshot_indexable_entries(
        self, snapshot_id: str
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT relative_path, name, entry_type, language, size_bytes,
                           content_sha256, content, indexable, metadata
                    FROM snapshot_entries
                    WHERE snapshot_id = %s
                      AND indexable = TRUE
                      AND content IS NOT NULL
                    ORDER BY relative_path
                    """,
                    (snapshot_id,),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError(
                "Repository snapshot entry lookup failed"
            ) from exc

    def prepare_generation_import(
        self,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
    ) -> None:
        """Reopen an idempotent offline generation before shard import."""
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE project_snapshots
                    SET status = 'building', completed_at = NULL
                    WHERE snapshot_id = %s AND project_id = %s
                    """,
                    (snapshot_id, project_id),
                )
                connection.execute(
                    """
                    UPDATE index_generations
                    SET status = 'building', error = NULL, activated_at = NULL, ready_at = NULL
                    WHERE generation_id = %s
                      AND snapshot_id = %s
                      AND project_id = %s
                    """,
                    (generation_id, snapshot_id, project_id),
                )
                connection.execute(
                    """
                    UPDATE projects
                    SET index_status = 'indexing', updated_at = NOW()
                    WHERE project_id = %s
                    """,
                    (project_id,),
                )
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError(
                "Offline generation preparation failed"
            ) from exc

    def complete_generation(
        self,
        *,
        source_id: str,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        revision: str | None,
        branch: str | None,
        dirty: bool | None,
        committed_at: datetime | None,
        manifest_sha256: str,
        file_count: int,
        chunk_count: int,
    ) -> str:
        """Complete a managed build without selecting it as the project route.

        P2-I separates build readiness from routing. This transaction proves the
        managed artifact is usable (Snapshot completed, VectorIndex ready, Binding
        verified, Generation ready). ProjectVectorRoute promotion happens separately.
        """
        self._ensure_schema()
        try:
            with self._connect() as connection:
                provenance = connection.execute(
                    """
                    SELECT vector_index_id
                    FROM index_generations
                    WHERE generation_id = %s
                      AND project_id = %s
                      AND snapshot_id = %s
                    """,
                    (generation_id, project_id, snapshot_id),
                ).fetchone()
                if provenance is None or not provenance.get("vector_index_id"):
                    raise RepositoryStoreError(
                        "P2-F requires generation.vector_index_id before build completion"
                    )
                binding = connection.execute(
                    """
                    SELECT binding_id
                    FROM snapshot_vector_bindings
                    WHERE snapshot_id = %s
                      AND vector_index_id = %s
                      AND generation_id = %s
                      AND binding_source = 'managed_generation'
                      AND verification_state IN ('pending', 'verified')
                    """,
                    (snapshot_id, provenance["vector_index_id"], generation_id),
                ).fetchone()
                if binding is None:
                    raise RepositoryStoreError(
                        "P2-G requires SnapshotVectorBinding before build completion"
                    )
                connection.execute(
                    """
                    UPDATE vector_indexes
                    SET status = 'ready', updated_at = NOW()
                    WHERE vector_index_id = %s
                      AND ownership_mode = 'vision_managed'
                      AND status IN ('building', 'ready')
                    """,
                    (provenance["vector_index_id"],),
                )
                connection.execute(
                    """
                    UPDATE project_snapshots
                    SET status = 'completed', completed_at = COALESCE(completed_at, NOW())
                    WHERE snapshot_id = %s
                    """,
                    (snapshot_id,),
                )
                connection.execute(
                    """
                    UPDATE index_generations
                    SET status = 'ready', file_count = %s, chunk_count = %s,
                        ready_at = NOW(), error = NULL
                    WHERE generation_id = %s
                    """,
                    (file_count, chunk_count, generation_id),
                )
                connection.execute(
                    """
                    UPDATE snapshot_vector_bindings
                    SET verification_state = 'verified', verified_at = COALESCE(verified_at, NOW()),
                        error = NULL, updated_at = NOW()
                    WHERE binding_id = %s
                    """,
                    (binding["binding_id"],),
                )
                # current_snapshot_id describes repository freshness, not retrieval routing.
                # projects.active_generation_id is intentionally left untouched as a legacy column.
                connection.execute(
                    """
                    UPDATE projects
                    SET current_snapshot_id = %s,
                        manifest_sha256 = %s,
                        git_commit_sha = %s,
                        git_branch = %s,
                        git_dirty = %s,
                        git_committed_at = %s,
                        embedding_model = %s,
                        embedding_model_id = %s,
                        index_version = %s,
                        index_status = 'ready',
                        index_completed_at = NOW(),
                        updated_at = NOW()
                    WHERE project_id = %s
                    """,
                    (
                        snapshot_id, manifest_sha256, revision, branch, dirty,
                        committed_at, self._settings.embedding_model,
                        self._settings.embedding_model_id, self._settings.index_version,
                        project_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE repository_sources
                    SET last_revision = %s, last_synced_at = NOW(), updated_at = NOW()
                    WHERE source_id = %s
                    """,
                    (revision, source_id),
                )
                return str(binding["binding_id"])
        except RepositoryStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Index generation completion failed") from exc

    def fail_generation(
        self,
        project_id: str,
        snapshot_id: str | None,
        generation_id: str | None,
        error: str,
    ) -> None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                if snapshot_id:
                    connection.execute(
                        "UPDATE project_snapshots SET status = 'failed' WHERE snapshot_id = %s",
                        (snapshot_id,),
                    )
                if generation_id:
                    connection.execute(
                        """
                        UPDATE index_generations
                        SET status = 'failed', error = %s
                        WHERE generation_id = %s
                        """,
                        (error, generation_id),
                    )
                    connection.execute(
                        """
                        UPDATE snapshot_vector_bindings
                        SET verification_state = 'failed', error = %s, updated_at = NOW()
                        WHERE generation_id = %s
                          AND binding_source = 'managed_generation'
                          AND verification_state <> 'verified'
                        """,
                        (error, generation_id),
                    )
                connection.execute(
                    """
                    UPDATE projects
                    SET index_status = CASE
                            WHEN EXISTS (
                                SELECT 1 FROM project_vector_routes pvr
                                WHERE pvr.project_id = projects.project_id
                                  AND pvr.active_binding_id IS NOT NULL
                            ) THEN 'stale'
                            ELSE 'failed'
                        END,
                        updated_at = NOW()
                    WHERE project_id = %s
                    """,
                    (project_id,),
                )
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Failed generation status update failed") from exc

    def pause_generation(
        self,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        error: str,
    ) -> None:
        """Keep a partial generation durable while its embedding service is down."""
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE project_snapshots
                    SET status = 'paused'
                    WHERE snapshot_id = %s
                    """,
                    (snapshot_id,),
                )
                connection.execute(
                    """
                    UPDATE index_generations
                    SET status = 'paused', error = %s
                    WHERE generation_id = %s
                    """,
                    (error, generation_id),
                )
                connection.execute(
                    """
                    UPDATE projects
                    SET index_status = 'paused', updated_at = NOW()
                    WHERE project_id = %s
                    """,
                    (project_id,),
                )
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Paused generation status update failed") from exc

    def get_active_generation(self, project_id: str) -> dict[str, Any] | None:
        """Compatibility projection derived from P2-I ProjectVectorRoute.

        Returns a row only when the active binding is managed and therefore has a
        generation_id. It never reads projects.active_generation_id.
        """
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT p.project_id, svb.snapshot_id, svb.generation_id,
                           svb.vector_index_id, s.revision, s.manifest_sha256
                    FROM project_vector_routes AS pvr
                    JOIN projects AS p ON p.project_id = pvr.project_id
                    JOIN snapshot_vector_bindings AS svb
                      ON svb.binding_id = pvr.active_binding_id
                    JOIN project_snapshots AS s ON s.snapshot_id = svb.snapshot_id
                    WHERE pvr.project_id = %s
                      AND pvr.active_binding_id IS NOT NULL
                      AND svb.generation_id IS NOT NULL
                      AND svb.verification_state = 'verified'
                    """,
                    (project_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Active managed generation projection failed") from exc

    def get_current_snapshot_context(self, project_id: str) -> dict[str, Any] | None:
        """Repository freshness projection independent from active retrieval route."""
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT p.project_id, p.current_snapshot_id AS snapshot_id,
                           s.revision, s.manifest_sha256,
                           (
                               SELECT ig.generation_id
                               FROM index_generations AS ig
                               WHERE ig.project_id = p.project_id
                                 AND ig.snapshot_id = p.current_snapshot_id
                                 AND ig.status = 'ready'
                               ORDER BY ig.ready_at DESC NULLS LAST, ig.created_at DESC
                               LIMIT 1
                           ) AS generation_id
                    FROM projects AS p
                    JOIN project_snapshots AS s ON s.snapshot_id = p.current_snapshot_id
                    WHERE p.project_id = %s AND p.current_snapshot_id IS NOT NULL
                    """,
                    (project_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Current project snapshot lookup failed") from exc

    def get_latest_ready_generation(self, project_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT ig.project_id, ig.snapshot_id, ig.generation_id, ig.vector_index_id,
                           s.revision, s.manifest_sha256
                    FROM index_generations AS ig
                    JOIN project_snapshots AS s ON s.snapshot_id = ig.snapshot_id
                    WHERE ig.project_id = %s AND ig.status = 'ready'
                    ORDER BY ig.ready_at DESC NULLS LAST, ig.created_at DESC
                    LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Latest ready generation lookup failed") from exc

    def list_tree(self, project_id: str, prefix: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
        active = self.get_current_snapshot_context(project_id)
        if active is None:
            raise RepositoryStoreError("Project has no current snapshot")
        normalized = prefix.replace("\\", "/").strip("/")
        pattern = f"{normalized}/%" if normalized else "%"
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT relative_path AS path, name, entry_type, language,
                           size_bytes, content_sha256, indexable
                    FROM snapshot_entries
                    WHERE snapshot_id = %s AND relative_path LIKE %s
                    ORDER BY relative_path
                    """,
                    (active["snapshot_id"], pattern),
                ).fetchall()
            return active, rows
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Project tree lookup failed") from exc

    def get_file(self, project_id: str, relative_path: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        active = self.get_current_snapshot_context(project_id)
        if active is None:
            raise RepositoryStoreError("Project has no current snapshot")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT relative_path AS path, language, size_bytes,
                           content_sha256, content
                    FROM snapshot_entries
                    WHERE snapshot_id = %s AND relative_path = %s
                      AND entry_type = 'file' AND content IS NOT NULL
                    """,
                    (active["snapshot_id"], relative_path),
                ).fetchone()
            return active, row
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Project file lookup failed") from exc

    def generation_chunk_count(self, generation_id: str) -> int:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM generation_chunks
                    WHERE generation_id = %s
                    """,
                    (generation_id,),
                ).fetchone()
            return int(row["count"])
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Generation chunk count failed") from exc

    def list_generation_chunk_ids(self, generation_id: str) -> set[str]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT chunk_id FROM generation_chunks
                    WHERE generation_id = %s
                    """,
                    (generation_id,),
                ).fetchall()
            return {str(row["chunk_id"]) for row in rows}
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Generation chunk checkpoint lookup failed") from exc
