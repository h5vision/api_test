from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
class ChatSessionMessage(BaseModel):
    request_id: str
    received_at: datetime
    completed_at: datetime | None = None
    question: str | None = None
    question_truncated: bool = False
    answer: str | None = None
    answer_truncated: bool = False
    status: str
    status_code: int | None = None
    requested_model_id: str | None = None
    used_model_id: str | None = None
    provider: str | None = None
    source_count: int | None = None
    duration_ms: int | None = None
    error: str | None = None
class ChatSessionSummary(BaseModel):
    session_id: str
    title: str
    project_id: str
    last_message_at: datetime
    message_count: int = Field(..., ge=1)
    status: str
    model_id: str | None = None
    provider: str | None = None
    messages: list[ChatSessionMessage] = Field(default_factory=list)
class ChatSessionUser(BaseModel):
    user_key: str
    display_name: str
    client_id: str | None = None
    last_message_at: datetime
    sessions: list[ChatSessionSummary] = Field(default_factory=list)
class ChatSessionListResponse(BaseModel):
    checked_at: datetime
    retention_days: int = 7
    users: list[ChatSessionUser] = Field(default_factory=list)
    total_users: int = Field(..., ge=0)
    total_sessions: int = Field(..., ge=0)
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
class FrontendClientWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=80)
    ip: str = Field(..., min_length=7, max_length=15)
    port: int = Field(..., ge=1, le=65535)
    enabled: bool = True
    chat_deep_normalization_mode: Literal["inherit", "auto", "off"] = "inherit"

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
        from ..runtime_config import validate_runtime_ip

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
    chat_deep_normalization_mode: Literal["inherit", "auto", "off"] = "inherit"
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
class ChatIntakeSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deep_normalization_enabled: bool
    fallback_mode: Literal["raw_message"] = "raw_message"
class ChatIntakeSettingsResponse(BaseModel):
    deep_normalization_enabled: bool
    fallback_mode: Literal["raw_message"]
    basic_normalization_enabled: Literal[True] = True
    updated_at: datetime
class NetworkEndpointSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ip: str = Field(..., min_length=7, max_length=15)
    port: int = Field(..., ge=1, le=65535)

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        from ..runtime_config import validate_runtime_ip

        try:
            return validate_runtime_ip(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
class NetworkSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontend: NetworkEndpointSettings
    backendai: NetworkEndpointSettings
class NetworkEndpointSettingsResponse(BaseModel):
    ip: str = ""
    port: int = 0
class NetworkSettingsResponse(BaseModel):
    configured: bool = True
    setup_required: bool = False
    frontend: NetworkEndpointSettingsResponse
    backendai: NetworkEndpointSettingsResponse
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
class RuntimeGroqSettingsResponse(BaseModel):
    enabled: bool = False
    base_url: str = ""
    model: str = ""
    public_model_id: str = "groq-default"
    api_key_configured: bool = False
class RuntimeVectorSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vector_target_id: str | None = Field(default=None, max_length=255)
    embedding_profile_id: str | None = Field(default=None, max_length=255)
    host: str = Field(..., min_length=1, max_length=253)
    port: int = Field(..., ge=1, le=65535)
    collection: str = Field(..., min_length=1, max_length=255)
    embedding_deployment: Literal["api", "local"]
    embedding_provider: Literal["ollama", "openai", "nvidia"]
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
class RuntimeVectorSettingsResponse(BaseModel):
    provider: str = "qdrant"
    vector_target_id: str = ""
    embedding_profile_id: str = ""
    host: str = ""
    port: int = 0
    collection: str = ""
    embedding_deployment: str = ""
    embedding_provider: str = ""
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_model_id: str = ""
    embedding_dimension: int = 0
    embedding_batch_size: int = 0
    index_version: str = ""
    active_host: str = ""
    active_port: int = 0
    active_collection: str = ""
    active_embedding_deployment: str = ""
    active_embedding_provider: str = ""
    active_embedding_base_url: str = ""
    active_embedding_model: str = ""
    active_embedding_model_id: str = ""
    active_embedding_dimension: int = 0
    active_embedding_batch_size: int = 0
    active_index_version: str = ""
    restart_required: bool = False
    reindex_required: bool = False
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
    configured: bool = True
    setup_required: bool = False
    missing: list[str] = Field(default_factory=list)
    groq: RuntimeGroqSettingsResponse
    default_model_id: str = ""
    vector: RuntimeVectorSettingsResponse
    updated_at: datetime | None = None
class RuntimeSetupStatusResponse(BaseModel):
    status: Literal["configured", "setup_required"]
    configured: bool
    missing: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    service_settings_configured: bool
    network_settings_configured: bool
    vector_target_configured: bool = False
    embedding_profile_configured: bool = False

__all__ = ['APIEndpointActivity', 'APIEndpointActivityResponse', 'CommunicationEvent', 'CommunicationEventListResponse', 'ChatAuditLog', 'ChatAuditLogListResponse', 'ChatSessionMessage', 'ChatSessionSummary', 'ChatSessionUser', 'ChatSessionListResponse', 'FrontendRegistrationEvent', 'FrontendRegistrationEventListResponse', 'AICommunicationProbeRequest', 'AICommunicationProbeResponse', 'FrontendClientWriteRequest', 'FrontendClientRecord', 'FrontendClientListResponse', 'ChatIntakeSettingsUpdateRequest', 'ChatIntakeSettingsResponse', 'NetworkEndpointSettings', 'NetworkSettingsUpdateRequest', 'NetworkEndpointSettingsResponse', 'NetworkSettingsResponse', 'RuntimeGroqSettingsWrite', 'RuntimeGroqSettingsResponse', 'RuntimeVectorSettingsWrite', 'RuntimeVectorSettingsResponse', 'RuntimeServiceSettingsUpdateRequest', 'RuntimeServiceSettingsResponse', 'RuntimeSetupStatusResponse']
