from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings


class ConnectivityStoreError(RuntimeError):
    pass


class PostgresConnectivityStore:
    """Stores the latest activity reported by VS Code extension clients."""

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
                        CREATE TABLE IF NOT EXISTS client_connections (
                            client_id TEXT PRIMARY KEY,
                            client_type TEXT NOT NULL,
                            project_id TEXT,
                            client_version TEXT,
                            last_event TEXT NOT NULL,
                            details JSONB NOT NULL DEFAULT '{}'::jsonb,
                            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_client_connections_type_seen
                        ON client_connections (client_type, last_seen_at DESC)
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS frontend_api_activity (
                            client_id TEXT NOT NULL,
                            method TEXT NOT NULL,
                            path TEXT NOT NULL,
                            request_count BIGINT NOT NULL DEFAULT 0,
                            success_count BIGINT NOT NULL DEFAULT 0,
                            error_count BIGINT NOT NULL DEFAULT 0,
                            last_status_code INTEGER,
                            last_request_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            last_response_at TIMESTAMPTZ,
                            last_success_at TIMESTAMPTZ,
                            last_duration_ms INTEGER,
                            last_request_id TEXT,
                            PRIMARY KEY (client_id, method, path)
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_frontend_api_activity_path_response
                        ON frontend_api_activity (method, path, last_response_at DESC)
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS communication_events (
                            event_id BIGSERIAL PRIMARY KEY,
                            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            request_id TEXT NOT NULL,
                            channel TEXT NOT NULL,
                            direction TEXT NOT NULL,
                            phase TEXT NOT NULL,
                            status TEXT NOT NULL,
                            method TEXT,
                            path TEXT,
                            client_id TEXT,
                            project_id TEXT,
                            status_code INTEGER,
                            duration_ms INTEGER,
                            provider TEXT,
                            model TEXT,
                            source_count INTEGER,
                            error TEXT,
                            details JSONB NOT NULL DEFAULT '{}'::jsonb
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_communication_events_occurred
                        ON communication_events (occurred_at DESC, event_id DESC)
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_communication_events_request
                        ON communication_events (request_id, occurred_at)
                        """
                    )
                self._initialized = True
            except (psycopg.Error, OSError) as exc:
                raise ConnectivityStoreError(
                    "PostgreSQL connectivity schema is unavailable"
                ) from exc

    def touch(
        self,
        *,
        client_id: str,
        client_type: str,
        event: str,
        project_id: str | None = None,
        client_version: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO client_connections (
                        client_id, client_type, project_id, client_version,
                        last_event, details
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (client_id)
                    DO UPDATE SET
                        client_type = EXCLUDED.client_type,
                        project_id = COALESCE(EXCLUDED.project_id, client_connections.project_id),
                        client_version = COALESCE(
                            EXCLUDED.client_version, client_connections.client_version
                        ),
                        last_event = EXCLUDED.last_event,
                        details = EXCLUDED.details,
                        last_seen_at = NOW()
                    RETURNING client_id, client_type, project_id, client_version,
                              last_event, details, first_seen_at, last_seen_at
                    """,
                    (
                        client_id,
                        client_type,
                        project_id,
                        client_version,
                        event,
                        Jsonb(details or {}),
                    ),
                ).fetchone()
            if row is None:
                raise ConnectivityStoreError(
                    "PostgreSQL did not return the client connection"
                )
            return row
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL connectivity write failed"
            ) from exc

    def latest(self, client_type: str) -> dict[str, Any] | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT client_id, client_type, project_id, client_version,
                           last_event, details, first_seen_at, last_seen_at
                    FROM client_connections
                    WHERE client_type = %s
                    ORDER BY last_seen_at DESC
                    LIMIT 1
                    """,
                    (client_type,),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL connectivity read failed"
            ) from exc

    def record_api_activity(
        self,
        *,
        client_id: str,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        request_id: str,
    ) -> None:
        self._ensure_schema()
        success = 200 <= status_code < 300
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO frontend_api_activity (
                        client_id, method, path, request_count, success_count,
                        error_count, last_status_code, last_request_at,
                        last_response_at, last_success_at, last_duration_ms,
                        last_request_id
                    ) VALUES (
                        %s, %s, %s, 1, %s, %s, %s, NOW(), NOW(),
                        CASE WHEN %s THEN NOW() ELSE NULL END, %s, %s
                    )
                    ON CONFLICT (client_id, method, path)
                    DO UPDATE SET
                        request_count = frontend_api_activity.request_count + 1,
                        success_count = frontend_api_activity.success_count
                            + EXCLUDED.success_count,
                        error_count = frontend_api_activity.error_count
                            + EXCLUDED.error_count,
                        last_status_code = EXCLUDED.last_status_code,
                        last_request_at = EXCLUDED.last_request_at,
                        last_response_at = EXCLUDED.last_response_at,
                        last_success_at = CASE
                            WHEN EXCLUDED.success_count > 0
                            THEN EXCLUDED.last_success_at
                            ELSE frontend_api_activity.last_success_at
                        END,
                        last_duration_ms = EXCLUDED.last_duration_ms,
                        last_request_id = EXCLUDED.last_request_id
                    """,
                    (
                        client_id,
                        method.upper(),
                        path,
                        1 if success else 0,
                        0 if success else 1,
                        status_code,
                        success,
                        max(0, duration_ms),
                        request_id,
                    ),
                )
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL API activity write failed"
            ) from exc

    def latest_api_activity(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT DISTINCT ON (method, path)
                           client_id, method, path, request_count, success_count,
                           error_count, last_status_code, last_request_at,
                           last_response_at, last_success_at, last_duration_ms,
                           last_request_id
                    FROM frontend_api_activity
                    ORDER BY method, path, last_response_at DESC NULLS LAST
                    """
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL API activity read failed"
            ) from exc

    def record_communication_event(
        self,
        *,
        request_id: str,
        channel: str,
        direction: str,
        phase: str,
        status: str,
        method: str | None = None,
        path: str | None = None,
        client_id: str | None = None,
        project_id: str | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        source_count: int | None = None,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Persist operational metadata without storing prompts, code, or answers."""

        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO communication_events (
                        request_id, channel, direction, phase, status,
                        method, path, client_id, project_id, status_code,
                        duration_ms, provider, model, source_count, error, details
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        request_id[:128],
                        channel[:80],
                        direction[:80],
                        phase[:80],
                        status[:32],
                        method.upper()[:16] if method else None,
                        path[:255] if path else None,
                        client_id[:255] if client_id else None,
                        project_id[:255] if project_id else None,
                        status_code,
                        max(0, duration_ms) if duration_ms is not None else None,
                        provider[:80] if provider else None,
                        model[:255] if model else None,
                        max(0, source_count) if source_count is not None else None,
                        error[:1000] if error else None,
                        Jsonb(details or {}),
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM communication_events
                    WHERE occurred_at < NOW() - INTERVAL '7 days'
                    """
                )
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL communication event write failed"
            ) from exc

    def latest_communication_events(
        self,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        safe_limit = min(max(1, limit), 200)
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT event_id, occurred_at, request_id, channel, direction,
                           phase, status, method, path, client_id, project_id,
                           status_code, duration_ms, provider, model, source_count,
                           error, details
                    FROM communication_events
                    WHERE NOT (
                        (channel = 'public-fastapi' AND path = '/v1/health')
                        OR (
                            channel = 'frontend-fastapi'
                            AND client_id LIKE 'vscode:%%'
                            AND details ->> 'client_type' = 'unclassified'
                        )
                    )
                    ORDER BY occurred_at DESC, event_id DESC
                    LIMIT %s
                    """,
                    (safe_limit,),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL communication event read failed"
            ) from exc

    def delete(self, client_id: str) -> None:
        """Used by verification code to remove a synthetic heartbeat."""
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM client_connections WHERE client_id = %s",
                    (client_id,),
                )
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL connectivity delete failed"
            ) from exc
