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
from ..snapshots.contracts import ConsumerType, SnapshotResolveRequest
from ..snapshots.resolver import SnapshotResolveError, SnapshotResolver




class ExchangeTargetType(str, Enum):
    CLIENT = "client"
    SLLM = "sllm"
    VECTOR_DB = "vector-db"




class ExchangeFormat(str, Enum):
    REFERENCE = "reference"
    MANIFEST = "manifest"
    PATCH = "patch"
    ARTIFACT = "artifact"
    TREE_PACKAGE = "tree-package"
    CHUNK_PACKAGE = "chunk-package"
    LLM_CONTEXT = "llm-context"




class ExchangeStatus(str, Enum):
    REQUESTED = "requested"
    RESOLVING = "resolving"
    READY = "ready"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"




class ExchangeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")


    tenant_id: str = Field(default="default", min_length=1, max_length=255)
    snapshot_id: str = Field(..., min_length=1, max_length=255)
    requester_id: str = Field(..., min_length=1, max_length=512)
    target_type: ExchangeTargetType
    target_id: str = Field(..., min_length=1, max_length=512)
    exchange_format: ExchangeFormat
    required_capabilities: list[str] = Field(default_factory=list, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


    @field_validator(
        "tenant_id",
        "snapshot_id",
        "requester_id",
        "target_id",
        "idempotency_key",
    )
    @classmethod
    def normalize_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("exchange identifiers cannot be blank")
        return normalized


    @field_validator("required_capabilities")
    @classmethod
    def normalize_capabilities(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})




class ExchangeRecord(BaseModel):
    exchange_id: str
    tenant_id: str
    snapshot_id: str
    requester_id: str
    target_type: ExchangeTargetType
    target_id: str
    exchange_format: ExchangeFormat
    required_capabilities: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None
    status: ExchangeStatus
    access_plan: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime




class ExchangeUpdate(BaseModel):
    status: ExchangeStatus
    result: dict[str, Any] | None = None
    error: str | None = None




class ExchangeHubError(RuntimeError):
    pass




class PostgresExchangeStore:
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
            raise ExchangeHubError("PostgreSQL exchange store is not configured")
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS snapshot_exchanges (
                            exchange_id TEXT PRIMARY KEY,
                            tenant_id TEXT NOT NULL,
                            snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id)
                                ON DELETE RESTRICT,
                            requester_id TEXT NOT NULL,
                            target_type TEXT NOT NULL,
                            target_id TEXT NOT NULL,
                            exchange_format TEXT NOT NULL,
                            required_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
                            idempotency_key TEXT,
                            status TEXT NOT NULL,
                            access_plan JSONB,
                            result JSONB,
                            error TEXT,
                            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_exchange_idempotency
                        ON snapshot_exchanges (requester_id, idempotency_key)
                        WHERE idempotency_key IS NOT NULL
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_snapshot_exchanges_snapshot
                        ON snapshot_exchanges (snapshot_id, created_at DESC)
                        """
                    )
                    connection.commit()
            except (psycopg.Error, OSError) as exc:
                raise ExchangeHubError(f"failed to initialize exchange store: {exc}") from exc
            self._initialized = True


    @staticmethod
    def _record(row: dict[str, Any]) -> ExchangeRecord:
        return ExchangeRecord.model_validate(row)


    def find_idempotent(self, requester_id: str, key: str) -> ExchangeRecord | None:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM snapshot_exchanges
                    WHERE requester_id = %s AND idempotency_key = %s
                    """,
                    (requester_id, key),
                ).fetchone()
                return self._record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise ExchangeHubError(f"exchange lookup failed: {exc}") from exc


    def create(
        self,
        payload: ExchangeCreate,
        *,
        status: ExchangeStatus,
        access_plan: dict[str, Any] | None,
        error: str | None,
    ) -> ExchangeRecord:
        self.ensure_schema()
        exchange_id = f"exchange_{uuid4().hex}"
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO snapshot_exchanges (
                        exchange_id, tenant_id, snapshot_id, requester_id,
                        target_type, target_id, exchange_format,
                        required_capabilities, idempotency_key, status,
                        access_plan, error, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        exchange_id,
                        payload.tenant_id,
                        payload.snapshot_id,
                        payload.requester_id,
                        payload.target_type.value,
                        payload.target_id,
                        payload.exchange_format.value,
                        Jsonb(payload.required_capabilities),
                        payload.idempotency_key,
                        status.value,
                        Jsonb(access_plan) if access_plan is not None else None,
                        error,
                        Jsonb(payload.metadata),
                    ),
                ).fetchone()
                if row is None:
                    raise ExchangeHubError("exchange creation returned no row")
                connection.commit()
                return self._record(row)
        except ExchangeHubError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise ExchangeHubError(f"exchange creation failed: {exc}") from exc


    def get(self, exchange_id: str) -> ExchangeRecord | None:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM snapshot_exchanges WHERE exchange_id = %s",
                    (exchange_id,),
                ).fetchone()
                return self._record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise ExchangeHubError(f"exchange lookup failed: {exc}") from exc


    def update(self, exchange_id: str, payload: ExchangeUpdate) -> ExchangeRecord:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE snapshot_exchanges
                    SET status = %s,
                        result = COALESCE(%s, result),
                        error = %s,
                        updated_at = NOW()
                    WHERE exchange_id = %s
                    RETURNING *
                    """,
                    (
                        payload.status.value,
                        Jsonb(payload.result) if payload.result is not None else None,
                        payload.error,
                        exchange_id,
                    ),
                ).fetchone()
                if row is None:
                    raise ExchangeHubError(f"exchange not found: {exchange_id}")
                connection.commit()
                return self._record(row)
        except ExchangeHubError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise ExchangeHubError(f"exchange update failed: {exc}") from exc




class ExchangeHub:
    """Plan and audit snapshot exchange without leaking provider credentials."""


    def __init__(self, resolver: SnapshotResolver, store: PostgresExchangeStore) -> None:
        self._resolver = resolver
        self._store = store


    @staticmethod
    def _consumer_type(target_type: ExchangeTargetType) -> ConsumerType:
        return {
            ExchangeTargetType.CLIENT: ConsumerType.CLIENT,
            ExchangeTargetType.SLLM: ConsumerType.SLLM,
            ExchangeTargetType.VECTOR_DB: ConsumerType.VECTOR_DB,
        }[target_type]


    @staticmethod
    def _default_capabilities(exchange_format: ExchangeFormat) -> list[str]:
        if exchange_format == ExchangeFormat.REFERENCE:
            return []
        if exchange_format in {ExchangeFormat.MANIFEST, ExchangeFormat.TREE_PACKAGE}:
            return ["tree.read"]
        if exchange_format in {
            ExchangeFormat.PATCH,
            ExchangeFormat.CHUNK_PACKAGE,
            ExchangeFormat.LLM_CONTEXT,
        }:
            return ["tree.read", "file.read"]
        if exchange_format == ExchangeFormat.ARTIFACT:
            return ["artifact.read"]
        return []


    def request(self, payload: ExchangeCreate) -> ExchangeRecord:
        if payload.idempotency_key:
            existing = self._store.find_idempotent(
                payload.requester_id,
                payload.idempotency_key,
            )
            if existing is not None:
                return existing


        capabilities = payload.required_capabilities or self._default_capabilities(
            payload.exchange_format
        )
        try:
            resolved = self._resolver.resolve(
                payload.snapshot_id,
                SnapshotResolveRequest(
                    consumer_type=self._consumer_type(payload.target_type),
                    consumer_id=payload.target_id,
                    required_capabilities=capabilities,
                ),
            )
        except SnapshotResolveError as exc:
            return self._store.create(
                payload,
                status=ExchangeStatus.FAILED,
                access_plan=None,
                error=str(exc),
            )


        plan = resolved.plan.model_dump(mode="json")
        plan["requested_format"] = payload.exchange_format.value
        plan["target_type"] = payload.target_type.value
        plan["target_id"] = payload.target_id
        return self._store.create(
            payload,
            status=ExchangeStatus.READY if resolved.available else ExchangeStatus.FAILED,
            access_plan=plan,
            error=None if resolved.available else resolved.reason,
        )


    def get(self, exchange_id: str) -> ExchangeRecord | None:
        return self._store.get(exchange_id)


    def update(self, exchange_id: str, payload: ExchangeUpdate) -> ExchangeRecord:
        return self._store.update(exchange_id, payload)