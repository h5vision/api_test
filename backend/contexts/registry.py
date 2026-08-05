from __future__ import annotations


import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator


from ..config import Settings
from ..snapshots.repository import PostgresSnapshotRepository
from ..vector_indexes.registry import (
    PostgresVectorIndexRegistry,
    VectorIndexRegistryError,
    VectorIndexStatus,
)




class ContextCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")


    tenant_id: str = Field(default="default", min_length=1, max_length=255)
    client_id: str = Field(..., min_length=1, max_length=255)
    repository_id: str = Field(..., min_length=1, max_length=512)
    snapshot_id: str = Field(..., min_length=1, max_length=255)
    vector_index_id: str | None = Field(default=None, max_length=255)
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    allow_stale_vector: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


    @field_validator(
        "tenant_id",
        "client_id",
        "repository_id",
        "snapshot_id",
        "vector_index_id",
    )
    @classmethod
    def normalize_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("context identifiers cannot be blank")
        return normalized




class ContextRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


    context_id: str
    tenant_id: str
    client_id: str
    repository_id: str
    snapshot_id: str
    vector_index_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime
    created_at: datetime
    last_used_at: datetime | None = None


    @property
    def expired(self) -> bool:
        now = datetime.now(timezone.utc)
        value = self.expires_at
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value <= now




class ContextBindingError(RuntimeError):
    code = "CONTEXT_BINDING_ERROR"




class SnapshotVectorMismatchError(ContextBindingError):
    code = "SNAPSHOT_VECTOR_MISMATCH"


    def __init__(
        self,
        requested_snapshot_id: str,
        indexed_snapshot_id: str,
        vector_index_id: str,
    ) -> None:
        super().__init__(
            "vector index source snapshot does not match the requested snapshot"
        )
        self.requested_snapshot_id = requested_snapshot_id
        self.indexed_snapshot_id = indexed_snapshot_id
        self.vector_index_id = vector_index_id




class ContextExpiredError(ContextBindingError):
    code = "CONTEXT_EXPIRED"




class PostgresContextStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._lock = threading.Lock()


    def _connect(self):
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
        if not self._settings.postgres_password:
            raise ContextBindingError("PostgreSQL context store is not configured")
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS ai_snapshot_contexts (
                            context_id TEXT PRIMARY KEY,
                            tenant_id TEXT NOT NULL,
                            client_id TEXT NOT NULL,
                            repository_id TEXT NOT NULL,
                            snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id)
                                ON DELETE RESTRICT,
                            vector_index_id TEXT,
                            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                            expires_at TIMESTAMPTZ NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            last_used_at TIMESTAMPTZ
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_ai_snapshot_contexts_client
                        ON ai_snapshot_contexts (client_id, created_at DESC)
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_ai_snapshot_contexts_expiry
                        ON ai_snapshot_contexts (expires_at)
                        """
                    )
                    connection.commit()
            except (psycopg.Error, OSError) as exc:
                raise ContextBindingError(f"failed to initialize context store: {exc}") from exc
            self._initialized = True


    @staticmethod
    def _record(row: dict[str, Any]) -> ContextRecord:
        return ContextRecord.model_validate(row)


    def create(self, payload: ContextCreate) -> ContextRecord:
        self.ensure_schema()
        context_id = f"ctx_{uuid4().hex}"
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=payload.ttl_seconds)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO ai_snapshot_contexts (
                        context_id, tenant_id, client_id, repository_id,
                        snapshot_id, vector_index_id, metadata, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        context_id,
                        payload.tenant_id,
                        payload.client_id,
                        payload.repository_id,
                        payload.snapshot_id,
                        payload.vector_index_id,
                        Jsonb(payload.metadata),
                        expires_at,
                    ),
                ).fetchone()
                if row is None:
                    raise ContextBindingError("context creation returned no row")
                connection.commit()
                return self._record(row)
        except ContextBindingError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise ContextBindingError(f"context creation failed: {exc}") from exc


    def get(self, context_id: str, *, touch: bool = False) -> ContextRecord | None:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                if touch:
                    row = connection.execute(
                        """
                        UPDATE ai_snapshot_contexts
                        SET last_used_at = NOW()
                        WHERE context_id = %s
                        RETURNING *
                        """,
                        (context_id,),
                    ).fetchone()
                    connection.commit()
                else:
                    row = connection.execute(
                        "SELECT * FROM ai_snapshot_contexts WHERE context_id = %s",
                        (context_id,),
                    ).fetchone()
                return self._record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise ContextBindingError(f"context lookup failed: {exc}") from exc


    def delete(self, context_id: str) -> bool:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                result = connection.execute(
                    "DELETE FROM ai_snapshot_contexts WHERE context_id = %s",
                    (context_id,),
                )
                deleted = result.rowcount > 0
                connection.commit()
                return deleted
        except (psycopg.Error, OSError) as exc:
            raise ContextBindingError(f"context deletion failed: {exc}") from exc




class ContextService:
    """Bind one question context to an immutable snapshot and optional vector index."""


    def __init__(
        self,
        snapshot_repository: PostgresSnapshotRepository,
        vector_registry: PostgresVectorIndexRegistry,
        context_store: PostgresContextStore,
    ) -> None:
        self._snapshots = snapshot_repository
        self._vectors = vector_registry
        self._contexts = context_store


    def create(self, payload: ContextCreate) -> ContextRecord:
        snapshot = self._snapshots.get_snapshot(payload.snapshot_id)
        if snapshot is None:
            raise ContextBindingError(f"snapshot not found: {payload.snapshot_id}")
        if snapshot["tenant_id"] != payload.tenant_id:
            raise ContextBindingError("snapshot belongs to a different tenant")
        if snapshot["repository_id"] != payload.repository_id:
            raise ContextBindingError("snapshot belongs to a different repository")


        if payload.vector_index_id:
            try:
                vector = self._vectors.get(payload.vector_index_id)
            except VectorIndexRegistryError as exc:
                raise ContextBindingError(str(exc)) from exc
            if vector is None:
                raise ContextBindingError(
                    f"vector index not found: {payload.vector_index_id}"
                )
            if vector.source_snapshot_id != payload.snapshot_id:
                raise SnapshotVectorMismatchError(
                    payload.snapshot_id,
                    vector.source_snapshot_id,
                    vector.vector_index_id,
                )
            if vector.repository_id != payload.repository_id:
                raise ContextBindingError("vector index belongs to a different repository")
            if vector.tenant_id != payload.tenant_id:
                raise ContextBindingError("vector index belongs to a different tenant")
            if not payload.allow_stale_vector and vector.status != VectorIndexStatus.READY:
                raise ContextBindingError(
                    f"vector index is not ready: {vector.status.value}"
                )


        return self._contexts.create(payload)


    def resolve(self, context_id: str) -> ContextRecord:
        context = self._contexts.get(context_id, touch=True)
        if context is None:
            raise ContextBindingError(f"context not found: {context_id}")
        if context.expired:
            raise ContextExpiredError(f"context expired: {context_id}")
        return context


    def delete(self, context_id: str) -> bool:
        return self._contexts.delete(context_id)