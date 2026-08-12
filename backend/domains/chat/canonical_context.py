from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...contracts.common import Source


CANONICAL_CONTEXT_SCHEMA_VERSION = "1.0"


class CanonicalContextMessage(BaseModel):
    """One message assembled by the system that owns prompt construction."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(..., min_length=1, max_length=2_000_000)


class CanonicalContextSource(BaseModel):
    """Evidence in citation order, either hydrated inline or addressable by locator."""

    model_config = ConfigDict(extra="forbid")

    citation_id: int = Field(..., ge=1)
    document_id: str = Field(..., min_length=1, max_length=512)
    document_version_id: str | None = Field(default=None, max_length=512)
    chunk_id: str = Field(..., min_length=1, max_length=512)
    project_id: str = Field(..., min_length=1, max_length=255)
    snapshot_id: str | None = Field(default=None, max_length=255)
    path: str | None = Field(default=None, max_length=4096)
    language: str | None = Field(default=None, max_length=128)
    line_start: int | None = Field(default=None, ge=0)
    line_end: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, max_length=5_000_000)
    score: float
    content_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    locator: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence_location(self) -> "CanonicalContextSource":
        if self.line_start is not None and self.line_end is not None:
            if self.line_end < self.line_start:
                raise ValueError("line_end must be greater than or equal to line_start")
        if not (self.text and self.text.strip()) and not self.locator:
            raise ValueError("canonical source requires hydrated text or a locator")
        if self.text and not self.content_sha256:
            self.content_sha256 = hashlib.sha256(
                self.text.encode("utf-8")
            ).hexdigest()
        return self


class CanonicalContextRetrieval(BaseModel):
    """Retrieval and prompt-ownership facts; these are not client-controlled."""

    model_config = ConfigDict(extra="forbid")

    owner: Literal["vectordb", "vision_legacy"]
    mode: Literal["prompt", "search"]
    prompt_owner: Literal["vectordb", "vision_legacy", "none"]
    provider: str = Field(..., min_length=1, max_length=128)
    endpoint: str | None = Field(default=None, max_length=2048)
    has_evidence: bool
    reason: str = Field(default="ok", min_length=1, max_length=512)
    top_score: float | None = None
    threshold: float | None = None

    @model_validator(mode="after")
    def validate_ownership(self) -> "CanonicalContextRetrieval":
        if self.mode == "prompt" and self.prompt_owner != "vectordb":
            raise ValueError("prompt mode requires VectorDB prompt ownership")
        if self.owner == "vectordb" and self.mode == "search":
            raise ValueError("P3 VectorDB retrieval must use /prompt, not /search")
        return self


class CanonicalContextRetention(BaseModel):
    """P3-A keeps raw evidence request-scoped; durable stores keep identifiers only."""

    model_config = ConfigDict(extra="forbid")

    policy: Literal["request_scoped"] = "request_scoped"
    raw_content_persisted: Literal[False] = False


class CanonicalContext(BaseModel):
    """Final P3-A boundary shared by VectorDB, Vision and the AI Server."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = CANONICAL_CONTEXT_SCHEMA_VERSION
    context_id: str = Field(..., pattern=r"^ctx_[0-9a-f]{32}$")
    request_id: str = Field(..., min_length=1, max_length=255)
    client_id: str | None = Field(default=None, max_length=255)
    project_id: str = Field(..., min_length=1, max_length=255)
    snapshot_id: str = Field(..., min_length=1, max_length=255)
    session_id: str = Field(..., min_length=1, max_length=255)
    query: str = Field(..., min_length=1, max_length=10_000_000)
    retrieval: CanonicalContextRetrieval
    messages: list[CanonicalContextMessage] = Field(default_factory=list)
    sources: list[CanonicalContextSource] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    retention: CanonicalContextRetention = Field(
        default_factory=CanonicalContextRetention
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def validate_context_consistency(self) -> "CanonicalContext":
        expected_citations = list(range(1, len(self.sources) + 1))
        actual_citations = [source.citation_id for source in self.sources]
        if actual_citations != expected_citations:
            raise ValueError("sources must preserve contiguous 1-based citation order")
        if self.retrieval.has_evidence != bool(self.sources):
            raise ValueError("has_evidence must match whether sources are present")
        if (
            self.retrieval.mode == "prompt"
            and self.retrieval.has_evidence
            and not self.messages
        ):
            raise ValueError("VectorDB /prompt evidence requires messages")
        mismatches = [
            source.citation_id
            for source in self.sources
            if source.project_id != self.project_id
            or (
                source.snapshot_id is not None
                and source.snapshot_id != self.snapshot_id
            )
        ]
        if mismatches:
            raise ValueError(
                "canonical sources must belong to the envelope project and snapshot"
            )
        return self


def _context_id(material: dict[str, Any]) -> str:
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"ctx_{hashlib.sha256(encoded).hexdigest()[:32]}"


def canonical_source(
    source: Source,
    *,
    citation_id: int,
    project_id: str,
    snapshot_id: str,
) -> CanonicalContextSource:
    locator = {
        "document_id": source.document_id,
        "chunk_id": source.chunk_id,
    }
    if source.document_version_id:
        locator["document_version_id"] = source.document_version_id
    if source.path:
        locator["path"] = source.path
    return CanonicalContextSource(
        citation_id=citation_id,
        document_id=source.document_id,
        document_version_id=source.document_version_id,
        chunk_id=source.chunk_id,
        project_id=project_id,
        snapshot_id=snapshot_id,
        path=source.path,
        language=source.language,
        line_start=source.line_start,
        line_end=source.line_end,
        text=source.text or None,
        score=source.score,
        locator=locator,
        metadata=dict(source.metadata),
    )


def build_canonical_context(
    *,
    request_id: str,
    client_id: str | None,
    project_id: str,
    snapshot_id: str,
    session_id: str,
    query: str,
    retrieval: CanonicalContextRetrieval,
    sources: Sequence[Source],
    messages: Sequence[dict[str, str] | CanonicalContextMessage] = (),
    provenance: dict[str, Any] | None = None,
) -> CanonicalContext:
    canonical_sources = [
        canonical_source(
            source,
            citation_id=index,
            project_id=project_id,
            snapshot_id=snapshot_id,
        )
        for index, source in enumerate(sources, start=1)
    ]
    canonical_messages = [
        item
        if isinstance(item, CanonicalContextMessage)
        else CanonicalContextMessage.model_validate(item)
        for item in messages
    ]
    identity_material = {
        "schema_version": CANONICAL_CONTEXT_SCHEMA_VERSION,
        "request_id": request_id,
        "client_id": client_id,
        "project_id": project_id,
        "snapshot_id": snapshot_id,
        "session_id": session_id,
        "query": query,
        "retrieval": retrieval.model_dump(mode="json"),
        "messages": [message.model_dump(mode="json") for message in canonical_messages],
        "sources": [source.model_dump(mode="json") for source in canonical_sources],
        "provenance": provenance or {},
    }
    return CanonicalContext(
        context_id=_context_id(identity_material),
        request_id=request_id,
        client_id=client_id,
        project_id=project_id,
        snapshot_id=snapshot_id,
        session_id=session_id,
        query=query,
        retrieval=retrieval,
        messages=canonical_messages,
        sources=canonical_sources,
        provenance=provenance or {},
    )
