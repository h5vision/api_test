from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .config import Settings


class FrontendClientStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class FrontendClient:
    client_id: str
    instance_id: str | None
    name: str
    ip: str
    port: int
    enabled: bool
    registration_type: str
    last_seen_ip: str | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class FrontendAccessDecision:
    allowed: bool
    reason: str
    client: FrontendClient | None = None
    auto_registered: bool = False


class PostgresFrontendClientStore:
    """Administrator-managed VS Code client registry and access switch."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._initialize_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cached_clients: tuple[FrontendClient, ...] = ()
        self._cache_loaded_at = 0.0

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
                    existed = bool(
                        connection.execute(
                            "SELECT to_regclass('public.frontend_clients') IS NOT NULL AS exists"
                        ).fetchone()["exists"]
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS frontend_clients (
                            client_id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            ip INET NOT NULL,
                            port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
                            enabled BOOLEAN NOT NULL DEFAULT TRUE,
                            instance_id TEXT,
                            registration_type TEXT NOT NULL DEFAULT 'admin',
                            last_seen_ip INET,
                            last_seen_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    connection.execute(
                        """
                        ALTER TABLE frontend_clients
                        ADD COLUMN IF NOT EXISTS instance_id TEXT
                        """
                    )
                    connection.execute(
                        """
                        ALTER TABLE frontend_clients
                        ADD COLUMN IF NOT EXISTS registration_type TEXT
                        NOT NULL DEFAULT 'admin'
                        """
                    )
                    connection.execute(
                        """
                        ALTER TABLE frontend_clients
                        ADD COLUMN IF NOT EXISTS last_seen_ip INET
                        """
                    )
                    connection.execute(
                        """
                        ALTER TABLE frontend_clients
                        ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ
                        """
                    )
                    connection.execute(
                        """
                        ALTER TABLE frontend_clients
                        DROP CONSTRAINT IF EXISTS frontend_clients_ip_port_key
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_frontend_clients_enabled_ip
                        ON frontend_clients (enabled, ip)
                        """
                    )
                    connection.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS
                        uq_frontend_clients_instance_id
                        ON frontend_clients (instance_id)
                        WHERE instance_id IS NOT NULL
                        """
                    )
                    if not existed:
                        connection.execute(
                            """
                            INSERT INTO frontend_clients (
                                client_id, name, ip, port, enabled
                            ) VALUES (%s, %s, %s, %s, TRUE)
                            ON CONFLICT DO NOTHING
                            """,
                            (
                                "frontend-primary",
                                "Primary VS Code Frontend",
                                self._settings.frontend_host,
                                self._settings.frontend_port,
                            ),
                        )
                self._initialized = True
            except (psycopg.Error, OSError, KeyError, TypeError) as exc:
                raise FrontendClientStoreError(
                    "PostgreSQL frontend client registry is unavailable"
                ) from exc

    @staticmethod
    def _from_row(row: dict[str, Any]) -> FrontendClient:
        return FrontendClient(
            client_id=str(row["client_id"]),
            instance_id=(
                str(row["instance_id"]) if row.get("instance_id") else None
            ),
            name=str(row["name"]),
            ip=str(row["ip"]),
            port=int(row["port"]),
            enabled=bool(row["enabled"]),
            registration_type=str(row.get("registration_type") or "admin"),
            last_seen_ip=(
                str(row["last_seen_ip"]) if row.get("last_seen_ip") else None
            ),
            last_seen_at=row.get("last_seen_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _invalidate(self) -> None:
        with self._cache_lock:
            self._cached_clients = ()
            self._cache_loaded_at = 0.0

    def list(self, *, refresh: bool = False) -> list[FrontendClient]:
        now = monotonic()
        with self._cache_lock:
            if (
                not refresh
                and self._cached_clients
                and now - self._cache_loaded_at < 5
            ):
                return list(self._cached_clients)
        self._ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT client_id, instance_id, name, ip, port, enabled,
                           registration_type, last_seen_ip, last_seen_at,
                           created_at, updated_at
                    FROM frontend_clients
                    ORDER BY created_at, client_id
                    """
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise FrontendClientStoreError(
                "Frontend client registry read failed"
            ) from exc
        clients = tuple(self._from_row(row) for row in rows)
        with self._cache_lock:
            self._cached_clients = clients
            self._cache_loaded_at = monotonic()
        return list(clients)

    def create(
        self,
        *,
        name: str,
        ip: str,
        port: int,
        enabled: bool,
    ) -> FrontendClient:
        self._ensure_schema()
        client_id = f"fcli_{uuid4().hex}"
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO frontend_clients (
                        client_id, instance_id, name, ip, port, enabled,
                        registration_type
                    ) VALUES (%s, NULL, %s, %s, %s, %s, 'admin')
                    RETURNING client_id, instance_id, name, ip, port, enabled,
                              registration_type, last_seen_ip, last_seen_at,
                              created_at, updated_at
                    """,
                    (client_id, name, ip, port, enabled),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise FrontendClientStoreError(
                "Frontend client creation failed"
            ) from exc
        if row is None:
            raise FrontendClientStoreError(
                "PostgreSQL did not return the created frontend client"
            )
        self._invalidate()
        return self._from_row(row)

    def update(
        self,
        client_id: str,
        *,
        name: str,
        ip: str,
        port: int,
        enabled: bool,
    ) -> FrontendClient | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE frontend_clients
                    SET name = %s,
                        ip = %s,
                        port = %s,
                        enabled = %s,
                        updated_at = NOW()
                    WHERE client_id = %s
                    RETURNING client_id, instance_id, name, ip, port, enabled,
                              registration_type, last_seen_ip, last_seen_at,
                              created_at, updated_at
                    """,
                    (name, ip, port, enabled, client_id),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise FrontendClientStoreError(
                "Frontend client update failed"
            ) from exc
        self._invalidate()
        return self._from_row(row) if row is not None else None

    def delete(self, client_id: str) -> bool:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "DELETE FROM frontend_clients WHERE client_id = %s RETURNING client_id",
                    (client_id,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise FrontendClientStoreError(
                "Frontend client deletion failed"
            ) from exc
        self._invalidate()
        return row is not None

    def authorize_or_register(
        self,
        *,
        client_id: str | None,
        instance_id: str | None,
        source_ip: str | None,
        auto_register: bool = False,
        client_name: str | None = None,
    ) -> FrontendAccessDecision:
        """Authorize a client and atomically auto-enroll the first chat."""

        self._ensure_schema()
        normalized_client_id = (client_id or "").strip()
        normalized_instance_id = (instance_id or "").strip()[:255]
        normalized_source_ip = (source_ip or "").strip()
        if not normalized_source_ip:
            return FrontendAccessDecision(
                False,
                "frontend client source IP is unavailable",
            )
        try:
            with self._connect() as connection:
                row: dict[str, Any] | None = None
                reason = ""
                if normalized_client_id:
                    row = connection.execute(
                        """
                        SELECT * FROM frontend_clients
                        WHERE client_id = %s
                        FOR UPDATE
                        """,
                        (normalized_client_id,),
                    ).fetchone()
                    reason = "client_id"
                if row is None and normalized_instance_id:
                    row = connection.execute(
                        """
                        SELECT * FROM frontend_clients
                        WHERE instance_id = %s
                        FOR UPDATE
                        """,
                        (normalized_instance_id,),
                    ).fetchone()
                    reason = "instance_id"
                if row is None:
                    row = connection.execute(
                        """
                        SELECT * FROM frontend_clients
                        WHERE ip = %s
                        ORDER BY enabled DESC, updated_at DESC
                        LIMIT 1
                        FOR UPDATE
                        """,
                        (normalized_source_ip,),
                    ).fetchone()
                    reason = "source_ip"
                if row is not None:
                    client = self._from_row(row)
                    if not client.enabled:
                        return FrontendAccessDecision(
                            False,
                            "frontend client connection is disabled",
                            client,
                        )
                    updated = connection.execute(
                        """
                        UPDATE frontend_clients
                        SET last_seen_ip = %s,
                            last_seen_at = NOW(),
                            updated_at = NOW()
                        WHERE client_id = %s
                        RETURNING *
                        """,
                        (normalized_source_ip, client.client_id),
                    ).fetchone()
                    self._invalidate()
                    return FrontendAccessDecision(
                        True,
                        reason,
                        self._from_row(updated),
                    )
                if not auto_register:
                    return FrontendAccessDecision(
                        False,
                        "frontend client is not registered or enabled",
                    )
                effective_instance_id = (
                    normalized_instance_id
                    or f"legacy-ip:{normalized_source_ip}"
                )
                generated_id = f"fcli_{uuid4().hex}"
                display_name = (
                    (client_name or "").strip()[:80]
                    or f"VS Code · {normalized_source_ip}"
                )
                created = connection.execute(
                    """
                    INSERT INTO frontend_clients (
                        client_id, instance_id, name, ip, port, enabled,
                        registration_type, last_seen_ip, last_seen_at
                    ) VALUES (%s, %s, %s, %s, %s, TRUE, 'auto', %s, NOW())
                    ON CONFLICT (instance_id) WHERE instance_id IS NOT NULL
                    DO UPDATE SET
                        last_seen_ip = EXCLUDED.last_seen_ip,
                        last_seen_at = NOW(),
                        updated_at = NOW()
                    RETURNING *
                    """,
                    (
                        generated_id,
                        effective_instance_id,
                        display_name,
                        normalized_source_ip,
                        self._settings.frontend_port,
                        normalized_source_ip,
                    ),
                ).fetchone()
                client = self._from_row(created)
                if not client.enabled:
                    return FrontendAccessDecision(
                        False,
                        "frontend client connection is disabled",
                        client,
                    )
                self._invalidate()
                return FrontendAccessDecision(
                    True,
                    "auto_registered",
                    client,
                    auto_registered=True,
                )
        except (psycopg.Error, OSError, KeyError, TypeError) as exc:
            raise FrontendClientStoreError(
                "Frontend client authorization failed"
            ) from exc

    def authorize(
        self,
        *,
        client_id: str | None,
        source_ip: str | None,
    ) -> tuple[bool, str]:
        decision = self.authorize_or_register(
            client_id=client_id,
            instance_id=None,
            source_ip=source_ip,
        )
        return decision.allowed, decision.reason

    @staticmethod
    def connection_status(client: FrontendClient) -> dict[str, Any]:
        if client.last_seen_at is None:
            return {
                "reachable": False,
                "latency_ms": 0,
                "error": "never_seen",
            }
        now = datetime.now(timezone.utc)
        last_seen = client.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((now - last_seen).total_seconds()))
        return {
            "reachable": age_seconds <= 180,
            "latency_ms": 0,
            "error": None if age_seconds <= 180 else "heartbeat_stale",
        }

    def list_with_probes(self) -> list[tuple[FrontendClient, dict[str, Any]]]:
        clients = self.list(refresh=True)
        return [
            (client, self.connection_status(client))
            for client in clients
        ]
