from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from .config import Settings
from .schema_guard import SchemaStateError, require_schema


EmbeddingProfileStatus = Literal["configured", "healthy", "unavailable", "disabled"]
EmbeddingDeployment = Literal["api", "local"]
EmbeddingProvider = Literal["ollama", "openai", "nvidia"]


class EmbeddingProfileStoreError(RuntimeError):
    pass


def normalize_embedding_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("embedding base_url must be an absolute HTTP(S) URL")
    if parsed.fragment:
        raise ValueError("embedding base_url must not contain a fragment")
    return normalized


def embedding_profile_identity(
    tenant_id: str,
    *,
    deployment: str,
    provider: str,
    base_url: str,
    model: str,
    model_id: str,
    dimension: int,
) -> str:
    resolved_tenant = tenant_id.strip() or "vision-default"
    resolved_deployment = deployment.strip().lower()
    resolved_provider = provider.strip().lower()
    resolved_base_url = normalize_embedding_base_url(base_url)
    resolved_model = model.strip()
    resolved_model_id = model_id.strip()
    if resolved_deployment not in {"api", "local"}:
        raise ValueError("unsupported embedding deployment")
    if resolved_provider not in {"ollama", "openai", "nvidia"}:
        raise ValueError("unsupported embedding provider")
    if not resolved_model or not resolved_model_id:
        raise ValueError("embedding model and model_id must not be blank")
    if int(dimension) <= 0:
        raise ValueError("embedding dimension must be positive")
    # P2-D identity represents vector-space/execution compatibility. batch_size is
    # intentionally excluded because it is execution tuning, not vector-space identity.
    material = "|".join(
        (
            resolved_tenant,
            resolved_deployment,
            resolved_provider,
            resolved_base_url,
            resolved_model,
            resolved_model_id,
            str(int(dimension)),
        )
    )
    return f"eprof_{hashlib.md5(material.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True)
class EmbeddingProfileRecord:
    embedding_profile_id: str
    tenant_id: str
    name: str
    deployment: str
    provider: str
    base_url: str
    model: str
    model_id: str
    dimension: int
    batch_size: int
    credential_ref: str | None
    status: EmbeddingProfileStatus
    error: str | None
    latency_ms: int | None
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def enabled(self) -> bool:
        return self.status != "disabled"


class PostgresEmbeddingProfileStore:
    """Canonical P2-D registry for embedding execution/vector-space contracts."""

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
                raise EmbeddingProfileStoreError(
                    "PostgreSQL schema is not on the required P2-D revision"
                ) from exc

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> EmbeddingProfileRecord:
        return EmbeddingProfileRecord(
            embedding_profile_id=str(row["embedding_profile_id"]),
            tenant_id=str(row["tenant_id"]),
            name=str(row["name"]),
            deployment=str(row["deployment"]),
            provider=str(row["provider"]),
            base_url=str(row["base_url"]).rstrip("/"),
            model=str(row["model"]),
            model_id=str(row["model_id"]),
            dimension=int(row["dimension"]),
            batch_size=int(row["batch_size"]),
            credential_ref=(str(row["credential_ref"]) if row.get("credential_ref") else None),
            status=str(row["status"]),
            error=(str(row["error"]) if row.get("error") else None),
            latency_ms=(int(row["latency_ms"]) if row.get("latency_ms") is not None else None),
            last_checked_at=row.get("last_checked_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list(self, *, tenant_id: str | None = None) -> list[EmbeddingProfileRecord]:
        self._ensure_schema()
        resolved_tenant = (tenant_id or self._settings.snapshot_tenant_id).strip() or "vision-default"
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT embedding_profile_id, tenant_id, name, deployment, provider,
                           base_url, model, model_id, dimension, batch_size,
                           credential_ref, status, error, latency_ms, last_checked_at,
                           created_at, updated_at
                    FROM embedding_profiles
                    WHERE tenant_id = %s
                    ORDER BY created_at, embedding_profile_id
                    """,
                    (resolved_tenant,),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise EmbeddingProfileStoreError("EmbeddingProfile registry read failed") from exc
        return [self._row_to_record(row) for row in rows]

    def get(self, embedding_profile_id: str) -> EmbeddingProfileRecord | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT embedding_profile_id, tenant_id, name, deployment, provider,
                           base_url, model, model_id, dimension, batch_size,
                           credential_ref, status, error, latency_ms, last_checked_at,
                           created_at, updated_at
                    FROM embedding_profiles
                    WHERE embedding_profile_id = %s
                    """,
                    (embedding_profile_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise EmbeddingProfileStoreError("EmbeddingProfile registry read failed") from exc
        return self._row_to_record(row) if row is not None else None

    def upsert(
        self,
        *,
        deployment: str,
        provider: str,
        base_url: str,
        model: str,
        model_id: str,
        dimension: int,
        batch_size: int,
        name: str | None = None,
        tenant_id: str | None = None,
        credential_ref: str | None = None,
    ) -> EmbeddingProfileRecord:
        self._ensure_schema()
        resolved_tenant = (tenant_id or self._settings.snapshot_tenant_id).strip() or "vision-default"
        resolved_deployment = deployment.strip().lower()
        resolved_provider = provider.strip().lower()
        resolved_base_url = normalize_embedding_base_url(base_url)
        resolved_model = model.strip()
        resolved_model_id = model_id.strip()
        resolved_dimension = int(dimension)
        resolved_batch_size = int(batch_size)
        if resolved_batch_size < 1 or resolved_batch_size > 256:
            raise ValueError("embedding batch_size must be between 1 and 256")
        profile_id = embedding_profile_identity(
            resolved_tenant,
            deployment=resolved_deployment,
            provider=resolved_provider,
            base_url=resolved_base_url,
            model=resolved_model,
            model_id=resolved_model_id,
            dimension=resolved_dimension,
        )
        resolved_name = (name or resolved_model_id or resolved_model).strip()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO embedding_profiles (
                        embedding_profile_id, tenant_id, name, deployment, provider,
                        base_url, model, model_id, dimension, batch_size,
                        credential_ref, status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              'configured', NOW(), NOW())
                    ON CONFLICT (
                        tenant_id, deployment, provider, base_url, model, model_id, dimension
                    ) DO UPDATE SET
                        name = EXCLUDED.name,
                        batch_size = EXCLUDED.batch_size,
                        credential_ref = COALESCE(EXCLUDED.credential_ref, embedding_profiles.credential_ref),
                        status = CASE
                            WHEN embedding_profiles.status = 'disabled' THEN 'disabled'
                            ELSE 'configured'
                        END,
                        error = NULL,
                        updated_at = NOW()
                    RETURNING embedding_profile_id, tenant_id, name, deployment, provider,
                              base_url, model, model_id, dimension, batch_size,
                              credential_ref, status, error, latency_ms, last_checked_at,
                              created_at, updated_at
                    """,
                    (
                        profile_id,
                        resolved_tenant,
                        resolved_name,
                        resolved_deployment,
                        resolved_provider,
                        resolved_base_url,
                        resolved_model,
                        resolved_model_id,
                        resolved_dimension,
                        resolved_batch_size,
                        credential_ref,
                    ),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise EmbeddingProfileStoreError("EmbeddingProfile registry write failed") from exc
        if row is None:
            raise EmbeddingProfileStoreError("EmbeddingProfile registry did not return the saved profile")
        return self._row_to_record(row)

    def set_status(
        self,
        embedding_profile_id: str,
        *,
        status: EmbeddingProfileStatus,
        error: str | None = None,
        latency_ms: int | None = None,
        checked: bool = False,
    ) -> EmbeddingProfileRecord:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE embedding_profiles
                    SET status = %s,
                        error = %s,
                        latency_ms = %s,
                        last_checked_at = CASE WHEN %s THEN NOW() ELSE last_checked_at END,
                        updated_at = NOW()
                    WHERE embedding_profile_id = %s
                    RETURNING embedding_profile_id, tenant_id, name, deployment, provider,
                              base_url, model, model_id, dimension, batch_size,
                              credential_ref, status, error, latency_ms, last_checked_at,
                              created_at, updated_at
                    """,
                    (status, error, latency_ms, checked, embedding_profile_id),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise EmbeddingProfileStoreError("EmbeddingProfile status update failed") from exc
        if row is None:
            raise EmbeddingProfileStoreError("EmbeddingProfile does not exist")
        return self._row_to_record(row)

