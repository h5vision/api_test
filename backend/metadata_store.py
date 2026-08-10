from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings
from .schema_guard import SchemaStateError, require_schema
from .schemas import (
    MetadataRecord,
    MetadataUpsertRequest,
    ProjectMetadataDocumentInput,
)


class MetadataStoreError(RuntimeError):
    pass


class PostgresMetadataStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._initialize_lock = threading.Lock()

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
                raise MetadataStoreError(
                    "PostgreSQL schema is not on the required Alembic baseline"
                ) from exc

    @staticmethod
    def _to_record(row: dict[str, Any]) -> MetadataRecord:
        return MetadataRecord(
            metadata_id=row["metadata_id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            scope=row["scope"],
            entity_id=row["entity_id"],
            source=row["source"],
            metadata=row["payload"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert(self, payload: MetadataUpsertRequest) -> MetadataRecord:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO frontend_metadata (
                        metadata_id, project_id, session_id, scope,
                        entity_id, source, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, scope, entity_id)
                    DO UPDATE SET
                        session_id = EXCLUDED.session_id,
                        source = EXCLUDED.source,
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    RETURNING metadata_id, project_id, session_id, scope,
                              entity_id, source, payload, created_at, updated_at
                    """,
                    (
                        uuid4(),
                        payload.project_id,
                        payload.session_id,
                        payload.scope,
                        payload.entity_id,
                        payload.source,
                        Jsonb(payload.metadata),
                    ),
                ).fetchone()
            if row is None:
                raise MetadataStoreError("PostgreSQL did not return the saved metadata")
            return self._to_record(row)
        except (psycopg.Error, OSError) as exc:
            raise MetadataStoreError("PostgreSQL metadata write failed") from exc

    def upsert_documents(
        self,
        project_id: str,
        documents: list[ProjectMetadataDocumentInput],
    ) -> int:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                for document in documents:
                    connection.execute(
                        """
                        INSERT INTO frontend_documents (
                            project_id, document_id, path, language, type, details
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (project_id, document_id)
                        DO UPDATE SET
                            path = EXCLUDED.path,
                            language = EXCLUDED.language,
                            type = EXCLUDED.type,
                            details = EXCLUDED.details,
                            updated_at = NOW()
                        """,
                        (
                            project_id,
                            document.name,
                            document.path,
                            document.language,
                            document.type,
                            Jsonb(
                                document.model_dump(
                                    mode="json",
                                    by_alias=True,
                                    exclude={"name", "path", "language", "type"},
                                    exclude_none=True,
                                )
                            ),
                        ),
                    )
            return len(documents)
        except (psycopg.Error, OSError) as exc:
            raise MetadataStoreError("PostgreSQL document registration failed") from exc

    def list_project(
        self,
        project_id: str,
        scope: str | None,
        limit: int,
    ) -> list[MetadataRecord]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                if scope:
                    rows = connection.execute(
                        """
                        SELECT metadata_id, project_id, session_id, scope,
                               entity_id, source, payload, created_at, updated_at
                        FROM frontend_metadata
                        WHERE project_id = %s AND scope = %s
                        ORDER BY updated_at DESC
                        LIMIT %s
                        """,
                        (project_id, scope, limit),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT metadata_id, project_id, session_id, scope,
                               entity_id, source, payload, created_at, updated_at
                        FROM frontend_metadata
                        WHERE project_id = %s
                        ORDER BY updated_at DESC
                        LIMIT %s
                        """,
                        (project_id, limit),
                    ).fetchall()
            return [self._to_record(row) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise MetadataStoreError("PostgreSQL metadata read failed") from exc

    def status(self) -> dict[str, Any]:
        if not self._settings.postgres_password:
            return {
                "provider": "postgresql",
                "status": "not_configured",
                "records": 0,
                "projects": 0,
                "registered_documents": 0,
            }
        try:
            self._ensure_schema()
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS records,
                           COUNT(DISTINCT project_id) AS projects,
                           (SELECT COUNT(*) FROM frontend_documents)
                               AS registered_documents
                    FROM frontend_metadata
                    """
                ).fetchone()
            return {
                "provider": "postgresql",
                "status": "ok",
                "records": int(row["records"]) if row else 0,
                "projects": int(row["projects"]) if row else 0,
                "registered_documents": (
                    int(row["registered_documents"]) if row else 0
                ),
            }
        except MetadataStoreError:
            return {
                "provider": "postgresql",
                "status": "unavailable",
                "records": 0,
                "projects": 0,
                "registered_documents": 0,
            }
        except (psycopg.Error, OSError):
            return {
                "provider": "postgresql",
                "status": "unavailable",
                "records": 0,
                "projects": 0,
                "registered_documents": 0,
            }
