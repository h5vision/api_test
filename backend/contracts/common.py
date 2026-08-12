from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_METADATA_BYTES = 500_000_000
MAX_CHAT_CONTEXT_BYTES = 5_000_000
MAX_CHAT_REQUEST_BYTES = 10_000_000
API_SCHEMA_VERSION = "1.0"


def _validate_metadata_size(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError("metadata must not exceed 500 MB")
    return value


class SearchRequest(BaseModel):
    project_id: str = Field(default="default", min_length=1)
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


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
    embedding_profile_id: str | None = None
    vector_index_id: str | None = None
    snapshot_id: str | None = None
    generation_id: str | None = None
    snapshot_vector_binding_id: str | None = None
    vector_route_revision: int | None = Field(default=None, ge=0)


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


class LegacyIngestRequest(BaseModel):
    document_id: str = Field(default="doc-001")
    text: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LegacyQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


__all__ = [
    "MAX_METADATA_BYTES",
    "MAX_CHAT_CONTEXT_BYTES",
    "MAX_CHAT_REQUEST_BYTES",
    "API_SCHEMA_VERSION",
    "SearchRequest",
    "Source",
    "SearchResponse",
    "ValidationIssue",
    "APIError",
    "ErrorResponse",
    "ClientHeartbeatRequest",
    "ClientHeartbeatResponse",
    "FrontendConnectivityStatus",
    "BackendAIConnectivityStatus",
    "ConnectivityStatusResponse",
    "UploadEntryType",
    "UploadStatusValue",
    "LegacyIngestRequest",
    "LegacyQueryRequest",
]
