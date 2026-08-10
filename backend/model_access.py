from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import Settings
from .schema_guard import SchemaStateError, require_schema


class ModelAccessPolicyError(RuntimeError):
    pass


class PostgresModelAccessPolicyStore:
    """Administrator-managed API access policy for generation models."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._initialize_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache: dict[str, bool] = {}
        self._loaded = False

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
                raise ModelAccessPolicyError(
                    "PostgreSQL schema is not on the required P2 revision"
                ) from exc

    def _default_enabled(self, model_id: str) -> bool:
        if model_id.startswith(
            ("provider:", "backendai:", "nvidia:", "groq:")
        ):
            # Discovered models inherit the Provider's access state. They can
            # still be disabled individually by creating an explicit policy.
            return True
        return model_id in {
            self._settings.backendai_public_model_id,
            self._settings.nvidia_public_model_id,
            self._settings.groq_public_model_id
        }

    def _load(self, *, refresh: bool = False) -> None:
        if self._loaded and not refresh:
            return
        self._ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT model_id, enabled
                    FROM model_access_policies
                    """
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise ModelAccessPolicyError(
                "Model access policy read failed"
            ) from exc
        with self._cache_lock:
            self._cache = {
                str(row["model_id"]): bool(row["enabled"]) for row in rows
            }
            self._loaded = True

    def is_enabled(self, model_id: str) -> bool:
        try:
            # PostgreSQL is the cross-replica source of truth. Refresh on every
            # authorization check so an administrator toggle applies to all
            # API instances immediately.
            self._load(refresh=True)
        except ModelAccessPolicyError:
            with self._cache_lock:
                cached = self._cache.get(model_id)
            return self._default_enabled(model_id) if cached is None else cached
        with self._cache_lock:
            value = self._cache.get(model_id)
        return self._default_enabled(model_id) if value is None else value

    def set_enabled(self, model_id: str, enabled: bool) -> dict[str, Any]:
        normalized = model_id.strip()
        if not normalized:
            raise ModelAccessPolicyError("model_id must not be blank")
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO model_access_policies (
                        model_id, enabled, updated_at
                    ) VALUES (%s, %s, NOW())
                    ON CONFLICT (model_id) DO UPDATE SET
                        enabled = EXCLUDED.enabled,
                        updated_at = NOW()
                    RETURNING model_id, enabled, updated_at
                    """,
                    (normalized, enabled),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise ModelAccessPolicyError(
                "Model access policy update failed"
            ) from exc
        if row is None:
            raise ModelAccessPolicyError(
                "PostgreSQL did not return updated model access policy"
            )
        with self._cache_lock:
            self._cache[normalized] = bool(enabled)
            self._loaded = True
        return row

    def updated_at(self, model_id: str) -> datetime | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT updated_at FROM model_access_policies
                    WHERE model_id = %s
                    """,
                    (model_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise ModelAccessPolicyError(
                "Model access policy timestamp lookup failed"
            ) from exc
        return row["updated_at"] if row else None
