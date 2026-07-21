from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_METADATA_BYTES = 500_000_000


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
    documents: list[DocumentInput] = Field(..., min_length=1, max_length=5000)


class ProjectMetadataDocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=512)
    path: str = Field(..., min_length=1, max_length=4096)
    language: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., min_length=1, max_length=100)

    @field_validator("name", "path", "language", "type")
    @classmethod
    def normalize_document_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("document field must not be blank")
        return normalized


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
    top_k: int = Field(default=5, ge=1, le=20)


class Source(BaseModel):
    document_id: str
    chunk_id: str
    path: str | None = None
    language: str | None = None
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    project_id: str
    query: str
    results: list[Source]
    embedding_provider: str


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    project_id: str | None = Field(default=None, min_length=1)
    message: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=20)

    @field_validator("session_id")
    @classmethod
    def normalize_session_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("session_id must not be blank")
        return normalized


class ChatResponse(BaseModel):
    project_id: str
    session_id: str
    answer: str
    sources: list[Source]
    metadata: dict[str, Any] = Field(default_factory=dict)


class LegacyIngestRequest(BaseModel):
    document_id: str = Field(default="doc-001")
    text: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LegacyQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)
