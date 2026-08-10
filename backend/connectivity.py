from __future__ import annotations

import threading
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings
from .schema_guard import SchemaStateError, require_schema


class ConnectivityStoreError(RuntimeError):
    pass


def group_chat_session_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the administrator user -> session -> message hierarchy.

    Chat audit rows stay the durable source of truth.  The hierarchy is built
    at read time so the Playground does not create a second conversation store
    that can drift away from the operational audit log.
    """

    users: dict[str, dict[str, Any]] = {}
    for row in rows:
        client_id = str(row.get("client_id") or "anonymous")
        encoded_admin_user = (
            client_id.removeprefix("admin-playground:")
            if client_id.startswith("admin-playground:")
            else ""
        )
        declared_user = str(row.get("declared_user") or "").strip()
        client_name = str(row.get("client_name") or "").strip()
        display_name = (
            declared_user
            or (unquote(encoded_admin_user).strip() if encoded_admin_user else "")
            or client_name
            or ("미식별 사용자" if client_id == "anonymous" else client_id)
        )
        user = users.setdefault(
            client_id,
            {
                "user_key": client_id,
                "display_name": display_name,
                "client_id": None if client_id == "anonymous" else client_id,
                "last_message_at": row["received_at"],
                "sessions": {},
            },
        )
        if row["received_at"] > user["last_message_at"]:
            user["last_message_at"] = row["received_at"]

        session_id = str(row.get("session_id") or "stateless")
        sessions = user["sessions"]
        session = sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "title": "새 대화",
                "project_id": str(row.get("project_id") or "__unscoped__"),
                "last_message_at": row["received_at"],
                "message_count": 0,
                "status": str(row.get("status") or "received"),
                "model_id": row.get("used_model_id")
                or row.get("requested_model_id"),
                "provider": row.get("provider"),
                "messages": [],
            },
        )
        if row["received_at"] >= session["last_message_at"]:
            session["last_message_at"] = row["received_at"]
            session["status"] = str(row.get("status") or "received")
            session["model_id"] = row.get("used_model_id") or row.get(
                "requested_model_id"
            )
            session["provider"] = row.get("provider")
            session["project_id"] = str(
                row.get("project_id") or "__unscoped__"
            )
        session["message_count"] += 1
        session["messages"].append(
            {
                "request_id": str(row["request_id"]),
                "received_at": row["received_at"],
                "completed_at": row.get("completed_at"),
                "question": row.get("message"),
                "question_truncated": bool(row.get("message_truncated")),
                "answer": row.get("answer"),
                "answer_truncated": bool(row.get("answer_truncated")),
                "status": str(row.get("status") or "received"),
                "status_code": row.get("status_code"),
                "requested_model_id": row.get("requested_model_id"),
                "used_model_id": row.get("used_model_id"),
                "provider": row.get("provider"),
                "source_count": row.get("source_count"),
                "duration_ms": row.get("duration_ms"),
                "error": row.get("error"),
            }
        )

    result: list[dict[str, Any]] = []
    for user in users.values():
        sessions = list(user.pop("sessions").values())
        for session in sessions:
            session["messages"].sort(key=lambda item: item["received_at"])
            first_question = next(
                (
                    str(item.get("question") or "").strip()
                    for item in session["messages"]
                    if str(item.get("question") or "").strip()
                ),
                "새 대화",
            )
            session["title"] = (
                first_question[:57] + "..."
                if len(first_question) > 60
                else first_question
            )
        sessions.sort(key=lambda item: item["last_message_at"], reverse=True)
        user["sessions"] = sessions
        result.append(user)
    result.sort(key=lambda item: item["last_message_at"], reverse=True)
    return result


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
                    require_schema(connection)
                self._initialized = True
            except (psycopg.Error, OSError, SchemaStateError) as exc:
                raise ConnectivityStoreError(
                    "PostgreSQL schema is not on the required Alembic baseline"
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

    def record_frontend_registration_event(
        self,
        *,
        request_id: str,
        event_type: str,
        status: str,
        client_id: str | None = None,
        instance_id: str | None = None,
        client_name: str | None = None,
        declared_user: str | None = None,
        client_version: str | None = None,
        source_ip: str | None = None,
        registration_type: str | None = None,
        identification_method: str | None = None,
        is_first_connection: bool = False,
        reason: str | None = None,
    ) -> None:
        """Record the initial Frontend enrollment and Client ID lifecycle."""

        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO frontend_registration_events (
                        request_id, event_type, status, client_id, instance_id,
                        client_name, declared_user, client_version, source_ip,
                        registration_type, identification_method,
                        is_first_connection, reason
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        request_id[:128],
                        event_type[:64],
                        status[:32],
                        client_id[:255] if client_id else None,
                        instance_id[:255] if instance_id else None,
                        client_name[:80] if client_name else None,
                        declared_user[:120] if declared_user else None,
                        client_version[:100] if client_version else None,
                        source_ip[:80] if source_ip else None,
                        registration_type[:32] if registration_type else None,
                        identification_method[:64]
                        if identification_method
                        else None,
                        is_first_connection,
                        reason[:1_000] if reason else None,
                    ),
                )
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL frontend registration event write failed"
            ) from exc

    def latest_frontend_registration_events(
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
                    SELECT event_id, occurred_at, request_id, event_type,
                           status, client_id, instance_id, client_name,
                           declared_user, client_version, source_ip,
                           registration_type, identification_method,
                           is_first_connection, reason
                    FROM frontend_registration_events
                    ORDER BY occurred_at DESC, event_id DESC
                    LIMIT %s
                    """,
                    (safe_limit,),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL frontend registration event read failed"
            ) from exc

    @staticmethod
    def _bounded_text(
        value: str | None,
        maximum_chars: int,
    ) -> tuple[str | None, bool]:
        if value is None:
            return None, False
        normalized = value.strip()
        if len(normalized) <= maximum_chars:
            return normalized, False
        return normalized[:maximum_chars], True

    def record_chat_request(
        self,
        *,
        request_id: str,
        client_id: str | None,
        project_id: str,
        session_id: str,
        requested_model_id: str | None,
        message: str,
        history_count: int,
        context_chars: int,
        maximum_chars: int = 20_000,
    ) -> None:
        """Store administrator-visible Chat content separately from telemetry."""

        self._ensure_schema()
        bounded_message, message_truncated = self._bounded_text(
            message,
            maximum_chars,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO chat_audit_logs (
                        request_id, client_id, project_id, session_id,
                        requested_model_id, message, message_truncated,
                        history_count, context_chars, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'received')
                    ON CONFLICT (request_id)
                    DO UPDATE SET
                        client_id = EXCLUDED.client_id,
                        project_id = EXCLUDED.project_id,
                        session_id = EXCLUDED.session_id,
                        requested_model_id = EXCLUDED.requested_model_id,
                        message = EXCLUDED.message,
                        message_truncated = EXCLUDED.message_truncated,
                        history_count = EXCLUDED.history_count,
                        context_chars = EXCLUDED.context_chars
                    """,
                    (
                        request_id[:128],
                        client_id[:255] if client_id else None,
                        project_id[:255],
                        session_id[:255],
                        requested_model_id[:512]
                        if requested_model_id
                        else None,
                        bounded_message,
                        message_truncated,
                        max(0, history_count),
                        max(0, context_chars),
                    ),
                )
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL Chat audit request write failed"
            ) from exc

    def complete_chat_audit(
        self,
        *,
        request_id: str,
        status: str,
        status_code: int,
        answer: str | None = None,
        used_model_id: str | None = None,
        provider: str | None = None,
        source_count: int | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
        maximum_chars: int = 20_000,
    ) -> None:
        self._ensure_schema()
        bounded_answer, answer_truncated = self._bounded_text(
            answer,
            maximum_chars,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE chat_audit_logs
                    SET completed_at = NOW(),
                        status = %s,
                        status_code = %s,
                        answer = %s,
                        answer_truncated = %s,
                        used_model_id = %s,
                        provider = %s,
                        source_count = %s,
                        duration_ms = %s,
                        error = %s
                    WHERE request_id = %s
                    """,
                    (
                        status[:32],
                        status_code,
                        bounded_answer,
                        answer_truncated,
                        used_model_id[:512] if used_model_id else None,
                        provider[:80] if provider else None,
                        max(0, source_count)
                        if source_count is not None
                        else None,
                        max(0, duration_ms)
                        if duration_ms is not None
                        else None,
                        error[:2_000] if error else None,
                        request_id[:128],
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM chat_audit_logs
                    WHERE received_at < NOW() - INTERVAL '7 days'
                    """
                )
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL Chat audit completion write failed"
            ) from exc

    def latest_chat_audit_logs(
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
                    SELECT request_id, received_at, completed_at, client_id,
                           project_id, session_id, requested_model_id, message,
                           message_truncated, history_count, context_chars,
                           status, status_code, answer, answer_truncated,
                           used_model_id, provider, source_count, duration_ms,
                           error
                    FROM chat_audit_logs
                    ORDER BY received_at DESC
                    LIMIT %s
                    """,
                    (safe_limit,),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL Chat audit log read failed"
            ) from exc

    def latest_chat_sessions(
        self,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return recent Chat history grouped by resolved user and session."""

        self._ensure_schema()
        safe_limit = min(max(1, limit), 1_000)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT logs.request_id, logs.received_at,
                           logs.completed_at, logs.client_id,
                           logs.project_id, logs.session_id,
                           logs.requested_model_id, logs.message,
                           logs.message_truncated, logs.status,
                           logs.status_code, logs.answer,
                           logs.answer_truncated, logs.used_model_id,
                           logs.provider, logs.source_count,
                           logs.duration_ms, logs.error,
                           clients.name AS client_name,
                           registration.declared_user
                    FROM chat_audit_logs AS logs
                    LEFT JOIN frontend_clients AS clients
                      ON clients.client_id = logs.client_id
                    LEFT JOIN LATERAL (
                        SELECT events.declared_user
                        FROM frontend_registration_events AS events
                        WHERE events.client_id = logs.client_id
                          AND events.declared_user IS NOT NULL
                        ORDER BY events.occurred_at DESC, events.event_id DESC
                        LIMIT 1
                    ) AS registration ON TRUE
                    WHERE logs.received_at >= NOW() - INTERVAL '7 days'
                    ORDER BY logs.received_at DESC
                    LIMIT %s
                    """,
                    (safe_limit,),
                ).fetchall()
            users = group_chat_session_rows(rows)
            for user in users:
                for session in user["sessions"]:
                    session["messages"] = []
            return users
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL Chat session history read failed"
            ) from exc

    def chat_session(
        self,
        *,
        client_id: str,
        session_id: str,
        limit: int = 200,
    ) -> dict[str, Any] | None:
        """Return one user's session with its chronological messages."""

        self._ensure_schema()
        safe_limit = min(max(1, limit), 200)
        anonymous = client_id == "anonymous"
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT logs.request_id, logs.received_at,
                           logs.completed_at, logs.client_id,
                           logs.project_id, logs.session_id,
                           logs.requested_model_id, logs.message,
                           logs.message_truncated, logs.status,
                           logs.status_code, logs.answer,
                           logs.answer_truncated, logs.used_model_id,
                           logs.provider, logs.source_count,
                           logs.duration_ms, logs.error,
                           clients.name AS client_name,
                           registration.declared_user
                    FROM chat_audit_logs AS logs
                    LEFT JOIN frontend_clients AS clients
                      ON clients.client_id = logs.client_id
                    LEFT JOIN LATERAL (
                        SELECT events.declared_user
                        FROM frontend_registration_events AS events
                        WHERE events.client_id = logs.client_id
                          AND events.declared_user IS NOT NULL
                        ORDER BY events.occurred_at DESC, events.event_id DESC
                        LIMIT 1
                    ) AS registration ON TRUE
                    WHERE logs.session_id = %s
                      AND (
                        (%s AND logs.client_id IS NULL)
                        OR (NOT %s AND logs.client_id = %s)
                      )
                      AND logs.received_at >= NOW() - INTERVAL '7 days'
                    ORDER BY logs.received_at DESC
                    LIMIT %s
                    """,
                    (session_id[:255], anonymous, anonymous, client_id[:255], safe_limit),
                ).fetchall()
            users = group_chat_session_rows(rows)
            if not users or not users[0]["sessions"]:
                return None
            return users[0]["sessions"][0]
        except (psycopg.Error, OSError) as exc:
            raise ConnectivityStoreError(
                "PostgreSQL Chat session message read failed"
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
