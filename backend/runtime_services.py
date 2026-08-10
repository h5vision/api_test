from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import Settings
from .schema_guard import SchemaStateError, require_schema


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
    vector_target_id: str
    embedding_profile_id: str
    collection: str
    index_version: str


@dataclass(frozen=True)
class RuntimeServiceSettings:
    groq: RuntimeGroqSettings
    vector: RuntimeVectorSettings
    updated_at: datetime | None = None


class PostgresRuntimeServiceSettingsStore:
    """Stores selected runtime identities, not physical target/profile details.

    P2-C promotes physical vector endpoints to ``vector_targets``. P2-D promotes
    embedding execution/vector-space details to ``embedding_profiles``. The
    singleton now selects those persistent identities and temporarily retains
    collection/index_version until P2-E promotes VectorIndex.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._loaded = False
        self._initialize_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache: RuntimeServiceSettings | None = None

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
                raise RuntimeServiceSettingsError(
                    "PostgreSQL schema is not on the required Alembic revision"
                ) from exc

    @staticmethod
    def _row_to_settings(row: dict[str, Any]) -> RuntimeServiceSettings:
        return RuntimeServiceSettings(
            groq=RuntimeGroqSettings(
                enabled=bool(row["groq_enabled"]),
                base_url=str(row.get("groq_base_url") or "").rstrip("/"),
                model=str(row.get("groq_model") or ""),
                default_model_id=str(row.get("default_model_id") or ""),
            ),
            vector=RuntimeVectorSettings(
                vector_target_id=str(row.get("vector_target_id") or ""),
                embedding_profile_id=str(row.get("embedding_profile_id") or ""),
                collection=str(row.get("vector_collection") or ""),
                index_version=str(row.get("index_version") or ""),
            ),
            updated_at=row["updated_at"],
        )

    @staticmethod
    def is_complete(value: RuntimeServiceSettings | None) -> bool:
        if value is None:
            return False
        vector = value.vector
        return bool(
            vector.vector_target_id
            and vector.embedding_profile_id
            and vector.collection
            and vector.index_version
            and value.groq.default_model_id
        )

    def get(self, *, refresh: bool = False) -> RuntimeServiceSettings | None:
        if not refresh and self._loaded:
            with self._cache_lock:
                return self._cache
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT groq_enabled, groq_base_url, groq_model,
                           default_model_id, vector_target_id, embedding_profile_id,
                           vector_collection, index_version, updated_at
                    FROM runtime_service_settings
                    WHERE singleton = TRUE
                    """
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RuntimeServiceSettingsError(
                "PostgreSQL runtime service settings read failed"
            ) from exc
        resolved = self._row_to_settings(row) if row is not None else None
        with self._cache_lock:
            self._cache = resolved
            self._loaded = True
            return self._cache

    def cached(self) -> RuntimeServiceSettings | None:
        with self._cache_lock:
            return self._cache

    def configured(self, *, refresh: bool = False) -> bool:
        return self.is_complete(self.get(refresh=refresh))

    def groq_settings(self) -> RuntimeGroqSettings:
        try:
            runtime = self.get(refresh=True)
        except RuntimeServiceSettingsError:
            runtime = self.cached()
        if runtime is None:
            return RuntimeGroqSettings(False, "", "", "")
        return runtime.groq

    def update(
        self,
        *,
        groq_enabled: bool,
        groq_base_url: str,
        groq_model: str,
        default_model_id: str,
        vector_target_id: str,
        embedding_profile_id: str,
        vector_collection: str,
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
                        vector_target_id, embedding_profile_id, vector_collection,
                        embedding_deployment, embedding_provider, embedding_base_url,
                        embedding_model, embedding_model_id, embedding_dimension,
                        embedding_batch_size, index_version, updated_at
                    ) VALUES (
                        TRUE, %s, %s, %s, %s, NULL, NULL,
                        %s, %s, %s, NULL, NULL, NULL, NULL, NULL, NULL, NULL, %s, NOW()
                    )
                    ON CONFLICT (singleton)
                    DO UPDATE SET
                        groq_enabled = EXCLUDED.groq_enabled,
                        groq_base_url = EXCLUDED.groq_base_url,
                        groq_model = EXCLUDED.groq_model,
                        default_model_id = EXCLUDED.default_model_id,
                        vector_target_id = EXCLUDED.vector_target_id,
                        embedding_profile_id = EXCLUDED.embedding_profile_id,
                        vector_collection = EXCLUDED.vector_collection,
                        embedding_deployment = NULL,
                        embedding_provider = NULL,
                        embedding_base_url = NULL,
                        embedding_model = NULL,
                        embedding_model_id = NULL,
                        embedding_dimension = NULL,
                        embedding_batch_size = NULL,
                        index_version = EXCLUDED.index_version,
                        updated_at = NOW()
                    RETURNING groq_enabled, groq_base_url, groq_model,
                              default_model_id, vector_target_id, embedding_profile_id,
                              vector_collection, index_version, updated_at
                    """,
                    (
                        groq_enabled,
                        groq_base_url.rstrip("/"),
                        groq_model,
                        default_model_id,
                        vector_target_id,
                        embedding_profile_id,
                        vector_collection,
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

    def _select_identity(self, column: str, value: str, label: str) -> RuntimeServiceSettings:
        if column not in {"vector_target_id", "embedding_profile_id"}:
            raise ValueError("unsupported runtime identity column")
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    UPDATE runtime_service_settings
                    SET {column} = %s, updated_at = NOW()
                    WHERE singleton = TRUE
                    RETURNING groq_enabled, groq_base_url, groq_model,
                              default_model_id, vector_target_id, embedding_profile_id,
                              vector_collection, index_version, updated_at
                    """,
                    (value,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RuntimeServiceSettingsError(f"{label} selection update failed") from exc
        if row is None:
            raise RuntimeServiceSettingsError(
                f"Runtime service settings must be created before selecting a {label}"
            )
        resolved = self._row_to_settings(row)
        with self._cache_lock:
            self._cache = resolved
            self._loaded = True
        return resolved

    def select_vector_target(self, vector_target_id: str) -> RuntimeServiceSettings:
        return self._select_identity("vector_target_id", vector_target_id, "VectorTarget")

    def select_embedding_profile(self, embedding_profile_id: str) -> RuntimeServiceSettings:
        return self._select_identity(
            "embedding_profile_id", embedding_profile_id, "EmbeddingProfile"
        )

    def effective_settings(self, base: Settings) -> Settings:
        """Compatibility helper for non-registry fields only.

        Canonical target/profile details are resolved by RuntimeSettingsResolver.
        """
        runtime = self.get(refresh=True)
        if not self.is_complete(runtime):
            raise RuntimeServiceSettingsError(
                "Administrator runtime service settings are not configured"
            )
        assert runtime is not None
        return replace(
            base,
            vector_target_id=runtime.vector.vector_target_id,
            embedding_profile_id=runtime.vector.embedding_profile_id,
            qdrant_collection=runtime.vector.collection,
            index_version=runtime.vector.index_version,
            groq_base_url=runtime.groq.base_url,
            groq_model=runtime.groq.model,
            default_model_id=runtime.groq.default_model_id,
        )
