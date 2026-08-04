from __future__ import annotations

import json
import hashlib
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


MAX_METADATA_BYTES = 500_000_000
MAX_CHAT_CONTEXT_BYTES = 5_000_000
MAX_CHAT_REQUEST_BYTES = 10_000_000
API_SCHEMA_VERSION = "1.0"


def _validate_metadata_size(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError("metadata must not exceed 500 MB")
    return value


class DocumentInput(BaseModel):
    document_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    path: str | None = None
    language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestRequest(BaseModel):
    project_id: str = Field(default="default", min_length=1)
    documents: list[DocumentInput] = Field(..., min_length=1, max_length=10_000)


class ProjectMetadataDocumentInput(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=512)
    path: str = Field(..., min_length=1, max_length=4096)
    language: str | None = Field(default=None, min_length=1, max_length=100)
    type: Literal["file", "directory"]
    size: int | None = Field(default=None, ge=0)
    modified_time: datetime | None = Field(default=None, alias="modifiedTime")
    children: list[dict[str, Any]] | None = None

    @field_validator("name", "path", "type")
    @classmethod
    def normalize_document_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("document field must not be blank")
        return normalized

    @field_validator("language")
    @classmethod
    def normalize_optional_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ProjectMetadataIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, max_length=255)
    documents: list[ProjectMetadataDocumentInput] = Field(
        ..., min_length=1, max_length=10_000
    )
    metadata: dict[str, Any] = Field(..., max_length=200)

    @field_validator("project_id")
    @classmethod
    def normalize_project_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("project_id must not be blank")
        return normalized

    @field_validator("metadata")
    @classmethod
    def limit_metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata_size(value)


class IngestResponse(BaseModel):
    project_id: str
    documents_received: int
    chunks_stored: int
    embedding_provider: str
    metadata_records_stored: int = 0
    documents_registered: int = 0


IndexedProjectStatus = Literal[
    "not_indexed",
    "queued",
    "indexing",
    "ready",
    "partially_ready",
    "failed",
    "stale",
]


class IndexedProjectItem(BaseModel):
    project_id: str
    project_name: str
    git_commit_sha: str | None = None
    git_short_sha: str | None = None
    git_branch: str | None = None
    git_dirty: bool | None = None
    git_committed_at: datetime | None = None
    active_snapshot_id: str | None = None
    index_status: IndexedProjectStatus
    indexed_at: datetime | None = None


class IndexedProjectListResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    request_id: str
    projects: list[IndexedProjectItem]
    total: int = Field(..., ge=0)
    generated_at: datetime


MetadataScope = Literal["project", "session", "document", "custom"]


class MetadataUpsertRequest(BaseModel):
    project_id: str = Field(..., min_length=1, max_length=255)
    session_id: str | None = Field(default=None, min_length=1, max_length=255)
    scope: MetadataScope = "project"
    entity_id: str | None = Field(default=None, min_length=1, max_length=512)
    source: str = Field(default="vscode-extension", min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(..., min_length=1, max_length=200)

    @field_validator("project_id", "session_id", "entity_id", "source")
    @classmethod
    def normalize_identifiers(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @field_validator("metadata")
    @classmethod
    def limit_metadata_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata_size(value)

    @model_validator(mode="after")
    def resolve_entity_id(self) -> "MetadataUpsertRequest":
        if self.scope == "project":
            self.entity_id = self.entity_id or self.project_id
        elif self.scope == "session":
            self.entity_id = self.entity_id or self.session_id
            if not self.entity_id:
                raise ValueError("session scope requires session_id or entity_id")
        elif not self.entity_id:
            raise ValueError(f"{self.scope} scope requires entity_id")
        return self


class MetadataRecord(BaseModel):
    metadata_id: UUID
    project_id: str
    session_id: str | None = None
    scope: MetadataScope
    entity_id: str
    source: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MetadataListResponse(BaseModel):
    project_id: str
    records: list[MetadataRecord]


class SearchRequest(BaseModel):
    project_id: str = Field(default="default", min_length=1)
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: int | None = Field(default=None, ge=1)
    document_id: str
    document_version_id: str | None = None
    chunk_id: str
    path: str | None = None
    language: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    project_id: str
    query: str
    results: list[Source]
    embedding_provider: str
    

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
                    "project_id": "h5vision/fest-api",
                    "message": "이 프로젝트의 실행 구조를 설명해줘",
                    "session_id": "9efda536-b502-49e4-926d-53343a428df0",
                    "reasoning_mode": "balanced",
                    "debug": False,
                    "history": [],
                    "context": (
                        "파일: README.md\n\n"
                        "# Project\n이 프로젝트는 VS Code AI Assistant입니다."
                    ),
                }
            ]
        },
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
        ...,
        min_length=1,
        max_length=100_000,
        validation_alias=AliasChoices("message", "prompt"),
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
            "uses the search policy configured by rag_lab."
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
            "false returns the frozen JSON response. true returns Server-Sent "
            "Events: meta, delta, done, or error."
        ),
    )
    debug: bool = Field(
        default=False,
        description=(
            "When true, include diagnostic metadata in the chat response. "
            "Normal frontend requests receive an empty metadata object."
        ),
    )
    reasoning_mode: Literal["auto", "fast", "balanced", "deep"] | None = Field(
        default=None,
        description=(
            "Deprecated compatibility field. rag_lab owns retrieval and prompt "
            "assembly, so the Backend accepts but ignores this value."
        ),
    )

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
                    raw.get("workspace_name"),
                    workspace_name,
                )
                if candidate is not None and str(candidate).strip()
            ),
            "__auto__",
        )
        raw["project_id"] = project_id

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
            "reasoning_mode",
            "request",
            "chat_context",
            "workspace",
            "workspace_name",
            "references",
            "command",
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

    @field_validator("project_id", "session_id", "model_id", "message")
    @classmethod
    def normalize_chat_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("chat field must not be blank")
        return normalized


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
                    "metadata": {},
                }
            ]
        },
    )

    answer: str
    source: list[SourceDocument]
    metadata: dict[str, Any]


class ModelInfo(BaseModel):
    model_id: str
    model_name: str
    display_name: str
    provider: str
    location: Literal["internal", "cloud", "local"]
    deployment_type: Literal["cloud", "local", "remote_server"]
    endpoint: str | None = None
    enabled: bool
    available: bool
    is_default: bool = False
    streaming: bool = False


class ModelListResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    default_model_id: str
    checked_at: datetime
    models: list[ModelInfo]


class ModelAccessUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(..., min_length=1, max_length=512)
    enabled: bool

    @field_validator("model_id")
    @classmethod
    def normalize_model_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model_id must not be blank")
        return normalized


class ModelAccessUpdateResponse(BaseModel):
    model: ModelInfo
    updated_at: datetime


class AIProviderWriteRequest(BaseModel):
    """Administrator-managed inference endpoint.

    ``base_url`` supports provider URLs such as ``https://api.groq.com/openai/v1``.
    For an Ollama server on a LAN, ``host`` and ``port`` can be supplied instead.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    protocol: Literal["auto", "ollama", "openai"] = "auto"
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    use_tls: bool = False
    auth_type: Literal["none", "bearer", "x-api-key"] = "none"
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    enabled: bool = True
    deployment_type: Literal["cloud", "local", "remote_server"] = "remote_server"

    @field_validator("name")
    @classmethod
    def normalize_provider_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("api_key")
    @classmethod
    def normalize_provider_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("base_url")
    @classmethod
    def validate_provider_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError(
                "base_url must be an absolute HTTP(S) URL without credentials or fragment"
            )
        return normalized

    @field_validator("host")
    @classmethod
    def validate_provider_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if (
            not normalized
            or "/" in normalized
            or "\\" in normalized
            or "@" in normalized
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("host must be an IP address or DNS name")
        return normalized

    @model_validator(mode="after")
    def validate_provider_connection(self) -> "AIProviderWriteRequest":
        if self.base_url is None and (self.host is None or self.port is None):
            raise ValueError("base_url or both host and port are required")
        if self.base_url is not None and (self.host is not None or self.port is not None):
            raise ValueError("use either base_url or host/port, not both")
        if self.auth_type == "none" and self.api_key:
            raise ValueError("api_key requires bearer or x-api-key auth_type")
        if self.clear_api_key and self.api_key:
            raise ValueError("api_key and clear_api_key cannot be used together")
        return self

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        scheme = "https" if self.use_tls else "http"
        return f"{scheme}://{self.host}:{self.port}"


class AIProviderRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    name: str
    protocol: Literal["ollama", "openai"]
    base_url: str
    auth_type: Literal["none", "bearer", "x-api-key"]
    api_key_configured: bool
    api_key_hint: str | None = None
    enabled: bool
    deployment_type: Literal["cloud", "local", "remote_server"]
    status: Literal["unknown", "online", "degraded", "offline", "disabled"]
    error: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    model_count: int = Field(default=0, ge=0)
    models: list[str] = Field(default_factory=list)
    embedding_model_count: int = Field(default=0, ge=0)
    embedding_models: list[str] = Field(default_factory=list)
    last_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AIProviderListResponse(BaseModel):
    providers: list[AIProviderRecord]
    total: int = Field(..., ge=0)


class EmbeddingModelProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(..., min_length=1, max_length=255)
    model_name: str = Field(..., min_length=1, max_length=512)

    @field_validator("provider_id", "model_name")
    @classmethod
    def normalize_embedding_probe_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("embedding probe field must not be blank")
        return normalized


class EmbeddingModelProbeResponse(BaseModel):
    provider_id: str
    provider_name: str
    protocol: Literal["ollama", "openai"]
    base_url: str
    deployment_type: Literal["cloud", "local", "remote_server"]
    model_name: str
    dimension: int = Field(..., ge=1, le=65_536)
    latency_ms: int = Field(..., ge=0)


class OllamaScanTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    base_url: str
    status: Literal["online", "degraded", "offline"]
    models: list[str] = Field(default_factory=list)
    embedding_models: list[str] = Field(default_factory=list)
    skipped_non_chat_models: list[str] = Field(default_factory=list)
    latency_ms: int = Field(default=0, ge=0)
    error: str | None = None
    registered: bool = False
    provider_id: str | None = None


class OllamaScanResponse(BaseModel):
    checked_at: datetime
    targets: list[OllamaScanTarget]
    discovered_servers: int = Field(..., ge=0)
    registered_providers: int = Field(..., ge=0)
    chat_models: int = Field(..., ge=0)
    embedding_models: int = Field(..., ge=0)


class CloudProviderScanTarget(BaseModel):
    name: str
    base_url: str
    configured: bool
    status: Literal["online", "degraded", "offline", "not_configured"]
    chat_models: list[str] = Field(default_factory=list)
    embedding_models: list[str] = Field(default_factory=list)
    skipped_models: list[str] = Field(default_factory=list)
    latency_ms: int = Field(default=0, ge=0)
    error: str | None = None
    registered: bool = False
    provider_id: str | None = None


class CloudProviderScanResponse(BaseModel):
    checked_at: datetime
    targets: list[CloudProviderScanTarget]
    configured_providers: int = Field(..., ge=0)
    registered_providers: int = Field(..., ge=0)
    chat_models: int = Field(..., ge=0)
    embedding_models: int = Field(..., ge=0)


class CloudProviderCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_type: Literal["nvidia", "groq", "openai", "custom"]
    name: str | None = Field(default=None, max_length=100)
    api_key: str = Field(..., min_length=1, max_length=4096)
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    auth_type: Literal["bearer", "x-api-key"] = "bearer"
    enabled: bool = True

    @field_validator("name", "api_key")
    @classmethod
    def normalize_cloud_credential_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("cloud credential field must not be blank")
        return normalized

    @field_validator("base_url")
    @classmethod
    def validate_cloud_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        return normalized

    @model_validator(mode="after")
    def validate_custom_cloud_provider(self) -> "CloudProviderCredentialRequest":
        if self.provider_type == "custom" and not self.base_url:
            raise ValueError("base_url is required for a custom provider")
        if self.provider_type != "custom" and self.auth_type != "bearer":
            raise ValueError("known Cloud providers use Bearer authentication")
        return self


VectorDatabaseEngine = Literal[
    "auto",
    "sqlite",
    "qdrant",
    "weaviate",
    "chroma",
    "milvus",
    "pgvector",
    "rag_lab",
    "custom",
]


class VectorDatabaseProviderWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    engine: VectorDatabaseEngine = "auto"
    connection_mode: Literal["remote", "local"] = "remote"
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    use_tls: bool = False
    storage_namespace: str = Field(..., min_length=1, max_length=255)
    local_path: str | None = Field(default=None, min_length=1, max_length=2048)
    embedding_model_path: str = Field(..., min_length=1, max_length=2048)
    embedding_models: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True

    @field_validator("name", "storage_namespace", "embedding_model_path")
    @classmethod
    def normalize_vector_database_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("VectorDB provider field must not be blank")
        return normalized

    @field_validator("host")
    @classmethod
    def validate_vector_database_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if (
            not normalized
            or "/" in normalized
            or "\\" in normalized
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("host must be an IP address or DNS/service name")
        return normalized

    @field_validator("local_path")
    @classmethod
    def normalize_vector_database_local_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().replace("\\", "/")
        if not normalized:
            return None
        return normalized

    @field_validator("embedding_models")
    @classmethod
    def normalize_vector_database_models(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip() for item in value if item.strip()})
        if not normalized:
            raise ValueError("at least one embedding model must be selected")
        if any(len(item) > 512 for item in normalized):
            raise ValueError("embedding model name is too long")
        return normalized

    @model_validator(mode="after")
    def validate_vector_database_connection(self) -> "VectorDatabaseProviderWriteRequest":
        if self.connection_mode == "remote" and (not self.host or not self.port):
            raise ValueError("host and port are required for a remote VectorDB")
        if self.connection_mode == "local" and not self.local_path:
            raise ValueError("local_path is required for local VectorDB storage")
        return self


class VectorDatabaseProviderRecord(BaseModel):
    provider_id: str
    name: str
    engine: VectorDatabaseEngine
    detected_engine: str | None = None
    connection_mode: Literal["remote", "local"]
    host: str | None = None
    port: int | None = None
    base_url: str
    use_tls: bool
    storage_namespace: str
    local_path: str | None = None
    embedding_model_path: str
    embedding_models: list[str]
    enabled: bool
    status: Literal["unknown", "online", "degraded", "offline", "disabled"]
    collections: list[str]
    adapter_available: bool
    error: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    last_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class VectorDatabaseProviderListResponse(BaseModel):
    providers: list[VectorDatabaseProviderRecord]
    total: int = Field(..., ge=0)
    online: int = Field(..., ge=0)
    adapter_ready: int = Field(..., ge=0)


class GitVersionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    commit_sha: str | None = Field(
        default=None,
        min_length=7,
        max_length=64,
        pattern=r"^[a-fA-F0-9]{7,64}$",
        validation_alias=AliasChoices("commit_sha", "commit", "head"),
    )
    branch: str | None = Field(default=None, min_length=1, max_length=255)
    dirty: bool | None = None
    committed_at: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("committed_at", "committedAt"),
    )

    @field_validator("commit_sha")
    @classmethod
    def normalize_commit_sha(cls, value: str | None) -> str | None:
        return value.lower() if value else None


class ProjectTreeNode(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=512)
    path: str = Field(..., min_length=1, max_length=4096)
    type: Literal["file", "directory"]
    language: str | None = Field(default=None, max_length=100)
    size: int | None = Field(default=None, ge=0)
    modified_time: datetime | None = Field(default=None, alias="modifiedTime")
    children: list["ProjectTreeNode"] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_tree_name(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or normalized in {".", ".."}
            or "/" in normalized
            or "\\" in normalized
        ):
            raise ValueError("tree node name must be a single safe path segment")
        return normalized


class ProjectVersionDescriptor(BaseModel):
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=255)
    manifest_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-fA-F0-9]{64}$",
    )
    structure_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-fA-F0-9]{64}$",
    )
    entry_count: int | None = Field(default=None, ge=1, le=100_000)
    modified_at: datetime | None = None
    git: GitVersionInfo | None = None

    @field_validator("manifest_sha256", "structure_sha256")
    @classmethod
    def normalize_sha256(cls, value: str | None) -> str | None:
        return value.lower() if value else None


class ProjectVersionCheckRequest(ProjectVersionDescriptor):
    model_config = ConfigDict(extra="forbid")

    tree: ProjectTreeNode | None = Field(
        default=None,
        description=(
            "The complete loaded workspace tree. Absolute path values are accepted "
            "for compatibility but ignored when the structure fingerprint is built."
        ),
    )


VersionRelation = Literal[
    "same",
    "client_newer",
    "backend_newer",
    "diverged",
    "unknown",
    "not_found",
]


class ProjectVersionChecks(BaseModel):
    snapshot_id: bool | None = None
    manifest_sha256: bool | None = None
    structure_sha256: bool | None = None
    git_commit_sha: bool | None = None
    git_branch: bool | None = None
    git_dirty: bool | None = None
    modified_at: bool | None = None


class ProjectVersionCheckResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    project_id: str
    backend_registered: bool
    backend_source: Literal["local", "postgresql", "local+postgresql", "none"]
    same_version: bool | None
    relation: VersionRelation
    relation_basis: Literal[
        "exact_match",
        "git_committed_at",
        "modified_at",
        "version_signals",
        "none",
    ]
    checked_at: datetime
    client: ProjectVersionDescriptor
    backend: ProjectVersionDescriptor | None = None
    backend_updated_at: datetime | None = None
    checks: ProjectVersionChecks
    reasons: list[str] = Field(default_factory=list)


class APIEndpointActivity(BaseModel):
    method: str
    path: str
    requested: bool
    responded: bool
    success: bool
    last_status_code: int | None = None
    last_request_at: datetime | None = None
    last_response_at: datetime | None = None
    last_success_at: datetime | None = None
    last_duration_ms: int | None = None
    last_request_id: str | None = None
    client_id: str | None = None
    request_count: int = 0
    success_count: int = 0
    error_count: int = 0


class APIEndpointActivityResponse(BaseModel):
    checked_at: datetime
    endpoints: list[APIEndpointActivity]


class CommunicationEvent(BaseModel):
    event_id: int
    occurred_at: datetime
    request_id: str
    channel: str
    direction: str
    phase: str
    status: str
    method: str | None = None
    path: str | None = None
    client_id: str | None = None
    project_id: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    provider: str | None = None
    model: str | None = None
    source_count: int | None = None
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CommunicationEventListResponse(BaseModel):
    checked_at: datetime
    retention_days: int = 7
    events: list[CommunicationEvent]


class ChatAuditLog(BaseModel):
    request_id: str
    received_at: datetime
    completed_at: datetime | None = None
    client_id: str | None = None
    project_id: str
    session_id: str
    requested_model_id: str | None = None
    message: str | None = None
    message_truncated: bool = False
    history_count: int = 0
    context_chars: int = 0
    status: str
    status_code: int | None = None
    answer: str | None = None
    answer_truncated: bool = False
    used_model_id: str | None = None
    provider: str | None = None
    source_count: int | None = None
    duration_ms: int | None = None
    error: str | None = None


class ChatAuditLogListResponse(BaseModel):
    checked_at: datetime
    retention_days: int = 7
    content_limit_chars: int = 20_000
    logs: list[ChatAuditLog]


class FrontendRegistrationEvent(BaseModel):
    event_id: int
    occurred_at: datetime
    request_id: str
    event_type: str
    status: str
    client_id: str | None = None
    instance_id: str | None = None
    client_name: str | None = None
    declared_user: str | None = None
    client_version: str | None = None
    source_ip: str | None = None
    registration_type: str | None = None
    identification_method: str | None = None
    is_first_connection: bool = False
    reason: str | None = None


class FrontendRegistrationEventListResponse(BaseModel):
    checked_at: datetime
    retention_policy: Literal["registry_lifetime"] = "registry_lifetime"
    events: list[FrontendRegistrationEvent]


class AICommunicationProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("model_id")
    @classmethod
    def normalize_probe_model_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AICommunicationProbeResponse(BaseModel):
    status: Literal["ok", "unexpected_answer"]
    request_id: str
    checked_at: datetime
    requested_model_id: str
    used_model_id: str
    provider: str
    model: str
    latency_ms: int
    answer_preview: str


class ValidationIssue(BaseModel):
    location: list[str | int]
    message: str
    type: str


class APIError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    issues: list[ValidationIssue] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    request_id: str
    detail: str | list[dict[str, Any]]
    error: APIError


class ClientHeartbeatRequest(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=255)
    project_id: str | None = Field(default=None, min_length=1, max_length=255)
    client_version: str | None = Field(default=None, min_length=1, max_length=100)
    details: dict[str, Any] = Field(default_factory=dict, max_length=50)

    @field_validator("client_id", "project_id", "client_version")
    @classmethod
    def normalize_heartbeat_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("heartbeat identifier must not be blank")
        return normalized


class ClientHeartbeatResponse(BaseModel):
    status: Literal["online"] = "online"
    client_id: str
    project_id: str | None = None
    last_seen_at: datetime


class FrontendConnectivityStatus(BaseModel):
    status: Literal["online", "stale", "offline", "unknown"]
    connected: bool
    client_id: str | None = None
    project_id: str | None = None
    client_version: str | None = None
    last_event: str | None = None
    last_seen_at: datetime | None = None
    age_seconds: int | None = None


class BackendAIConnectivityStatus(BaseModel):
    status: Literal["online", "degraded", "offline"]
    connected: bool
    model_id: str
    model: str
    model_available: bool
    latency_ms: int
    error: Literal["timeout", "unreachable", "model_not_found"] | str | None = None


class ConnectivityStatusResponse(BaseModel):
    checked_at: datetime
    frontend: FrontendConnectivityStatus
    backendai: BackendAIConnectivityStatus


class FrontendClientWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=80)
    ip: str = Field(..., min_length=7, max_length=15)
    port: int = Field(..., ge=1, le=65535)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("frontend client name must not be blank")
        return normalized

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        from .runtime_config import validate_runtime_ip

        try:
            return validate_runtime_ip(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class FrontendClientRecord(BaseModel):
    client_id: str
    instance_id: str | None = None
    name: str
    ip: str
    port: int
    enabled: bool
    registration_type: Literal["admin", "auto"] = "admin"
    last_seen_ip: str | None = None
    last_seen_at: datetime | None = None
    reachable: bool
    latency_ms: int
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class FrontendClientListResponse(BaseModel):
    clients: list[FrontendClientRecord]
    total: int
    enabled: int
    reachable: int


class NetworkEndpointSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ip: str = Field(..., min_length=7, max_length=15)
    port: int = Field(..., ge=1, le=65535)

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        from .runtime_config import validate_runtime_ip

        try:
            return validate_runtime_ip(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class NetworkSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontend: NetworkEndpointSettings
    backendai: NetworkEndpointSettings


class NetworkSettingsResponse(BaseModel):
    frontend: NetworkEndpointSettings
    backendai: NetworkEndpointSettings
    updated_at: datetime | None = None
    frontend_reachable: bool
    frontend_latency_ms: int
    frontend_error: str | None = None


class RuntimeGroqSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    base_url: str = Field(..., min_length=8, max_length=2048)
    model: str = Field(..., min_length=1, max_length=255)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        return normalized

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model must not be blank")
        return normalized


class RuntimeGroqSettingsResponse(RuntimeGroqSettingsWrite):
    public_model_id: str
    api_key_configured: bool


class RuntimeVectorSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(..., min_length=1, max_length=253)
    port: int = Field(..., ge=1, le=65535)
    collection: str = Field(..., min_length=1, max_length=255)
    embedding_deployment: Literal["api", "local"]
    embedding_provider: Literal["ollama", "nvidia", "openai"]
    embedding_provider_id: str | None = Field(default=None, max_length=255)
    embedding_base_url: str = Field(..., min_length=1, max_length=2048)
    embedding_model: str = Field(..., min_length=1, max_length=255)
    embedding_model_id: str = Field(..., min_length=1, max_length=255)
    embedding_dimension: int = Field(..., ge=1, le=65_536)
    embedding_batch_size: int = Field(..., ge=1, le=256)
    index_version: str = Field(..., min_length=1, max_length=255)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or "/" in normalized
            or "\\" in normalized
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("host must be an IP address or DNS/service name")
        return normalized

    @field_validator("embedding_base_url")
    @classmethod
    def validate_embedding_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(
                "embedding_base_url must be an absolute HTTP(S) URL"
            )
        return normalized

    @field_validator(
        "collection",
        "embedding_model",
        "embedding_model_id",
        "index_version",
    )
    @classmethod
    def normalize_vector_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("vector setting must not be blank")
        return normalized

    @field_validator("embedding_provider_id")
    @classmethod
    def normalize_embedding_provider_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class RuntimeVectorSettingsResponse(RuntimeVectorSettingsWrite):
    provider: str
    active_host: str
    active_port: int
    active_collection: str
    active_embedding_deployment: Literal["api", "local"]
    active_embedding_provider: str
    active_embedding_provider_id: str | None = None
    active_embedding_base_url: str
    active_embedding_model: str
    active_embedding_model_id: str
    active_embedding_dimension: int
    active_embedding_batch_size: int
    active_index_version: str
    restart_required: bool
    reindex_required: bool


class RuntimeServiceSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groq: RuntimeGroqSettingsWrite
    default_model_id: str = Field(..., min_length=1, max_length=512)
    vector: RuntimeVectorSettingsWrite

    @field_validator("default_model_id")
    @classmethod
    def normalize_default_model_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("default_model_id must not be blank")
        return normalized


class RuntimeServiceSettingsResponse(BaseModel):
    groq: RuntimeGroqSettingsResponse
    default_model_id: str
    vector: RuntimeVectorSettingsResponse
    updated_at: datetime | None = None


UploadEntryType = Literal["file", "directory"]
UploadStatusValue = Literal[
    "created",
    "uploading",
    "queued",
    "indexing",
    "completed",
    "failed",
    "cancelled",
]


class UploadCreateRequest(BaseModel):
    schema_version: str = Field(default="1.0", min_length=1, max_length=20)
    project_id: str = Field(..., min_length=1, max_length=255)
    snapshot_id: str = Field(..., min_length=1, max_length=255)
    document_count: int = Field(..., ge=0, le=10_000)
    total_bytes: int = Field(..., ge=0)
    manifest_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-fA-F0-9]{64}$",
        description=(
            "SHA-256 of manifest entries sorted by relative_path/file_id and "
            "serialized as UTF-8 JSON with sorted keys and compact separators. "
            "The server computes it when omitted and rejects a mismatch."
        ),
    )
    modified_at: datetime | None = None
    git: GitVersionInfo | None = None


class UploadManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9._~-]+$",
    )
    relative_path: str = Field(..., min_length=1, max_length=4096)
    entry_type: UploadEntryType
    size_bytes: int = Field(default=0, ge=0)
    modified_at: datetime | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    language_hint: str | None = Field(default=None, max_length=100)
    content_type_hint: str | None = Field(default=None, max_length=255)
    encoding_hint: str | None = Field(default=None, max_length=100)

    @field_validator("file_id", "relative_path")
    @classmethod
    def normalize_upload_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("upload identifier must not be blank")
        return normalized

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip("/")
        parts = normalized.split("/")
        if not normalized or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("relative_path must be a safe project-relative path")
        if ":" in parts[0]:
            raise ValueError("relative_path must not contain a drive prefix")
        return normalized


class UploadManifestPageRequest(BaseModel):
    page: int = Field(..., ge=1)
    has_more: bool = False
    entries: list[UploadManifestEntry] = Field(..., min_length=1, max_length=1000)


class UploadSessionResponse(BaseModel):
    upload_id: str
    project_id: str
    snapshot_id: str
    status: UploadStatusValue
    part_size: int
    max_concurrency: int = 4
    expires_at: datetime


class UploadProgressResponse(UploadSessionResponse):
    manifest_entries: int = 0
    files_received: int = 0
    bytes_received: int = 0
    documents_processed: int = 0
    chunks_stored: int = 0
    failed_documents: int = 0
    error: str | None = None


class IndexingJobResponse(BaseModel):
    job_id: str
    upload_id: str
    project_id: str
    status: UploadStatusValue
    status_url: str


RepositorySourceType = Literal["git", "local"]
RepositoryJobStatus = Literal[
    "queued",
    "inspecting",
    "snapshotting",
    "chunking",
    "embedding",
    "paused",
    "publishing",
    "completed",
    "failed",
]


class RepositorySourceWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., min_length=1, max_length=255)
    project_id: str = Field(..., min_length=1, max_length=255)
    source_type: RepositorySourceType = "git"
    root_relative_path: str = Field(..., min_length=1, max_length=4096)
    repository_url: str | None = Field(default=None, max_length=4096)
    default_branch: str | None = Field(default="main", max_length=255)
    enabled: bool = True

    @field_validator("source_id", "project_id")
    @classmethod
    def normalize_repository_source_fields(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        if not normalized:
            raise ValueError("repository source field must not be blank")
        return normalized.strip("/")

    @field_validator("root_relative_path")
    @classmethod
    def validate_repository_relative_path(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        parts = normalized.split("/")
        if (
            normalized.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or ":" in parts[0]
        ):
            raise ValueError("root_relative_path must stay below PROJECT_DB_LOCAL_ROOT")
        return normalized


class RepositorySourceRecord(BaseModel):
    source_id: str
    project_id: str
    source_type: RepositorySourceType
    root_relative_path: str
    repository_url: str | None = None
    default_branch: str | None = None
    enabled: bool
    last_revision: str | None = None
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RepositorySourceListResponse(BaseModel):
    sources: list[RepositorySourceRecord]
    total: int = Field(..., ge=0)


RepositoryVersionStatus = Literal[
    "current",
    "different",
    "not_indexed",
    "unavailable",
]


class RepositoryBrowserItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    project_id: str
    project_name: str
    source_type: RepositorySourceType
    repository_url: str | None = None
    default_branch: str | None = None
    enabled: bool
    source_origin: Literal["backend_checkout"] = "backend_checkout"
    source_available: bool
    source_revision: str | None = None
    source_short_revision: str | None = None
    source_branch: str | None = None
    source_dirty: bool | None = None
    source_committed_at: datetime | None = None
    indexed_revision: str | None = None
    indexed_short_revision: str | None = None
    indexed_snapshot_id: str | None = None
    index_status: str
    version_status: RepositoryVersionStatus
    source_tree_url: str
    indexed_tree_url: str
    error: str | None = None


class RepositoryBrowserListResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    repositories: list[RepositoryBrowserItem]
    total: int = Field(..., ge=0)
    generated_at: datetime


class RepositorySourceTreeResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    source_id: str
    project_id: str
    source_origin: Literal["backend_checkout"] = "backend_checkout"
    repository_url: str | None = None
    revision: str
    branch: str | None = None
    dirty: bool | None = None
    committed_at: datetime | None = None
    prefix: str = ""
    entries: list["ProjectTreeEntry"]
    total: int = Field(..., ge=0)


class OfflineEmbeddingArtifactSummary(BaseModel):
    artifact_id: str
    project_id: str
    snapshot_id: str
    generation_id: str
    model_id: str
    model_name: str
    embedding_dimension: int = Field(..., ge=0)
    index_version: str
    chunk_count: int = Field(..., ge=0)
    shard_count: int = Field(..., ge=0)
    relative_path: str
    compatible: bool
    contract_errors: list[str] = Field(default_factory=list)
    imported: bool
    completed_at: datetime | None = None
    error: str | None = None


class OfflineEmbeddingArtifactListResponse(BaseModel):
    checked_at: datetime
    root_available: bool
    artifacts: list[OfflineEmbeddingArtifactSummary]
    total: int = Field(..., ge=0)
    ready: int = Field(..., ge=0)
    imported: int = Field(..., ge=0)


class RepositoryIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


class RepositoryIndexJobResponse(BaseModel):
    job_id: str
    source_id: str
    project_id: str
    snapshot_id: str | None = None
    generation_id: str | None = None
    status: RepositoryJobStatus
    stage: str
    files_total: int = Field(default=0, ge=0)
    files_processed: int = Field(default=0, ge=0)
    chunks_stored: int = Field(default=0, ge=0)
    bytes_total: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    status_url: str


class IndexingJobSummary(BaseModel):
    job_id: str
    job_kind: Literal["repository", "upload"]
    project_id: str
    source_id: str | None = None
    upload_id: str | None = None
    state: str
    stage: str
    active: bool
    stalled: bool = False
    progress_percent: float = Field(..., ge=0, le=100)
    processed: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    files_processed: int = Field(default=0, ge=0)
    files_total: int = Field(default=0, ge=0)
    chunks_stored: int = Field(default=0, ge=0)
    bytes_processed: int = Field(default=0, ge=0)
    bytes_total: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    status_url: str


class IndexingJobListResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    checked_at: datetime
    jobs: list[IndexingJobSummary]
    total: int = Field(..., ge=0)
    active: int = Field(..., ge=0)


class ProjectTreeEntry(BaseModel):
    path: str
    name: str
    entry_type: UploadEntryType
    language: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    content_sha256: str | None = None
    indexable: bool = False


class ProjectTreeResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    project_id: str
    snapshot_id: str
    generation_id: str
    revision: str | None = None
    prefix: str = ""
    entries: list[ProjectTreeEntry]
    total: int = Field(..., ge=0)


class ProjectFileResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    project_id: str
    snapshot_id: str
    generation_id: str
    path: str
    language: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    content_sha256: str
    content: str


class VectorIndexValidationResponse(BaseModel):
    project_id: str
    snapshot_id: str
    generation_id: str
    postgres_chunks: int = Field(..., ge=0)
    qdrant_chunks: int = Field(..., ge=0)
    consistent: bool
    checked_at: datetime


class LegacyIngestRequest(BaseModel):
    document_id: str = Field(default="doc-001")
    text: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LegacyQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)
