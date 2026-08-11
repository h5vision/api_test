from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from .config import Settings
from .language_registry import (
    context_language_metadata,
    message_language_metadata,
    normalize_chat_context_languages,
)
from .schema_guard import SchemaStateError, require_schema
from .schemas import ChatRequest


DeepNormalizationMode = Literal["inherit", "auto", "off"]


class ChatIntakeSettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatIntakeSettings:
    deep_normalization_enabled: bool
    fallback_mode: Literal["raw_message"]
    updated_at: datetime


@dataclass(frozen=True)
class ChatIntakeResult:
    payload: ChatRequest
    deep_normalization_enabled: bool
    input_format: Literal["plain_text", "embedded_json", "labeled_text"]
    fallback_used: bool
    extracted_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    error_code: str | None = None

    def metadata(self, *, debug: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "basic_normalization_enabled": True,
            "deep_normalization_enabled": self.deep_normalization_enabled,
            "input_format": self.input_format,
            "fallback_used": self.fallback_used,
            "extracted_fields": list(self.extracted_fields),
            "conflicts": list(self.conflicts),
        }
        if debug and self.error_code:
            value["error_code"] = self.error_code
        detected_languages = context_language_metadata(self.payload)
        if detected_languages:
            value["context_languages"] = detected_languages
        message_languages = message_language_metadata(self.payload)
        if message_languages:
            value["message_languages"] = message_languages
        return value


class PostgresChatIntakeSettingsStore:
    """Database-owned global policy; client overrides live in frontend_clients."""

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
                raise ChatIntakeSettingsError(
                    "Chat intake settings require the current Alembic schema"
                ) from exc

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ChatIntakeSettings:
        return ChatIntakeSettings(
            deep_normalization_enabled=bool(row["deep_normalization_enabled"]),
            fallback_mode="raw_message",
            updated_at=row["updated_at"],
        )

    def get(self) -> ChatIntakeSettings:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT deep_normalization_enabled, fallback_mode, updated_at
                    FROM chat_intake_settings
                    WHERE singleton = TRUE
                    """
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise ChatIntakeSettingsError("Chat intake settings read failed") from exc
        if row is None:
            raise ChatIntakeSettingsError("Chat intake settings row is missing")
        return self._from_row(row)

    def update(
        self,
        *,
        deep_normalization_enabled: bool,
        fallback_mode: Literal["raw_message"] = "raw_message",
    ) -> ChatIntakeSettings:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE chat_intake_settings
                    SET deep_normalization_enabled = %s,
                        fallback_mode = %s,
                        updated_at = NOW()
                    WHERE singleton = TRUE
                    RETURNING deep_normalization_enabled, fallback_mode, updated_at
                    """,
                    (deep_normalization_enabled, fallback_mode),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise ChatIntakeSettingsError("Chat intake settings update failed") from exc
        if row is None:
            raise ChatIntakeSettingsError("Chat intake settings row is missing")
        return self._from_row(row)


def resolve_deep_normalization(
    global_enabled: bool,
    client_mode: DeepNormalizationMode | str | None,
) -> bool:
    normalized = str(client_mode or "inherit").strip().lower()
    if normalized == "auto":
        return True
    if normalized == "off":
        return False
    return bool(global_enabled)


_RECOGNIZED_ENVELOPE_KEYS = {
    "role",
    "content",
    "message",
    "prompt",
    "request",
    "project_id",
    "projectId",
    "workspace",
    "workspace_name",
    "snapshot",
    "snapshot_id",
    "snapshotId",
    "snapshot_info",
    "snapshotInfo",
    "session_id",
    "conversation_id",
    "chat_session_id",
    "history",
    "context",
    "chat_context",
    "references",
    "model_id",
    "stream",
    "debug",
    "reasoning_mode",
    "client_request_id",
}

_OUTER_FIELD_ALIASES: dict[str, set[str]] = {
    "role": {"role"},
    "project_id": {"project_id", "projectId", "workspace", "workspace_name"},
    "snapshot_id": {"snapshot_id", "snapshotId"},
    "snapshot": {"snapshot", "snapshot_info", "snapshotInfo"},
    "session_id": {"session_id", "conversation_id", "chat_session_id"},
    "history": {"history", "chat_context"},
    "context": {"context", "references", "chat_context"},
    "schema_version": {"schema_version"},
    "client_request_id": {"client_request_id"},
    "model_id": {"model_id"},
    "stream": {"stream"},
    "debug": {"debug"},
    "reasoning_mode": {"reasoning_mode"},
    "top_k": {"top_k"},
}


def _explicit(input_fields: frozenset[str], canonical: str) -> bool:
    return bool(input_fields.intersection(_OUTER_FIELD_ALIASES[canonical]))


def _snapshot_project_is_explicit(payload: ChatRequest) -> bool:
    return (
        _explicit(payload.intake_input_fields, "snapshot")
        and payload.snapshot is not None
        and bool(payload.snapshot.project_id)
    )


_LABELED_HINT_PATTERN = re.compile(
    r"(?im)(?:^|[;,]\s*)"
    r"(?P<key>project_id|projectId|snapshot_id|snapshotId)"
    r"\s*[:=]\s*[\"']?(?P<value>[^\s,;\"']{1,255})[\"']?"
)


def _labeled_message_envelope(message: str) -> dict[str, Any] | None:
    hints: dict[str, str] = {}
    for match in _LABELED_HINT_PATTERN.finditer(message):
        canonical = (
            "project_id"
            if match.group("key") in {"project_id", "projectId"}
            else "snapshot_id"
        )
        hints.setdefault(canonical, match.group("value").strip())
    if not hints:
        return None
    question = _LABELED_HINT_PATTERN.sub(" ", message)
    question = re.sub(r"^[\s,;|\-]+", "", question)
    question = re.sub(
        r"(?im)^\s*(?:message|content|question|질문)\s*[:=]\s*",
        "",
        question,
    )
    question = re.sub(r"^[\s,;|\-]+|[\s,;|\-]+$", "", question).strip()
    if not question:
        return None
    return {**hints, "message": question}


def normalize_chat_intake(
    payload: ChatRequest,
    *,
    deep_enabled: bool,
) -> ChatIntakeResult:
    """Normalize a JSON envelope carried inside message without guessing code JSON."""

    # Language detection is part of the always-on structural normalization.
    # Deep normalization only controls interpretation of fields embedded in message.
    payload = normalize_chat_context_languages(payload)
    raw_message = payload.message.strip()
    if not deep_enabled:
        return ChatIntakeResult(payload, deep_enabled, "plain_text", False)
    input_format: Literal["embedded_json", "labeled_text"]
    if raw_message.startswith("{"):
        try:
            decoded = json.loads(raw_message)
        except json.JSONDecodeError:
            return ChatIntakeResult(
                payload,
                deep_enabled,
                "plain_text",
                True,
                error_code="embedded_json_invalid",
            )
        input_format = "embedded_json"
    else:
        decoded = _labeled_message_envelope(raw_message)
        if decoded is None:
            return ChatIntakeResult(payload, deep_enabled, "plain_text", False)
        input_format = "labeled_text"
    if not isinstance(decoded, dict) or not _RECOGNIZED_ENVELOPE_KEYS.intersection(decoded):
        return ChatIntakeResult(payload, deep_enabled, "plain_text", False)
    try:
        embedded = ChatRequest.model_validate(decoded)
    except ValidationError:
        return ChatIntakeResult(
            payload,
            deep_enabled,
            "plain_text",
            True,
            error_code="embedded_contract_invalid",
        )
    if not embedded.message.strip():
        return ChatIntakeResult(
            payload,
            deep_enabled,
            "plain_text",
            True,
            error_code="embedded_message_missing",
        )

    merged: dict[str, Any] = dict(decoded)
    merged["message"] = embedded.message
    merged["content"] = embedded.message
    conflicts: list[str] = []

    outer_project_explicit = (
        _explicit(payload.intake_input_fields, "project_id")
        or _snapshot_project_is_explicit(payload)
    ) and payload.project_id not in {"__auto__", "auto", "default"}
    if outer_project_explicit:
        if embedded.project_id not in {"__auto__", payload.project_id}:
            conflicts.append("project_id:outer_wins")
        merged["project_id"] = payload.project_id

    outer_snapshot_explicit = (
        _explicit(payload.intake_input_fields, "snapshot_id")
        or _explicit(payload.intake_input_fields, "snapshot")
    )
    if outer_snapshot_explicit:
        if (
            payload.snapshot_id
            and embedded.snapshot_id
            and payload.snapshot_id != embedded.snapshot_id
        ):
            conflicts.append("snapshot_id:outer_wins")
        if payload.snapshot is not None:
            merged["snapshot"] = payload.snapshot.model_dump(mode="json")
        if payload.snapshot_id:
            merged["snapshot_id"] = payload.snapshot_id

    for field in (
        "role",
        "session_id",
        "history",
        "context",
        "schema_version",
        "client_request_id",
        "model_id",
        "stream",
        "debug",
        "reasoning_mode",
        "top_k",
    ):
        if not _explicit(payload.intake_input_fields, field):
            continue
        value = getattr(payload, field)
        if isinstance(value, list):
            value = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in value
            ]
        merged[field] = value

    try:
        normalized = ChatRequest.model_validate(merged)
    except ValidationError:
        return ChatIntakeResult(
            payload,
            deep_enabled,
            "plain_text",
            True,
            error_code="merged_contract_invalid",
        )

    extracted = sorted(
        key for key in decoded.keys() if key in _RECOGNIZED_ENVELOPE_KEYS
    )
    return ChatIntakeResult(
        normalized,
        deep_enabled,
        input_format,
        False,
        tuple(extracted),
        tuple(conflicts),
    )
