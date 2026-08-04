from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .config import Settings


class VectorDatabaseRegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class VectorDatabaseProvider:
    provider_id: str
    name: str
    engine: str
    detected_engine: str | None
    connection_mode: str
    host: str
    port: int
    use_tls: bool
    storage_namespace: str
    local_path: str | None
    embedding_model_path: str
    embedding_models: list[str]
    enabled: bool
    status: str
    collections: list[str]
    adapter_available: bool
    error: str | None
    latency_ms: int
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def base_url(self) -> str:
        if self.connection_mode == "local":
            return f"file:{self.local_path or ''}"
        return f"{'https' if self.use_tls else 'http'}://{self.host}:{self.port}"


@dataclass(frozen=True)
class VectorDatabaseProbeResult:
    status: str
    detected_engine: str | None
    collections: list[str] = field(default_factory=list)
    adapter_available: bool = False
    latency_ms: int = 0
    error: str | None = None


class PostgresVectorDatabaseRegistry:
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
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS vector_database_providers (
                            provider_id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            engine TEXT NOT NULL,
                            detected_engine TEXT,
                            connection_mode TEXT NOT NULL DEFAULT 'remote',
                            host TEXT NOT NULL,
                            port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
                            use_tls BOOLEAN NOT NULL DEFAULT FALSE,
                            storage_namespace TEXT NOT NULL,
                            local_path TEXT,
                            embedding_model_path TEXT NOT NULL,
                            embedding_models_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                            enabled BOOLEAN NOT NULL DEFAULT TRUE,
                            status TEXT NOT NULL DEFAULT 'unknown',
                            collections_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                            adapter_available BOOLEAN NOT NULL DEFAULT FALSE,
                            error TEXT,
                            latency_ms INTEGER NOT NULL DEFAULT 0,
                            last_checked_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            UNIQUE (host, port, storage_namespace)
                        )
                        """
                    )
                    connection.execute(
                        """
                        ALTER TABLE vector_database_providers
                        ADD COLUMN IF NOT EXISTS connection_mode TEXT NOT NULL DEFAULT 'remote'
                        """
                    )
                    connection.execute(
                        """
                        ALTER TABLE vector_database_providers
                        ADD COLUMN IF NOT EXISTS local_path TEXT
                        """
                    )
                self._initialized = True
            except (psycopg.Error, OSError) as exc:
                raise VectorDatabaseRegistryError(
                    "VectorDB provider schema initialization failed"
                ) from exc

    @staticmethod
    def _record(row: dict[str, Any]) -> VectorDatabaseProvider:
        return VectorDatabaseProvider(
            provider_id=str(row["provider_id"]),
            name=str(row["name"]),
            engine=str(row["engine"]),
            detected_engine=(
                str(row["detected_engine"]) if row.get("detected_engine") else None
            ),
            connection_mode=str(row.get("connection_mode") or "remote"),
            host=str(row["host"]),
            port=int(row["port"]),
            use_tls=bool(row["use_tls"]),
            storage_namespace=str(row["storage_namespace"]),
            local_path=str(row["local_path"]) if row.get("local_path") else None,
            embedding_model_path=str(row["embedding_model_path"]),
            embedding_models=list(row.get("embedding_models_json") or []),
            enabled=bool(row["enabled"]),
            status=str(row["status"]),
            collections=list(row.get("collections_json") or []),
            adapter_available=bool(row["adapter_available"]),
            error=str(row["error"]) if row.get("error") else None,
            latency_ms=int(row["latency_ms"]),
            last_checked_at=row.get("last_checked_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _returning_sql() -> str:
        return """
            RETURNING provider_id, name, engine, detected_engine,
                      connection_mode, host, port, use_tls, storage_namespace,
                      local_path, embedding_model_path,
                      embedding_models_json, enabled, status, collections_json,
                      adapter_available, error, latency_ms, last_checked_at,
                      created_at, updated_at
        """

    def list(self) -> list[VectorDatabaseProvider]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT provider_id, name, engine, detected_engine,
                           connection_mode, host, port, use_tls,
                           storage_namespace, local_path, embedding_model_path,
                           embedding_models_json, enabled, status,
                           collections_json, adapter_available, error, latency_ms,
                           last_checked_at, created_at, updated_at
                    FROM vector_database_providers
                    ORDER BY created_at DESC, provider_id
                    """
                ).fetchall()
            return [self._record(row) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise VectorDatabaseRegistryError("VectorDB provider list failed") from exc

    def get(self, provider_id: str) -> VectorDatabaseProvider | None:
        return next(
            (item for item in self.list() if item.provider_id == provider_id),
            None,
        )

    def create(
        self,
        *,
        name: str,
        engine: str,
        connection_mode: str,
        host: str,
        port: int,
        use_tls: bool,
        storage_namespace: str,
        local_path: str | None,
        embedding_model_path: str,
        embedding_models: list[str],
        enabled: bool,
    ) -> VectorDatabaseProvider:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    INSERT INTO vector_database_providers (
                        provider_id, name, engine, connection_mode, host, port,
                        use_tls, storage_namespace, local_path, embedding_model_path,
                        embedding_models_json, enabled
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    {self._returning_sql()}
                    """,
                    (
                        f"vdb_{uuid4().hex}", name, engine, connection_mode,
                        host, port, use_tls, storage_namespace, local_path,
                        embedding_model_path,
                        json.dumps(embedding_models), enabled,
                    ),
                ).fetchone()
            if row is None:
                raise VectorDatabaseRegistryError("VectorDB provider insert returned no row")
            return self._record(row)
        except psycopg.errors.UniqueViolation as exc:
            raise VectorDatabaseRegistryError(
                "The same VectorDB endpoint and namespace is already registered"
            ) from exc
        except (psycopg.Error, OSError) as exc:
            raise VectorDatabaseRegistryError("VectorDB provider create failed") from exc

    def update(
        self,
        provider_id: str,
        *,
        name: str,
        engine: str,
        connection_mode: str,
        host: str,
        port: int,
        use_tls: bool,
        storage_namespace: str,
        local_path: str | None,
        embedding_model_path: str,
        embedding_models: list[str],
        enabled: bool,
    ) -> VectorDatabaseProvider | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    UPDATE vector_database_providers
                    SET name=%s, engine=%s, detected_engine=NULL,
                        connection_mode=%s, host=%s, port=%s, use_tls=%s,
                        storage_namespace=%s, local_path=%s,
                        embedding_model_path=%s, embedding_models_json=%s::jsonb,
                        enabled=%s, status='unknown', collections_json='[]'::jsonb,
                        adapter_available=FALSE, error=NULL, latency_ms=0,
                        last_checked_at=NULL, updated_at=NOW()
                    WHERE provider_id=%s
                    {self._returning_sql()}
                    """,
                    (
                        name, engine, connection_mode, host, port, use_tls,
                        storage_namespace, local_path, embedding_model_path,
                        json.dumps(embedding_models), enabled,
                        provider_id,
                    ),
                ).fetchone()
            return self._record(row) if row else None
        except psycopg.errors.UniqueViolation as exc:
            raise VectorDatabaseRegistryError(
                "The same VectorDB endpoint and namespace is already registered"
            ) from exc
        except (psycopg.Error, OSError) as exc:
            raise VectorDatabaseRegistryError("VectorDB provider update failed") from exc

    def update_probe(
        self,
        provider_id: str,
        result: VectorDatabaseProbeResult,
    ) -> VectorDatabaseProvider:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    UPDATE vector_database_providers
                    SET detected_engine=%s, status=%s, collections_json=%s::jsonb,
                        adapter_available=%s, error=%s, latency_ms=%s,
                        last_checked_at=NOW(), updated_at=NOW()
                    WHERE provider_id=%s
                    {self._returning_sql()}
                    """,
                    (
                        result.detected_engine, result.status,
                        json.dumps(result.collections), result.adapter_available,
                        result.error, result.latency_ms, provider_id,
                    ),
                ).fetchone()
            if row is None:
                raise VectorDatabaseRegistryError("VectorDB provider was not found")
            return self._record(row)
        except (psycopg.Error, OSError) as exc:
            raise VectorDatabaseRegistryError("VectorDB probe update failed") from exc

    def delete(self, provider_id: str) -> bool:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM vector_database_providers WHERE provider_id=%s",
                    (provider_id,),
                )
            return bool(cursor.rowcount)
        except (psycopg.Error, OSError) as exc:
            raise VectorDatabaseRegistryError("VectorDB provider delete failed") from exc


class VectorDatabaseDetector:
    HTTP_ENGINES = ("rag_lab", "qdrant", "weaviate", "chroma", "milvus")

    @staticmethod
    def _request(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float = 2,
        api_key: str = "",
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if api_key:
            headers["api-key"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"
            headers["x-chroma-token"] = api_key
            headers["X-VSS-Token"] = api_key
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}{path}",
            data=(json.dumps(payload).encode("utf-8") if payload is not None else None),
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
                if not body:
                    return response.status, {}
                try:
                    return response.status, json.loads(body)
                except json.JSONDecodeError:
                    return response.status, body
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, ValueError):
            return 0, None

    def probe(
        self,
        provider: VectorDatabaseProvider,
        *,
        api_key: str = "",
    ) -> VectorDatabaseProbeResult:
        started_at = perf_counter()
        if not provider.enabled:
            return VectorDatabaseProbeResult("disabled", None, error="disabled")
        if provider.connection_mode == "local":
            return self._probe_local(provider, started_at)
        requested = provider.engine
        if requested == "pgvector":
            return VectorDatabaseProbeResult(
                "degraded",
                requested,
                adapter_available=False,
                latency_ms=0,
                error=f"{requested}_adapter_not_implemented",
            )
        if requested == "auto":
            engines = self.HTTP_ENGINES
        elif requested == "custom":
            engines = ("rag_lab",)
        elif requested == "rag_lab":
            engines = ("rag_lab",)
        else:
            # A remote service may hide its storage engine behind the rag_lab
            # contract. Fall back to that protocol after the selected raw DB API.
            engines = (requested, "rag_lab")
        for engine in engines:
            detected = self._probe_engine(provider.base_url, engine, api_key=api_key)
            if detected is not None:
                collections = sorted(set(detected))
                return VectorDatabaseProbeResult(
                    "online",
                    engine,
                    collections=collections,
                    adapter_available=engine in {"qdrant", "rag_lab"},
                    latency_ms=round((perf_counter() - started_at) * 1000),
                )
        return VectorDatabaseProbeResult(
            "offline",
            None,
            latency_ms=round((perf_counter() - started_at) * 1000),
            error="vector_database_not_detected",
        )

    @staticmethod
    def _probe_local(
        provider: VectorDatabaseProvider,
        started_at: float,
    ) -> VectorDatabaseProbeResult:
        path = Path(provider.local_path or "")
        latency_ms = round((perf_counter() - started_at) * 1000)
        if not path.exists():
            if (
                provider.engine == "sqlite"
                and path.parent.exists()
                and path.parent.is_dir()
            ):
                return VectorDatabaseProbeResult(
                    "degraded",
                    "sqlite",
                    collections=[provider.storage_namespace],
                    adapter_available=True,
                    latency_ms=latency_ms,
                    error="local_sqlite_target_ready_not_created",
                )
            return VectorDatabaseProbeResult(
                "offline",
                None,
                latency_ms=latency_ms,
                error="local_path_not_accessible",
            )
        requested = provider.engine
        detected: str | None = None
        if requested == "sqlite" or path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            detected = "sqlite"
        elif requested == "chroma" or (path / "chroma.sqlite3").exists():
            detected = "chroma"
        elif requested == "qdrant" or (path / "collections").is_dir():
            detected = "qdrant"
        elif requested not in {"auto", "custom"}:
            detected = requested
        if detected is None:
            return VectorDatabaseProbeResult(
                "degraded",
                "custom" if requested == "auto" else requested,
                collections=[provider.storage_namespace],
                latency_ms=latency_ms,
                error="local_storage_format_not_detected",
            )
        return VectorDatabaseProbeResult(
            "online",
            detected,
            collections=[provider.storage_namespace],
            adapter_available=detected == "sqlite",
            latency_ms=latency_ms,
            error=(
                None
                if detected == "sqlite"
                else f"{detected}_local_storage_requires_server_adapter"
            ),
        )

    def _probe_engine(
        self,
        base_url: str,
        engine: str,
        *,
        api_key: str = "",
    ) -> list[str] | None:
        if engine == "rag_lab":
            status, health = self._request(
                base_url, "GET", "/health", api_key=api_key
            )
            if (
                status != 200
                or not isinstance(health, dict)
                or health.get("ok") is not True
                or not health.get("embed_model")
            ):
                return None
            projects_status, projects = self._request(
                base_url, "GET", "/projects", api_key=api_key
            )
            if projects_status != 200 or not isinstance(projects, dict):
                return [str(item) for item in health.get("projects", []) if item]
            return [
                str(item.get("project_id"))
                for item in projects.get("projects", [])
                if isinstance(item, dict) and item.get("project_id")
            ]
        if engine == "qdrant":
            status, payload = self._request(
                base_url, "GET", "/collections", api_key=api_key
            )
            if status != 200 or not isinstance(payload, dict) or "result" not in payload:
                return None
            return [
                str(item.get("name"))
                for item in payload.get("result", {}).get("collections", [])
                if isinstance(item, dict) and item.get("name")
            ]
        if engine == "weaviate":
            status, _ = self._request(
                base_url, "GET", "/v1/.well-known/ready", api_key=api_key
            )
            if status != 200:
                return None
            _, payload = self._request(
                base_url, "GET", "/v1/schema", api_key=api_key
            )
            return [
                str(item.get("class"))
                for item in (payload or {}).get("classes", [])
                if isinstance(item, dict) and item.get("class")
            ]
        if engine == "chroma":
            status, _ = self._request(
                base_url, "GET", "/api/v2/heartbeat", api_key=api_key
            )
            if status != 200:
                return None
            _, payload = self._request(
                base_url,
                "GET",
                "/api/v2/tenants/default_tenant/databases/default_database/collections",
                api_key=api_key,
            )
            return [
                str(item.get("name"))
                for item in payload if isinstance(payload, list) and isinstance(item, dict) and item.get("name")
            ]
        if engine == "milvus":
            status, payload = self._request(
                base_url,
                "POST",
                "/v2/vectordb/collections/list",
                {"dbName": "_default"},
                api_key=api_key,
            )
            if status != 200 or not isinstance(payload, dict) or payload.get("code") not in {0, 200}:
                return None
            data = payload.get("data", [])
            if isinstance(data, dict):
                data = data.get("collectionNames", [])
            return [str(item) for item in data if isinstance(item, str)]
        return None
