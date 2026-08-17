from __future__ import annotations

import ipaddress
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import Settings
from .schema_guard import SchemaStateError, require_schema


class RuntimeNetworkSettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class NetworkEndpoint:
    ip: str
    port: int

    @property
    def http_base_url(self) -> str:
        return f"http://{self.ip}:{self.port}"


@dataclass(frozen=True)
class RuntimeNetworkSettings:
    frontend: NetworkEndpoint
    backendai: NetworkEndpoint
    updated_at: datetime | None = None



class PostgresRuntimeNetworkSettingsStore:
    """Persists administrator-controlled service endpoints with env fallbacks."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._loaded = False
        self._initialize_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        # Missing administrator rows are not substituted with deployment values.
        self._cache: RuntimeNetworkSettings | None = None

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
                raise RuntimeNetworkSettingsError(
                    "PostgreSQL schema is not on the required Alembic baseline"
                ) from exc

    @staticmethod
    def _row_to_settings(row: dict[str, Any]) -> RuntimeNetworkSettings:
        return RuntimeNetworkSettings(
            frontend=NetworkEndpoint(
                str(row["frontend_ip"]),
                int(row["frontend_port"]),
            ),
            backendai=NetworkEndpoint(
                str(row["backendai_ip"]),
                int(row["backendai_port"]),
            ),
            updated_at=row["updated_at"],
        )

    def get(self, *, refresh: bool = False) -> RuntimeNetworkSettings | None:
        if not refresh and self._loaded:
            with self._cache_lock:
                return self._cache
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT frontend_ip, frontend_port, backendai_ip,
                           backendai_port, updated_at
                    FROM runtime_network_settings
                    WHERE singleton = TRUE
                    """
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RuntimeNetworkSettingsError(
                "PostgreSQL runtime network settings read failed"
            ) from exc
        resolved = self._row_to_settings(row) if row is not None else None
        with self._cache_lock:
            self._cache = resolved
            self._loaded = True
            return self._cache

    def cached(self) -> RuntimeNetworkSettings | None:
        with self._cache_lock:
            return self._cache

    def configured(self, *, refresh: bool = False) -> bool:
        value = self.get(refresh=refresh)
        return bool(
            value is not None
            and value.backendai.ip
            and value.backendai.port > 0
        )

    def update(
        self,
        *,
        frontend_ip: str,
        frontend_port: int,
        backendai_ip: str,
        backendai_port: int,
    ) -> RuntimeNetworkSettings:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO runtime_network_settings (
                        singleton, frontend_ip, frontend_port,
                        backendai_ip, backendai_port, updated_at
                    ) VALUES (TRUE, %s, %s, %s, %s, NOW())
                    ON CONFLICT (singleton)
                    DO UPDATE SET
                        frontend_ip = EXCLUDED.frontend_ip,
                        frontend_port = EXCLUDED.frontend_port,
                        backendai_ip = EXCLUDED.backendai_ip,
                        backendai_port = EXCLUDED.backendai_port,
                        updated_at = NOW()
                    RETURNING frontend_ip, frontend_port, backendai_ip,
                              backendai_port, updated_at
                    """,
                    (
                        frontend_ip,
                        frontend_port,
                        backendai_ip,
                        backendai_port,
                    ),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise RuntimeNetworkSettingsError(
                "PostgreSQL runtime network settings update failed"
            ) from exc
        if row is None:
            raise RuntimeNetworkSettingsError(
                "PostgreSQL did not return the updated network settings"
            )
        resolved = self._row_to_settings(row)
        with self._cache_lock:
            self._cache = resolved
        self._loaded = True
        return resolved

    def backendai_base_url(self) -> str:
        try:
            value = self.get(refresh=True)
        except RuntimeNetworkSettingsError:
            value = self.cached()
        return value.backendai.http_base_url if value is not None else ""


    def probe_frontend(self, timeout_seconds: float = 2.0) -> dict[str, Any]:
        try:
            value = self.get(refresh=True)
        except RuntimeNetworkSettingsError:
            value = self.cached()
        if value is None:
            return {"reachable": False, "latency_ms": 0, "error": "not_configured"}
        endpoint = value.frontend
        started_at = perf_counter()
        try:
            with socket.create_connection(
                (endpoint.ip, endpoint.port),
                timeout=timeout_seconds,
            ):
                pass
            return {
                "reachable": True,
                "latency_ms": max(1, round((perf_counter() - started_at) * 1000)),
                "error": None,
            }
        except (OSError, TimeoutError, ValueError):
            return {
                "reachable": False,
                "latency_ms": max(1, round((perf_counter() - started_at) * 1000)),
                "error": "unreachable",
            }


def validate_runtime_ip(value: str) -> str:
    address = ipaddress.ip_address(value.strip())
    if address.version != 4:
        raise ValueError("IPv4 address is required")
    if address.is_unspecified or address.is_multicast:
        raise ValueError("unspecified and multicast addresses are not allowed")
    return str(address)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
