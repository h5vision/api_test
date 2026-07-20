from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class DocumentInput(BaseModel):
    document_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    path: str | None = None
    language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestRequest(BaseModel):
    project_id: str = Field(default="default", min_length=1)
    documents: list[DocumentInput] = Field(..., min_length=1, max_length=100)


class IngestResponse(BaseModel):
    project_id: str
    documents_received: int
    chunks_stored: int
    embedding_provider: str


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
