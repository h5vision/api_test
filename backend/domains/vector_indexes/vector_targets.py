from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from .config import Settings
from .schema_guard import SchemaStateError, require_schema


VectorTargetStatus = Literal["configured", "healthy", "unavailable", "disabled"]


class VectorTargetStoreError(RuntimeError):
    pass


def normalize_vector_target_endpoint(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("vector target endpoint must be an absolute HTTP(S) URL")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("vector target endpoint must not contain path, query, or fragment")
    return normalized


def infer_vector_target_deployment(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1", "qdrant", "host.docker.internal"}:
        return "local"
    if host.endswith(".svc") or ".svc." in host:
        return "cluster"
    return "remote_server"


def vector_target_identity(tenant_id: str, engine: str, endpoint: str) -> str:
    # Keep runtime-created IDs identical to the deterministic P2-C migration backfill.
    material = "|".join(
        (
            tenant_id.strip() or "vision-default",
            engine.strip().lower(),
            normalize_vector_target_endpoint(endpoint).lower(),
        )
    )
    return f"vtarget_{hashlib.md5(material.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True)
class VectorTargetRecord:
    vector_target_id: str
    tenant_id: str
    name: str
    engine: str
    endpoint: str
    credential_ref: str | None
    deployment_type: str
    capabilities: dict[str, Any]
    status: VectorTargetStatus
    error: str | None
    latency_ms: int | None
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def enabled(self) -> bool:
        return self.status != "disabled"


class PostgresVectorTargetStore:
    """Canonical P2-C registry for physical vector engine targets."""

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
                raise VectorTargetStoreError(
                    "PostgreSQL schema is not on the required P2-C revision"
                ) from exc

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> VectorTargetRecord:
        capabilities = row.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}
        return VectorTargetRecord(
            vector_target_id=str(row["vector_target_id"]),
            tenant_id=str(row["tenant_id"]),
            name=str(row["name"]),
            engine=str(row["engine"]),
            endpoint=str(row["endpoint"]).rstrip("/"),
            credential_ref=(str(row["credential_ref"]) if row.get("credential_ref") else None),
            deployment_type=str(row["deployment_type"]),
            capabilities=dict(capabilities),
            status=str(row["status"]),
            error=(str(row["error"]) if row.get("error") else None),
            latency_ms=(int(row["latency_ms"]) if row.get("latency_ms") is not None else None),
            last_checked_at=row.get("last_checked_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list(self, *, tenant_id: str | None = None) -> list[VectorTargetRecord]:
        self._ensure_schema()
        resolved_tenant = (tenant_id or self._settings.snapshot_tenant_id).strip() or "vision-default"
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT vector_target_id, tenant_id, name, engine, endpoint,
                           credential_ref, deployment_type, capabilities, status,
                           error, latency_ms, last_checked_at, created_at, updated_at
                    FROM vector_targets
                    WHERE tenant_id = %s
                    ORDER BY created_at, vector_target_id
                    """,
                    (resolved_tenant,),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise VectorTargetStoreError("VectorTarget registry read failed") from exc
        return [self._row_to_record(row) for row in rows]

    def get(self, vector_target_id: str) -> VectorTargetRecord | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT vector_target_id, tenant_id, name, engine, endpoint,
                           credential_ref, deployment_type, capabilities, status,
                           error, latency_ms, last_checked_at, created_at, updated_at
                    FROM vector_targets
                    WHERE vector_target_id = %s
                    """,
                    (vector_target_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise VectorTargetStoreError("VectorTarget registry read failed") from exc
        return self._row_to_record(row) if row is not None else None

    def upsert_qdrant(
        self,
        *,
        endpoint: str,
        name: str | None = None,
        tenant_id: str | None = None,
        credential_ref: str | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> VectorTargetRecord:
        self._ensure_schema()
        resolved_endpoint = normalize_vector_target_endpoint(endpoint)
        resolved_tenant = (tenant_id or self._settings.snapshot_tenant_id).strip() or "vision-default"
        target_id = vector_target_identity(resolved_tenant, "qdrant", resolved_endpoint)
        deployment_type = infer_vector_target_deployment(resolved_endpoint)
        resolved_name = (name or "Qdrant").strip() or "Qdrant"
        resolved_capabilities = capabilities or {
            "dense_vectors": True,
            "payload_filter": True,
            "exact_count": True,
            "provision_index": True,
            "named_vectors": True,
            "hybrid_query": True,
            "rrf": True,
        }
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO vector_targets (
                        vector_target_id, tenant_id, name, engine, endpoint,
                        credential_ref, deployment_type, capabilities, status,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, 'qdrant', %s, %s, %s, %s::jsonb,
                              'configured', NOW(), NOW())
                    ON CONFLICT (tenant_id, engine, endpoint)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        credential_ref = COALESCE(EXCLUDED.credential_ref, vector_targets.credential_ref),
                        deployment_type = EXCLUDED.deployment_type,
                        capabilities = EXCLUDED.capabilities,
                        status = CASE
                            WHEN vector_targets.status = 'disabled' THEN 'disabled'
                            ELSE 'configured'
                        END,
                        error = NULL,
                        updated_at = NOW()
                    RETURNING vector_target_id, tenant_id, name, engine, endpoint,
                              credential_ref, deployment_type, capabilities, status,
                              error, latency_ms, last_checked_at, created_at, updated_at
                    """,
                    (
                        target_id,
                        resolved_tenant,
                        resolved_name,
                        resolved_endpoint,
                        credential_ref,
                        deployment_type,
                        json.dumps(resolved_capabilities, sort_keys=True),
                    ),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise VectorTargetStoreError("VectorTarget registry write failed") from exc
        if row is None:
            raise VectorTargetStoreError("VectorTarget registry did not return the saved target")
        return self._row_to_record(row)

    def set_status(
        self,
        vector_target_id: str,
        *,
        status: VectorTargetStatus,
        error: str | None = None,
        latency_ms: int | None = None,
        checked: bool = False,
    ) -> VectorTargetRecord:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE vector_targets
                    SET status = %s,
                        error = %s,
                        latency_ms = %s,
                        last_checked_at = CASE WHEN %s THEN NOW() ELSE last_checked_at END,
                        updated_at = NOW()
                    WHERE vector_target_id = %s
                    RETURNING vector_target_id, tenant_id, name, engine, endpoint,
                              credential_ref, deployment_type, capabilities, status,
                              error, latency_ms, last_checked_at, created_at, updated_at
                    """,
                    (status, error, latency_ms, checked, vector_target_id),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise VectorTargetStoreError("VectorTarget status update failed") from exc
        if row is None:
            raise VectorTargetStoreError("VectorTarget does not exist")
        return self._row_to_record(row)

