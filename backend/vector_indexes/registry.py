from __future__ import annotations


import threading
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator


from ..config import Settings
from ..snapshots.repository import PostgresSnapshotRepository




class VectorIndexStatus(str, Enum):
    REGISTERED = "registered"
    BUILDING = "building"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"




class VectorIndexCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")


    tenant_id: str = Field(default="default", min_length=1, max_length=255)
    repository_id: str = Field(..., min_length=1, max_length=512)
    source_snapshot_id: str = Field(..., min_length=1, max_length=255)
    provider_id: str = Field(..., min_length=1, max_length=255)
    endpoint_ref: str | None = Field(default=None, max_length=1024)
    collection: str = Field(..., min_length=1, max_length=512)
    namespace: str = Field(default="default", min_length=1, max_length=512)
    index_version: str = Field(..., min_length=1, max_length=255)
    embedding_model: str = Field(..., min_length=1, max_length=512)
    dimension: int = Field(..., gt=0, le=100000)
    status: VectorIndexStatus = VectorIndexStatus.REGISTERED
    metadata: dict[str, Any] = Field(default_factory=dict)


    @field_validator(
        "tenant_id",
        "repository_id",
        "source_snapshot_id",
        "provider_id",
        "collection",
        "namespace",
        "index_version",
        "embedding_model",
        "endpoint_ref",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier values cannot be blank")
        return normalized




class VectorIndexRecord(VectorIndexCreate):
    vector_index_id: str
    last_verified_at: datetime | None = None
    verification_error: str | None = None
    created_at: datetime
    updated_at: datetime




class VectorIndexFreshness(BaseModel):
    snapshot_id: str
    vector_index_id: str
    source_snapshot_id: str
    current: bool
    state: str
    action: str | None = None




class VectorIndexRegistryError(RuntimeError):
    pass




class PostgresVectorIndexRegistry:
    """Registry for external vector indexes; vector payloads remain provider-owned."""


    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._snapshot_repository = PostgresSnapshotRepository(settings)
        self._initialized = False
        self._lock = threading.Lock()


    @property
    def configured(self) -> bool:
        return bool(self._settings.postgres_password)


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
        if not self.configured:
            raise VectorIndexRegistryError("PostgreSQL vector index registry is not configured")
        self._snapshot_repository.ensure_schema()
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS external_vector_indexes (
                            vector_index_id TEXT PRIMARY KEY,
                            tenant_id TEXT NOT NULL,
                            repository_id TEXT NOT NULL,
                            source_snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id)
                                ON DELETE RESTRICT,
                            provider_id TEXT NOT NULL,
                            endpoint_ref TEXT,
                            collection_name TEXT NOT NULL,
                            namespace TEXT NOT NULL,
                            index_version TEXT NOT NULL,
                            embedding_model TEXT NOT NULL,
                            dimension INTEGER NOT NULL CHECK (dimension > 0),
                            status TEXT NOT NULL,
                            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                            last_verified_at TIMESTAMPTZ,
                            verification_error TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            UNIQUE (
                                tenant_id, provider_id, collection_name,
                                namespace, index_version
                            )
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_external_vector_indexes_snapshot
                        ON external_vector_indexes (source_snapshot_id, status)
                        """
                    )
                    connection.commit()
            except (psycopg.Error, OSError) as exc:
                raise VectorIndexRegistryError(
                    f"failed to initialize external vector index registry: {exc}"
                ) from exc
            self._initialized = True


    @staticmethod
    def _record(row: dict[str, Any]) -> VectorIndexRecord:
        payload = dict(row)
        payload["collection"] = payload.pop("collection_name")
        return VectorIndexRecord.model_validate(payload)


    def register(self, payload: VectorIndexCreate) -> tuple[VectorIndexRecord, bool]:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                snapshot = connection.execute(
                    """
                    SELECT repository_id FROM snapshots
                    WHERE snapshot_id = %s AND tenant_id = %s
                    """,
                    (payload.source_snapshot_id, payload.tenant_id),
                ).fetchone()
                if snapshot is None:
                    raise VectorIndexRegistryError(
                        f"source snapshot not found: {payload.source_snapshot_id}"
                    )
                if snapshot["repository_id"] != payload.repository_id:
                    raise VectorIndexRegistryError(
                        "source snapshot belongs to a different repository"
                    )


                vector_index_id = f"vec_{uuid4().hex}"
                row = connection.execute(
                    """
                    INSERT INTO external_vector_indexes (
                        vector_index_id, tenant_id, repository_id,
                        source_snapshot_id, provider_id, endpoint_ref,
                        collection_name, namespace, index_version,
                        embedding_model, dimension, status, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (
                        tenant_id, provider_id, collection_name,
                        namespace, index_version
                    ) DO NOTHING
                    RETURNING *
                    """,
                    (
                        vector_index_id,
                        payload.tenant_id,
                        payload.repository_id,
                        payload.source_snapshot_id,
                        payload.provider_id,
                        payload.endpoint_ref,
                        payload.collection,
                        payload.namespace,
                        payload.index_version,
                        payload.embedding_model,
                        payload.dimension,
                        payload.status.value,
                        Jsonb(payload.metadata),
                    ),
                ).fetchone()
                deduplicated = row is None
                if row is None:
                    row = connection.execute(
                        """
                        SELECT * FROM external_vector_indexes
                        WHERE tenant_id = %s AND provider_id = %s
                          AND collection_name = %s
                          AND namespace = %s
                          AND index_version = %s
                        """,
                        (
                            payload.tenant_id,
                            payload.provider_id,
                            payload.collection,
                            payload.namespace,
                            payload.index_version,
                        ),
                    ).fetchone()
                if row is None:
                    raise VectorIndexRegistryError("vector index registration returned no row")
                connection.commit()
                return self._record(row), deduplicated
        except VectorIndexRegistryError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise VectorIndexRegistryError(f"vector index registration failed: {exc}") from exc


    def get(self, vector_index_id: str) -> VectorIndexRecord | None:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM external_vector_indexes WHERE vector_index_id = %s",
                    (vector_index_id,),
                ).fetchone()
                return self._record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise VectorIndexRegistryError(f"vector index lookup failed: {exc}") from exc


    def list_for_snapshot(self, snapshot_id: str) -> list[VectorIndexRecord]:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM external_vector_indexes
                    WHERE source_snapshot_id = %s
                    ORDER BY created_at DESC
                    """,
                    (snapshot_id,),
                ).fetchall()
                return [self._record(row) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise VectorIndexRegistryError(f"vector index list failed: {exc}") from exc


    def update_status(
        self,
        vector_index_id: str,
        status: VectorIndexStatus,
        *,
        verification_error: str | None = None,
        verified: bool = False,
    ) -> VectorIndexRecord:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE external_vector_indexes
                    SET status = %s,
                        verification_error = %s,
                        last_verified_at = CASE WHEN %s THEN NOW() ELSE last_verified_at END,
                        updated_at = NOW()
                    WHERE vector_index_id = %s
                    RETURNING *
                    """,
                    (status.value, verification_error, verified, vector_index_id),
                ).fetchone()
                if row is None:
                    raise VectorIndexRegistryError(
                        f"vector index not found: {vector_index_id}"
                    )
                connection.commit()
                return self._record(row)
        except VectorIndexRegistryError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise VectorIndexRegistryError(f"vector index update failed: {exc}") from exc


    def freshness(self, snapshot_id: str, vector_index_id: str) -> VectorIndexFreshness:
        index = self.get(vector_index_id)
        if index is None:
            raise VectorIndexRegistryError(f"vector index not found: {vector_index_id}")
        current = index.source_snapshot_id == snapshot_id
        return VectorIndexFreshness(
            snapshot_id=snapshot_id,
            vector_index_id=vector_index_id,
            source_snapshot_id=index.source_snapshot_id,
            current=current,
            state="current" if current else "stale",
            action=None if current else "reindex_required",
        )