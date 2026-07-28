from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from .config import Settings


class RuntimeServiceSettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeGroqSettings:
    enabled: bool
    base_url: str
    model: str
    default_model_id: str


@dataclass(frozen=True)
class RuntimeVectorSettings:
    host: str
    port: int
    collection: str
    embedding_deployment: str
    embedding_provider: str
    embedding_base_url: str
    embedding_model: str
    embedding_model_id: str
    embedding_dimension: int
    embedding_batch_size: int
    index_version: str

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class RuntimeServiceSettings:
    groq: RuntimeGroqSettings
    vector: RuntimeVectorSettings
    updated_at: datetime | None = None


def _vector_default(settings: Settings) -> RuntimeVectorSettings:
    parsed = urlsplit(settings.qdrant_url)
    return RuntimeVectorSettings(
        host=parsed.hostname or "qdrant",
        port=parsed.port or (443 if parsed.scheme == "https" else 6333),
        collection=settings.qdrant_collection,
        embedding_deployment=settings.embedding_deployment,
        embedding_provider=settings.embedding_provider,
        embedding_base_url=settings.embedding_base_url,
        embedding_model=settings.embedding_model,
        embedding_model_id=settings.embedding_model_id,
        embedding_dimension=settings.embedding_dimension,
        embedding_batch_size=settings.embedding_batch_size,
        index_version=settings.index_version,
    )


class PostgresRuntimeServiceSettingsStore:
    """Stores administrator-managed AI and vector configuration.

    Provider credentials remain environment/file secrets. Only non-secret
    routing and index contract fields are persisted here.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._loaded = False
        self._initialize_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache = RuntimeServiceSettings(
            groq=RuntimeGroqSettings(
                enabled=bool(settings.groq_api_key),
                base_url=settings.groq_base_url,
                model=settings.groq_model,
                default_model_id=settings.default_model_id,
            ),
            vector=_vector_default(settings),
        )

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
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS runtime_service_settings (
                            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
                            groq_enabled BOOLEAN NOT NULL,
                            groq_base_url TEXT NOT NULL,
                            groq_model TEXT NOT NULL,
                            default_model_id TEXT NOT NULL,
                            vector_host TEXT NOT NULL,
                            vector_port INTEGER NOT NULL CHECK (
                                vector_port BETWEEN 1 AND 65535
                            ),
                            vector_collection TEXT NOT NULL,
                            embedding_model TEXT NOT NULL,
                            index_version TEXT NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    for column, definition in (
                        ("embedding_deployment", "TEXT"),
                        ("embedding_provider", "TEXT"),
                        ("embedding_base_url", "TEXT"),
                        ("embedding_model_id", "TEXT"),
                        ("embedding_dimension", "INTEGER"),
                        ("embedding_batch_size", "INTEGER"),
                    ):
                        connection.execute(
                            f"""
                            ALTER TABLE runtime_service_settings
                            ADD COLUMN IF NOT EXISTS {column} {definition}
                            """
                        )
                    defaults = _vector_default(self._settings)
                    connection.execute(
                        """
                        UPDATE runtime_service_settings
                        SET embedding_deployment = COALESCE(
                                embedding_deployment, %s
                            ),
                            embedding_provider = COALESCE(
                                embedding_provider, %s
                            ),
                            embedding_base_url = COALESCE(
                                embedding_base_url, %s
                            ),
                            embedding_model_id = COALESCE(
                                embedding_model_id, embedding_model, %s
                            ),
                            embedding_dimension = COALESCE(
                                embedding_dimension, %s
                            ),
                            embedding_batch_size = COALESCE(
                                embedding_batch_size, %s
                            )
                        WHERE singleton = TRUE
                        """,
                        (
                            defaults.embedding_deployment,
                            defaults.embedding_provider,
                            defaults.embedding_base_url,
                            defaults.embedding_model_id,
                            defaults.embedding_dimension,
                            defaults.embedding_batch_size,
                        ),
                    )
                self._initialized = True
            except (psycopg.Error, OSError) as exc:
                raise RuntimeServiceSettingsError(
                    "PostgreSQL runtime service settings are unavailable"
                ) from exc

    @staticmethod
    def _row_to_settings(row: dict[str, Any]) -> RuntimeServiceSettings:
        return RuntimeServiceSettings(
            groq=RuntimeGroqSettings(
                enabled=bool(row["groq_enabled"]),
                base_url=str(row["groq_base_url"]).rstrip("/"),
                model=str(row["groq_model"]),
                default_model_id=str(row["default_model_id"]),
            ),
            vector=RuntimeVectorSettings(
                host=str(row["vector_host"]),
                port=int(row["vector_port"]),
                collection=str(row["vector_collection"]),
                embedding_deployment=str(row["embedding_deployment"]),
                embedding_provider=str(row["embedding_provider"]),
                embedding_base_url=str(row["embedding_base_url"]).rstrip("/"),
                embedding_model=str(row["embedding_model"]),
                embedding_model_id=str(row["embedding_model_id"]),
                embedding_dimension=int(row["embedding_dimension"]),
                embedding_batch_size=int(row["embedding_batch_size"]),
                index_version=str(row["index_version"]),
            ),
            updated_at=row["updated_at"],
        )

    def get(self, *, refresh: bool = False) -> RuntimeServiceSettings:
        if not refresh and self._loaded:
            with self._cache_lock:
                return self._cache
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT groq_enabled, groq_base_url, groq_model,
                           default_model_id, vector_host, vector_port,
                           vector_collection, embedding_deployment,
                           embedding_provider, embedding_base_url,
                           embedding_model, embedding_model_id,
                           embedding_dimension,
                           embedding_batch_size, index_version,
                           updated_at
                    FROM runtime_service_settings
                    WHERE singleton = TRUE
                    """
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RuntimeServiceSettingsError(
                "PostgreSQL runtime service settings read failed"
            ) from exc
        if row is not None:
            resolved = self._row_to_settings(row)
            with self._cache_lock:
                self._cache = resolved
        self._loaded = True
        with self._cache_lock:
            return self._cache

    def cached(self) -> RuntimeServiceSettings:
        with self._cache_lock:
            return self._cache

    def groq_settings(self) -> RuntimeGroqSettings:
        try:
            return self.get(refresh=True).groq
        except RuntimeServiceSettingsError:
            return self.cached().groq

    def update(
        self,
        *,
        groq_enabled: bool,
        groq_base_url: str,
        groq_model: str,
        default_model_id: str,
        vector_host: str,
        vector_port: int,
        vector_collection: str,
        embedding_deployment: str,
        embedding_provider: str,
        embedding_base_url: str,
        embedding_model: str,
        embedding_model_id: str,
        embedding_dimension: int,
        embedding_batch_size: int,
        index_version: str,
    ) -> RuntimeServiceSettings:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO runtime_service_settings (
                        singleton, groq_enabled, groq_base_url, groq_model,
                        default_model_id, vector_host, vector_port,
                        vector_collection, embedding_deployment,
                        embedding_provider, embedding_base_url,
                        embedding_model, embedding_model_id,
                        embedding_dimension,
                        embedding_batch_size, index_version, updated_at
                    ) VALUES (
                        TRUE, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (singleton)
                    DO UPDATE SET
                        groq_enabled = EXCLUDED.groq_enabled,
                        groq_base_url = EXCLUDED.groq_base_url,
                        groq_model = EXCLUDED.groq_model,
                        default_model_id = EXCLUDED.default_model_id,
                        vector_host = EXCLUDED.vector_host,
                        vector_port = EXCLUDED.vector_port,
                        vector_collection = EXCLUDED.vector_collection,
                        embedding_deployment = EXCLUDED.embedding_deployment,
                        embedding_provider = EXCLUDED.embedding_provider,
                        embedding_base_url = EXCLUDED.embedding_base_url,
                        embedding_model = EXCLUDED.embedding_model,
                        embedding_model_id = EXCLUDED.embedding_model_id,
                        embedding_dimension = EXCLUDED.embedding_dimension,
                        embedding_batch_size = EXCLUDED.embedding_batch_size,
                        index_version = EXCLUDED.index_version,
                        updated_at = NOW()
                    RETURNING groq_enabled, groq_base_url, groq_model,
                              default_model_id, vector_host, vector_port,
                              vector_collection, embedding_deployment,
                              embedding_provider, embedding_base_url,
                              embedding_model, embedding_model_id,
                              embedding_dimension,
                              embedding_batch_size, index_version, updated_at
                    """,
                    (
                        groq_enabled,
                        groq_base_url.rstrip("/"),
                        groq_model,
                        default_model_id,
                        vector_host,
                        vector_port,
                        vector_collection,
                        embedding_deployment,
                        embedding_provider,
                        embedding_base_url.rstrip("/"),
                        embedding_model,
                        embedding_model_id,
                        embedding_dimension,
                        embedding_batch_size,
                        index_version,
                    ),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RuntimeServiceSettingsError(
                "PostgreSQL runtime service settings update failed"
            ) from exc
        if row is None:
            raise RuntimeServiceSettingsError(
                "PostgreSQL did not return updated runtime service settings"
            )
        resolved = self._row_to_settings(row)
        with self._cache_lock:
            self._cache = resolved
        self._loaded = True
        return resolved

    def effective_settings(self, base: Settings) -> Settings:
        try:
            runtime = self.get(refresh=True)
        except RuntimeServiceSettingsError:
            runtime = self.cached()
        return replace(
            base,
            qdrant_url=runtime.vector.url,
            qdrant_collection=runtime.vector.collection,
            embedding_provider=runtime.vector.embedding_provider,
            embedding_base_url=runtime.vector.embedding_base_url,
            embedding_model=runtime.vector.embedding_model,
            embedding_model_id=runtime.vector.embedding_model_id,
            embedding_deployment=runtime.vector.embedding_deployment,
            embedding_dimension=runtime.vector.embedding_dimension,
            embedding_batch_size=runtime.vector.embedding_batch_size,
            index_version=runtime.vector.index_version,
            groq_base_url=runtime.groq.base_url,
            groq_model=runtime.groq.model,
            default_model_id=runtime.groq.default_model_id,
        )
