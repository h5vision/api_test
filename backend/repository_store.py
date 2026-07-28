from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings


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
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS projects (
                            project_id TEXT PRIMARY KEY,
                            display_name TEXT NOT NULL,
                            current_snapshot_id TEXT,
                            manifest_sha256 TEXT,
                            git_commit_sha TEXT,
                            git_branch TEXT,
                            git_dirty BOOLEAN,
                            git_committed_at TIMESTAMPTZ,
                            source_modified_at TIMESTAMPTZ,
                            index_completed_at TIMESTAMPTZ,
                            embedding_model TEXT NOT NULL,
                            index_version TEXT NOT NULL,
                            index_status TEXT NOT NULL DEFAULT 'ready',
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    connection.execute(
                        """
                        ALTER TABLE projects
                        ADD COLUMN IF NOT EXISTS active_generation_id TEXT
                        """
                    )
                    connection.execute(
                        """
                        ALTER TABLE projects
                        ADD COLUMN IF NOT EXISTS embedding_model_id TEXT
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS repository_sources (
                            source_id TEXT PRIMARY KEY,
                            project_id TEXT NOT NULL,
                            source_type TEXT NOT NULL,
                            root_relative_path TEXT NOT NULL,
                            repository_url TEXT,
                            default_branch TEXT,
                            enabled BOOLEAN NOT NULL DEFAULT TRUE,
                            last_revision TEXT,
                            last_synced_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_repository_sources_project
                        ON repository_sources (project_id)
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS project_snapshots (
                            snapshot_id TEXT PRIMARY KEY,
                            project_id TEXT NOT NULL REFERENCES projects(project_id)
                                ON DELETE CASCADE,
                            source_id TEXT NOT NULL REFERENCES repository_sources(source_id)
                                ON DELETE RESTRICT,
                            revision TEXT,
                            git_branch TEXT,
                            git_dirty BOOLEAN,
                            git_committed_at TIMESTAMPTZ,
                            manifest_sha256 TEXT NOT NULL,
                            file_count INTEGER NOT NULL DEFAULT 0,
                            total_bytes BIGINT NOT NULL DEFAULT 0,
                            status TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            completed_at TIMESTAMPTZ
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_project_snapshots_project
                        ON project_snapshots (project_id, created_at DESC)
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS snapshot_entries (
                            snapshot_id TEXT NOT NULL REFERENCES project_snapshots(snapshot_id)
                                ON DELETE CASCADE,
                            relative_path TEXT NOT NULL,
                            name TEXT NOT NULL,
                            entry_type TEXT NOT NULL,
                            language TEXT,
                            size_bytes BIGINT NOT NULL DEFAULT 0,
                            content_sha256 TEXT,
                            content TEXT,
                            indexable BOOLEAN NOT NULL DEFAULT FALSE,
                            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                            PRIMARY KEY (snapshot_id, relative_path)
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_snapshot_entries_path
                        ON snapshot_entries (snapshot_id, relative_path)
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS index_generations (
                            generation_id TEXT PRIMARY KEY,
                            project_id TEXT NOT NULL REFERENCES projects(project_id)
                                ON DELETE CASCADE,
                            snapshot_id TEXT NOT NULL REFERENCES project_snapshots(snapshot_id)
                                ON DELETE CASCADE,
                            collection_name TEXT NOT NULL,
                            embedding_model TEXT NOT NULL,
                            index_version TEXT NOT NULL,
                            status TEXT NOT NULL,
                            file_count INTEGER NOT NULL DEFAULT 0,
                            chunk_count INTEGER NOT NULL DEFAULT 0,
                            error TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            activated_at TIMESTAMPTZ
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_index_generations_project
                        ON index_generations (project_id, created_at DESC)
                        """
                    )
                    connection.execute(
                        """
                        ALTER TABLE index_generations
                        ADD COLUMN IF NOT EXISTS embedding_model_id TEXT
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS generation_chunks (
                            generation_id TEXT NOT NULL
                                REFERENCES index_generations(generation_id)
                                ON DELETE CASCADE,
                            chunk_id TEXT NOT NULL,
                            document_id TEXT NOT NULL,
                            relative_path TEXT NOT NULL,
                            external_point_id UUID NOT NULL,
                            content_sha256 TEXT NOT NULL,
                            content TEXT NOT NULL,
                            line_start INTEGER,
                            line_end INTEGER,
                            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                            PRIMARY KEY (generation_id, chunk_id)
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_generation_chunks_document
                        ON generation_chunks (generation_id, document_id)
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS repository_index_jobs (
                            job_id TEXT PRIMARY KEY,
                            source_id TEXT NOT NULL REFERENCES repository_sources(source_id)
                                ON DELETE RESTRICT,
                            project_id TEXT NOT NULL,
                            snapshot_id TEXT,
                            generation_id TEXT,
                            status TEXT NOT NULL,
                            stage TEXT NOT NULL,
                            force_run BOOLEAN NOT NULL DEFAULT FALSE,
                            files_total INTEGER NOT NULL DEFAULT 0,
                            files_processed INTEGER NOT NULL DEFAULT 0,
                            chunks_stored INTEGER NOT NULL DEFAULT 0,
                            bytes_total BIGINT NOT NULL DEFAULT 0,
                            error TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            completed_at TIMESTAMPTZ
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_repository_jobs_source
                        ON repository_index_jobs (source_id, created_at DESC)
                        """
                    )
                    connection.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS
                        uq_repository_jobs_one_active_source
                        ON repository_index_jobs (source_id)
                        WHERE status NOT IN ('completed', 'failed', 'paused')
                        """
                    )
                self._initialized = True
            except (psycopg.Error, OSError) as exc:
                raise RepositoryStoreError(
                    "PostgreSQL repository schema is unavailable"
                ) from exc

    def upsert_source(self, values: dict[str, Any]) -> dict[str, Any]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    INSERT INTO repository_sources (
                        source_id, project_id, source_type, root_relative_path,
                        repository_url, default_branch, enabled
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_id) DO UPDATE SET
                        project_id = EXCLUDED.project_id,
                        source_type = EXCLUDED.source_type,
                        root_relative_path = EXCLUDED.root_relative_path,
                        repository_url = EXCLUDED.repository_url,
                        default_branch = EXCLUDED.default_branch,
                        enabled = EXCLUDED.enabled,
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
                connection.execute(
                    """
                    INSERT INTO project_snapshots (
                        snapshot_id, project_id, source_id, revision, git_branch,
                        git_dirty, git_committed_at, manifest_sha256, file_count,
                        total_bytes, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'building')
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
                    SET status = 'building', error = NULL, activated_at = NULL
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

    def activate_generation(
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
    ) -> None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE project_snapshots
                    SET status = 'completed', completed_at = NOW()
                    WHERE snapshot_id = %s
                    """,
                    (snapshot_id,),
                )
                connection.execute(
                    """
                    UPDATE index_generations
                    SET status = 'active', file_count = %s, chunk_count = %s,
                        activated_at = NOW(), error = NULL
                    WHERE generation_id = %s
                    """,
                    (file_count, chunk_count, generation_id),
                )
                connection.execute(
                    """
                    UPDATE index_generations
                    SET status = 'retired'
                    WHERE project_id = %s AND generation_id <> %s AND status = 'active'
                    """,
                    (project_id, generation_id),
                )
                connection.execute(
                    """
                    UPDATE projects
                    SET current_snapshot_id = %s,
                        active_generation_id = %s,
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
                        snapshot_id,
                        generation_id,
                        manifest_sha256,
                        revision,
                        branch,
                        dirty,
                        committed_at,
                        self._settings.embedding_model,
                        self._settings.embedding_model_id,
                        self._settings.index_version,
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
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Index generation activation failed") from exc

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
                    UPDATE projects
                    SET index_status = CASE
                            WHEN active_generation_id IS NULL THEN 'failed'
                            ELSE 'stale'
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
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT p.project_id, p.current_snapshot_id AS snapshot_id,
                           p.active_generation_id AS generation_id,
                           s.revision, s.manifest_sha256
                    FROM projects p
                    LEFT JOIN project_snapshots s
                      ON s.snapshot_id = p.current_snapshot_id
                    WHERE p.project_id = %s AND p.active_generation_id IS NOT NULL
                    """,
                    (project_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RepositoryStoreError("Active generation lookup failed") from exc

    def list_tree(self, project_id: str, prefix: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
        active = self.get_active_generation(project_id)
        if active is None:
            raise RepositoryStoreError("Project has no active index generation")
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
        active = self.get_active_generation(project_id)
        if active is None:
            raise RepositoryStoreError("Project has no active index generation")
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
