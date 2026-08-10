from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings
from .schema_guard import SchemaStateError, require_schema
from .schemas import DocumentInput, GitVersionInfo, Source


class ProjectStoreError(RuntimeError):
    pass


class PostgresProjectStore:
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
            return
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
                raise ProjectStoreError(
                    "PostgreSQL schema is not on the required Alembic baseline"
                ) from exc

    @staticmethod
    def content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def current_version(self, project_id: str, document_id: str) -> dict[str, Any] | None:
        if not self.configured:
            return None
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT document_version_id, content_sha256
                    FROM document_versions
                    WHERE project_id = %s AND document_id = %s AND is_current
                    """,
                    (project_id, document_id),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise ProjectStoreError("PostgreSQL current document lookup failed") from exc

    def register_snapshot(
        self,
        project_id: str,
        snapshot_id: str,
        *,
        manifest_sha256: str | None = None,
        modified_at: datetime | None = None,
        git: GitVersionInfo | None = None,
    ) -> None:
        if not self.configured:
            return
        self._ensure_schema()
        git_commit_sha = git.commit_sha if git else None
        git_branch = git.branch if git else None
        git_dirty = git.dirty if git else None
        git_committed_at = git.committed_at if git else None
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO projects (
                        project_id, display_name, current_snapshot_id,
                        manifest_sha256, git_commit_sha, git_branch, git_dirty,
                        git_committed_at, source_modified_at,
                        embedding_model, index_version, index_status
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued'
                    )
                    ON CONFLICT (project_id) DO UPDATE SET
                        current_snapshot_id = EXCLUDED.current_snapshot_id,
                        manifest_sha256 = COALESCE(
                            EXCLUDED.manifest_sha256, projects.manifest_sha256
                        ),
                        git_commit_sha = COALESCE(
                            EXCLUDED.git_commit_sha, projects.git_commit_sha
                        ),
                        git_branch = COALESCE(EXCLUDED.git_branch, projects.git_branch),
                        git_dirty = COALESCE(EXCLUDED.git_dirty, projects.git_dirty),
                        git_committed_at = COALESCE(
                            EXCLUDED.git_committed_at, projects.git_committed_at
                        ),
                        source_modified_at = COALESCE(
                            EXCLUDED.source_modified_at, projects.source_modified_at
                        ),
                        embedding_model = EXCLUDED.embedding_model,
                        index_version = EXCLUDED.index_version,
                        index_status = 'queued',
                        updated_at = NOW()
                    """,
                    (
                        project_id,
                        project_id,
                        snapshot_id,
                        manifest_sha256,
                        git_commit_sha,
                        git_branch,
                        git_dirty,
                        git_committed_at,
                        modified_at,
                        self._settings.embedding_model,
                        self._settings.index_version,
                    ),
                )
        except (psycopg.Error, OSError) as exc:
            raise ProjectStoreError("PostgreSQL snapshot registration failed") from exc

    def get_version(self, project_id: str) -> dict[str, Any] | None:
        if not self.configured:
            return None
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT project_id, current_snapshot_id, manifest_sha256,
                           git_commit_sha, git_branch, git_dirty, git_committed_at,
                           source_modified_at, index_status, created_at, updated_at
                    FROM projects
                    WHERE project_id = %s
                    """,
                    (project_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise ProjectStoreError("PostgreSQL project version lookup failed") from exc

    def list_projects(self) -> list[dict[str, Any]]:
        if not self.configured:
            raise ProjectStoreError("PostgreSQL project store is not configured")
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT project_id, display_name, current_snapshot_id,
                           git_commit_sha, git_branch, git_dirty,
                           git_committed_at, index_status,
                           index_completed_at, updated_at
                    FROM projects
                    ORDER BY LOWER(display_name) ASC, project_id ASC
                    """
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise ProjectStoreError("PostgreSQL project list lookup failed") from exc

    def set_index_status(self, project_id: str, index_status: str) -> None:
        if not self.configured:
            return
        if index_status not in {"queued", "indexing", "completed", "failed"}:
            raise ProjectStoreError("Unsupported project index status")
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE projects
                    SET index_status = %s,
                        index_completed_at = CASE
                            WHEN %s = 'completed' THEN NOW()
                            ELSE index_completed_at
                        END,
                        updated_at = NOW()
                    WHERE project_id = %s
                    """,
                    (index_status, index_status, project_id),
                )
        except (psycopg.Error, OSError) as exc:
            raise ProjectStoreError("PostgreSQL index status update failed") from exc

    def save_document(
        self,
        project_id: str,
        document: DocumentInput,
        chunks: list[dict[str, Any]],
    ) -> str | None:
        if not self.configured:
            return None
        self._ensure_schema()
        document_hash = self.content_hash(document.text)
        version_id = uuid4()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO projects (
                        project_id, display_name, embedding_model, index_version
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (project_id) DO UPDATE SET
                        embedding_model = EXCLUDED.embedding_model,
                        index_version = EXCLUDED.index_version,
                        index_status = 'indexing',
                        updated_at = NOW()
                    """,
                    (
                        project_id,
                        project_id,
                        self._settings.embedding_model,
                        self._settings.index_version,
                    ),
                )
                existing = connection.execute(
                    """
                    SELECT document_version_id
                    FROM document_versions
                    WHERE project_id = %s AND document_id = %s AND content_sha256 = %s
                    """,
                    (project_id, document.document_id, document_hash),
                ).fetchone()
                if existing:
                    version_id = existing["document_version_id"]
                    connection.execute(
                        """
                        UPDATE document_versions
                        SET is_current = (document_version_id = %s)
                        WHERE project_id = %s AND document_id = %s
                        """,
                        (version_id, project_id, document.document_id),
                    )
                    connection.execute(
                        "DELETE FROM document_chunks WHERE document_version_id = %s",
                        (version_id,),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE document_versions SET is_current = FALSE
                        WHERE project_id = %s AND document_id = %s AND is_current
                        """,
                        (project_id, document.document_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO document_versions (
                            document_version_id, project_id, document_id, path,
                            language, content_sha256, content, metadata, is_current
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                        """,
                        (
                            version_id,
                            project_id,
                            document.document_id,
                            document.path,
                            document.language,
                            document_hash,
                            document.text,
                            Jsonb(document.metadata),
                        ),
                    )
                for chunk in chunks:
                    chunk["document_version_id"] = str(version_id)
                    connection.execute(
                        """
                        INSERT INTO document_chunks (
                            chunk_id, document_version_id, project_id, document_id,
                            content, content_sha256, line_start, line_end, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            chunk["chunk_id"],
                            version_id,
                            project_id,
                            document.document_id,
                            chunk["content"],
                            self.content_hash(chunk["content"]),
                            chunk.get("line_start"),
                            chunk.get("line_end"),
                            Jsonb(document.metadata),
                        ),
                    )
                    point_id = uuid5(NAMESPACE_URL, f"{project_id}:{chunk['chunk_id']}")
                    connection.execute(
                        """
                        INSERT INTO vector_mappings (
                            chunk_id, external_point_id, collection_name,
                            embedding_model, index_version
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (chunk_id) DO UPDATE SET
                            external_point_id = EXCLUDED.external_point_id,
                            collection_name = EXCLUDED.collection_name,
                            embedding_model = EXCLUDED.embedding_model,
                            index_version = EXCLUDED.index_version,
                            updated_at = NOW()
                        """,
                        (
                            chunk["chunk_id"],
                            point_id,
                            self._settings.qdrant_collection,
                            self._settings.embedding_model,
                            self._settings.index_version,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE projects
                    SET index_status = 'ready',
                        index_completed_at = NOW(),
                        updated_at = NOW()
                    WHERE project_id = %s
                    """,
                    (project_id,),
                )
            return str(version_id)
        except (psycopg.Error, OSError) as exc:
            raise ProjectStoreError("PostgreSQL document version write failed") from exc

    def enrich_sources(self, sources: list[Source]) -> list[Source]:
        if not self.configured or not sources:
            return sources
        self._ensure_schema()
        chunk_ids = [source.chunk_id for source in sources]
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT c.chunk_id, c.content, c.line_start, c.line_end,
                           c.metadata, v.document_version_id, v.path, v.language
                    FROM document_chunks c
                    JOIN document_versions v
                      ON v.document_version_id = c.document_version_id
                    WHERE c.chunk_id = ANY(%s) AND v.is_current
                    """,
                    (chunk_ids,),
                ).fetchall()
            by_id = {row["chunk_id"]: row for row in rows}
            enriched: list[Source] = []
            for source in sources:
                row = by_id.get(source.chunk_id)
                if row is None:
                    enriched.append(source)
                    continue
                enriched.append(
                    source.model_copy(
                        update={
                            "document_version_id": str(row["document_version_id"]),
                            "path": row["path"],
                            "language": row["language"],
                            "line_start": row["line_start"],
                            "line_end": row["line_end"],
                            "text": row["content"],
                            "metadata": row["metadata"],
                        }
                    )
                )
            return enriched
        except (psycopg.Error, OSError) as exc:
            raise ProjectStoreError("PostgreSQL source lookup failed") from exc

    def delete_project(self, project_id: str) -> int:
        if not self.configured:
            return 0
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "DELETE FROM projects WHERE project_id = %s RETURNING project_id",
                    (project_id,),
                ).fetchone()
            return 1 if row else 0
        except (psycopg.Error, OSError) as exc:
            raise ProjectStoreError("PostgreSQL project delete failed") from exc

    def status(self) -> dict[str, Any]:
        if not self.configured:
            return {"provider": "postgresql", "status": "not_configured"}
        try:
            self._ensure_schema()
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM projects) AS projects,
                        (SELECT COUNT(*) FROM document_versions WHERE is_current)
                            AS current_documents,
                        (SELECT COUNT(*) FROM document_chunks) AS chunks
                    """
                ).fetchone()
            return {
                "provider": "postgresql",
                "status": "ok",
                "projects": int(row["projects"]),
                "current_documents": int(row["current_documents"]),
                "chunks": int(row["chunks"]),
            }
        except ProjectStoreError:
            return {"provider": "postgresql", "status": "unavailable"}
