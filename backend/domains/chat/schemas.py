from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from ...contracts.common import API_SCHEMA_VERSION, MAX_CHAT_CONTEXT_BYTES, MAX_CHAT_REQUEST_BYTES

class HistoryMessage(BaseModel):
    """Team-vision ChatMessage used by the current VS Code extension."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system"]
    content: str = Field(..., min_length=1, max_length=100_000)
class ChatContextItem(BaseModel):
    """One VS Code ChatPromptReference serialized by the Extension."""

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(default=None, max_length=4096)
    name: str | None = Field(default=None, max_length=512)
    value: Any = None
class ChatSnapshotHint(BaseModel):
    """Optional Snapshot routing hint sent separately from the chat text."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    project_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("project_id", "projectId"),
    )
    snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("snapshot_id", "snapshotId", "id"),
    )
    revision: str | None = Field(default=None, max_length=255)
def _vscode_text(value: Any, *, depth: int = 0) -> str:
    """Extract readable text from serialized VS Code Chat response parts."""

    if depth > 5 or value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            part
            for item in value
            if (part := _vscode_text(item, depth=depth + 1))
        ).strip()
    if not isinstance(value, dict):
        return ""
    for key in ("content", "text", "markdown", "value"):
        if key in value:
            extracted = _vscode_text(value[key], depth=depth + 1)
            if extracted:
                return extracted
    return ""
def _normalize_vscode_history(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role in {"user", "assistant", "system"}:
            content = _vscode_text(item.get("content"))
            if content:
                normalized.append({"role": role, "content": content})
            continue

        # vscode.ChatRequestTurn serializes its user input as prompt.
        prompt = _vscode_text(item.get("prompt"))
        if prompt:
            normalized.append({"role": "user", "content": prompt})

        # vscode.ChatResponseTurn contains an array of ChatResponsePart values.
        response = _vscode_text(item.get("response"))
        if response:
            normalized.append({"role": "assistant", "content": response})
    return normalized[-20:]
def _context_item(value: Any, *, index: int, prefix: str) -> dict[str, Any]:
    if isinstance(value, dict):
        item = dict(value)
        item_id = str(item.get("id") or item.get("uri") or "").strip()
        name = str(item.get("name") or item.get("label") or "").strip()
        item_value = item.get("value", item)
    else:
        item_id = ""
        name = ""
        item_value = value
    return {
        "id": item_id[:4096] or f"{prefix}.{index}",
        "name": name[:512] or f"{prefix} {index}",
        "value": item_value,
    }
def _fallback_session_id(
    project_id: str,
    message: str,
    history: list[dict[str, str]],
) -> str:
    first_user = next(
        (
            item["content"]
            for item in history
            if item.get("role") == "user" and item.get("content")
        ),
        message,
    )
    digest = hashlib.sha256(
        f"{project_id}\n{first_user}".encode("utf-8")
    ).hexdigest()[:24]
    return f"vscode-{digest}"
class ChatRequest(BaseModel):
    """Request accepted from vision/vision/src/types/chat.ts."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "role": "user",
                    "model_id": "backendai:gemma3:4b",
                    "content": "이 프로젝트의 실행 구조를 설명해줘",
                    "stream": True,
                }
            ]
        },
    )

    role: Literal["user"] = Field(
        default="user",
        description="Role of the current frontend chat turn.",
    )
    content: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CHAT_REQUEST_BYTES,
        description="Current frontend chat text, normalized to message.",
    )
    project_id: str = Field(
        default="__auto__",
        min_length=1,
        max_length=255,
        description=(
            "Indexed project ID or workspace name. If omitted, the Backend may "
            "resolve the sole indexed non-default project; ambiguous projects return 409."
        ),
    )
    message: str = Field(
        default="",
        max_length=MAX_CHAT_REQUEST_BYTES,
        validation_alias=AliasChoices("message", "prompt"),
    )
    snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("snapshot_id", "snapshotId"),
        description=(
            "Optional immutable Snapshot ID. Explicit outer fields take precedence "
            "over Snapshot hints embedded in message JSON."
        ),
    )
    snapshot: ChatSnapshotHint | None = Field(
        default=None,
        validation_alias=AliasChoices("snapshot", "snapshot_info", "snapshotInfo"),
    )
    session_id: str = Field(
        default="vscode-stateless",
        min_length=1,
        max_length=255,
        description=(
            "Stable conversation ID is recommended. If omitted, the Backend derives "
            "a deterministic fallback from the project and first user turn."
        ),
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=20,
        deprecated=True,
        description=(
            "Deprecated compatibility field. /v1/chat ignores client top_k and "
            "uses the Backend adaptive retrieval policy."
        ),
    )
    history: list[HistoryMessage] = Field(default_factory=list, max_length=20)
    context: str | list[ChatContextItem] = ""

    # Optional compatibility fields keep the administrator playground and model
    # selector usable without requiring them from the Team-vision extension.
    schema_version: Literal["1.0"] | None = None
    client_request_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^(?:|[A-Za-z0-9._:-]{1,128})$",
    )
    model_id: str | None = Field(default=None, max_length=512)
    stream: bool | None = Field(
        default=False,
        description=(
            "Backward-compatible transport override. true requests SSE and false "
            "requests JSON. When omitted or null, the HTTP Accept header decides."
        ),
    )
    debug: bool = Field(
        default=False,
        description="Include diagnostic response metadata only when explicitly requested.",
    )
    reasoning_mode: Literal["auto", "fast", "balanced", "deep"] | None = Field(
        default=None,
        description=(
            "Optional Agentic RAG budget. Omit to use the Backend default. "
            "auto=Backend complexity routing, fast=one retrieval, "
            "balanced=context-aware bounded retry, deep=multi-query evidence exploration."
        ),
    )

    _intake_input_fields: frozenset[str] = PrivateAttr(default_factory=frozenset)

    def model_post_init(self, __context: Any) -> None:
        extras = self.__pydantic_extra__
        provenance = extras.pop("vision_intake_original", None) if extras else None
        fields = provenance.get("fields", []) if isinstance(provenance, dict) else []
        self._intake_input_fields = frozenset(str(field) for field in fields)

    @property
    def intake_input_fields(self) -> frozenset[str]:
        return self._intake_input_fields

    @model_validator(mode="before")
    @classmethod
    def normalize_vscode_chat_envelope(cls, value: Any) -> Any:
        """Accept both the frozen API payload and serialized VS Code tutorial data."""

        if not isinstance(value, dict):
            return value
        try:
            encoded_request = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        except (TypeError, ValueError):
            encoded_request = b""
        if len(encoded_request) > MAX_CHAT_REQUEST_BYTES:
            raise ValueError("chat request must not exceed 10 MB")
        raw = dict(value)
        raw["vision_intake_original"] = {
            "fields": [str(key) for key in value.keys() if key != "vision_intake_original"]
        }
        request_envelope = (
            dict(raw.get("request"))
            if isinstance(raw.get("request"), dict)
            else {}
        )
        context_envelope = (
            dict(raw.get("chat_context"))
            if isinstance(raw.get("chat_context"), dict)
            else dict(raw.get("context"))
            if isinstance(raw.get("context"), dict)
            else {}
        )

        message = next(
            (
                str(candidate).strip()
                for candidate in (
                    raw.get("message"),
                    raw.get("prompt"),
                    raw.get("content"),
                    request_envelope.get("prompt"),
                )
                if isinstance(candidate, str) and candidate.strip()
            ),
            "",
        )
        if message:
            raw["message"] = message

        workspace = raw.get("workspace")
        workspace_name = (
            str(workspace.get("name") or workspace.get("project_id") or "").strip()
            if isinstance(workspace, dict)
            else str(workspace or "").strip()
        )
        project_id = next(
            (
                str(candidate).strip()
                for candidate in (
                    raw.get("project_id"),
                    raw.get("projectId"),
                    raw.get("workspace_name"),
                    workspace_name,
                    (
                        raw.get("snapshot", {}).get("project_id")
                        or raw.get("snapshot", {}).get("projectId")
                        if isinstance(raw.get("snapshot"), dict)
                        else None
                    ),
                    (
                        raw.get("snapshot_info", {}).get("project_id")
                        or raw.get("snapshot_info", {}).get("projectId")
                        if isinstance(raw.get("snapshot_info"), dict)
                        else None
                    ),
                    (
                        raw.get("snapshotInfo", {}).get("project_id")
                        or raw.get("snapshotInfo", {}).get("projectId")
                        if isinstance(raw.get("snapshotInfo"), dict)
                        else None
                    ),
                )
                if candidate is not None and str(candidate).strip()
            ),
            "__auto__",
        )
        raw["project_id"] = project_id

        snapshot_value = next(
            (
                candidate
                for candidate in (
                    raw.get("snapshot"),
                    raw.get("snapshot_info"),
                    raw.get("snapshotInfo"),
                )
                if isinstance(candidate, dict)
            ),
            None,
        )
        if snapshot_value is not None:
            raw["snapshot"] = snapshot_value
        snapshot_id = next(
            (
                str(candidate).strip()
                for candidate in (
                    raw.get("snapshot_id"),
                    raw.get("snapshotId"),
                    snapshot_value.get("snapshot_id") if snapshot_value else None,
                    snapshot_value.get("snapshotId") if snapshot_value else None,
                    snapshot_value.get("id") if snapshot_value else None,
                )
                if candidate is not None and str(candidate).strip()
            ),
            None,
        )
        if snapshot_id:
            raw["snapshot_id"] = snapshot_id

        history_input = raw.get("history")
        if history_input is None:
            history_input = context_envelope.get("history")
        history = _normalize_vscode_history(history_input)
        raw["history"] = history

        session_id = next(
            (
                str(candidate).strip()
                for candidate in (
                    raw.get("session_id"),
                    raw.get("conversation_id"),
                    raw.get("chat_session_id"),
                )
                if candidate is not None and str(candidate).strip()
            ),
            "",
        )
        raw["session_id"] = session_id or _fallback_session_id(
            project_id,
            message,
            history,
        )

        existing_context = raw.get("context")
        context_items: list[Any] = []
        if isinstance(existing_context, list):
            context_items.extend(existing_context)
        elif isinstance(existing_context, str) and existing_context.strip():
            context_items.append(
                {
                    "id": "vscode.context",
                    "name": "VS Code context",
                    "value": {"content": existing_context},
                }
            )

        references: list[Any] = []
        for candidate in (
            raw.get("references"),
            request_envelope.get("references"),
            context_envelope.get("references"),
        ):
            if isinstance(candidate, list):
                references.extend(candidate)
        for index, reference in enumerate(references, start=1):
            context_items.append(
                _context_item(reference, index=index, prefix="vscode.reference")
            )

        command = request_envelope.get("command", raw.get("command"))
        if isinstance(command, str) and command.strip():
            context_items.append(
                {
                    "id": "vscode.command",
                    "name": "VS Code chat command",
                    "value": {"content": command.strip()},
                }
            )
        if workspace:
            context_items.append(
                {
                    "id": "vscode.workspace",
                    "name": "VS Code workspace",
                    "value": workspace,
                }
            )

        canonical_keys = {
            "project_id",
            "projectId",
            "snapshot_id",
            "snapshotId",
            "snapshot",
            "snapshot_info",
            "snapshotInfo",
            "role",
            "content",
            "message",
            "prompt",
            "session_id",
            "conversation_id",
            "chat_session_id",
            "top_k",
            "history",
            "context",
            "schema_version",
            "client_request_id",
            "model_id",
            "stream",
            "debug",
            "reasoning_mode",
            "request",
            "chat_context",
            "workspace",
            "workspace_name",
            "references",
            "command",
            "vision_intake_original",
        }
        future_data = {
            key: item
            for key, item in raw.items()
            if key not in canonical_keys
        }
        request_metadata = {
            key: item
            for key, item in request_envelope.items()
            if key not in {"prompt", "references", "command"}
        }
        if future_data or request_metadata:
            serialized = json.dumps(
                {
                    "future_fields": future_data,
                    "request_metadata": request_metadata,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            context_items.append(
                {
                    "id": "vscode.extra",
                    "name": "VS Code request metadata",
                    "value": {"content": serialized[:50_000]},
                }
            )
        raw["context"] = context_items[:100] if context_items else ""

        # Consumed wrapper fields are normalized into the canonical fields above.
        raw.pop("request", None)
        raw.pop("chat_context", None)
        return raw

    @field_validator("client_request_id", "model_id", mode="before")
    @classmethod
    def normalize_blank_optional_chat_fields(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("context", mode="before")
    @classmethod
    def normalize_null_chat_context(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("context")
    @classmethod
    def validate_chat_context_size(
        cls,
        value: str | list[ChatContextItem],
    ) -> str | list[ChatContextItem]:
        if isinstance(value, str):
            encoded = value.encode("utf-8")
        else:
            if len(value) > 100:
                raise ValueError("context array must not exceed 100 items")
            encoded = json.dumps(
                [item.model_dump(mode="json") for item in value],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        if len(encoded) > MAX_CHAT_CONTEXT_BYTES:
            raise ValueError("context must not exceed 5 MB")
        return value

    @field_validator("project_id", "session_id", "model_id", "message", "content")
    @classmethod
    def normalize_chat_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("chat field must not be blank")
        return normalized
class ChatContextRegistrationRequest(BaseModel):
    """Optional project/Snapshot envelope sent separately from ChatRequest."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "project_id": "h5vision/fest-api",
                    "commit_id": "0123456789abcdef0123456789abcdef01234567",
                    "snapshot_id": None,
                }
            ]
        },
    )

    project_id: str | None = Field(default=None, min_length=1, max_length=255)
    commit_id: str | None = Field(
        default=None,
        pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$",
        description="Optional 40- or 64-character Git object SHA.",
    )
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("project_id", "commit_id", "snapshot_id", mode="before")
    @classmethod
    def normalize_optional_context_text(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("commit_id")
    @classmethod
    def normalize_commit_id(cls, value: str | None) -> str | None:
        return value.lower() if value else None
class ChatContextResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    context_id: str
    project_id: str | None = None
    commit_id: str | None = None
    snapshot_id: str | None = None
    resolution: str
    grounding_available: bool
    created_at: datetime
    expires_at: datetime
class SourceDocument(BaseModel):
    """SourceDocument returned to the current Team-vision extension."""

    model_config = ConfigDict(extra="forbid")

    file: str
    chunk: str
    score: float | None = None
class ChatResponse(BaseModel):
    """Response declared in vision/vision/src/types/chat.ts."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "answer": "프로젝트 실행 구조는 다음과 같습니다. [1]",
                    "source": [
                        {
                            "file": "backend/app.py",
                            "chunk": "FastAPI 애플리케이션과 /v1/chat Route...",
                            "score": 0.91,
                        }
                    ],
                    "metadata": {
                        "request_id": "req_0123456789abcdef",
                        "project_id": "h5vision/fest-api",
                        "session_id": "9efda536-b502-49e4-926d-53343a428df0",
                        "used_model_id": "backendai-default",
                    },
                }
            ]
        },
    )

    answer: str
    source: list[SourceDocument]
    metadata: dict[str, Any]

__all__ = ['HistoryMessage', 'ChatContextItem', 'ChatSnapshotHint', 'ChatRequest', 'ChatContextRegistrationRequest', 'ChatContextResponse', 'SourceDocument', 'ChatResponse']
