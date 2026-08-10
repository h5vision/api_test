from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .canonical_context import CanonicalContextMessage


VECTOR_SERVICE_SCHEMA_VERSION = "1.0"


class VectorServiceCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = VECTOR_SERVICE_SCHEMA_VERSION
    service_id: str = Field(..., min_length=1, max_length=255)
    conformance_level: Literal["L0", "L1", "L2", "L3"]
    prompt: bool
    search: bool
    snapshot_filter: bool
    source_hydration: bool
    incremental_indexing: bool = False


class VectorEmbeddingDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., min_length=1, max_length=128)
    model: str = Field(..., min_length=1, max_length=512)
    dimension: int = Field(..., ge=1)
    distance_metric: Literal["cosine", "dot", "euclid", "manhattan"]


class ExternalVectorIndexDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = VECTOR_SERVICE_SCHEMA_VERSION
    index_id: str = Field(..., min_length=1, max_length=512)
    project_id: str = Field(..., min_length=1, max_length=255)
    snapshot_id: str = Field(..., min_length=1, max_length=255)
    source_revision: str = Field(..., min_length=1, max_length=255)
    manifest_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    index_version: str = Field(..., min_length=1, max_length=255)
    embedding: VectorEmbeddingDescriptor
    status: Literal["building", "ready", "stale", "failed"]
    indexed_at: datetime | None = None
    capabilities: VectorServiceCapabilities

    @model_validator(mode="after")
    def require_l2_for_ready_index(self) -> "ExternalVectorIndexDescriptor":
        if self.status == "ready":
            level = int(self.capabilities.conformance_level.removeprefix("L"))
            if level < 2 or not self.capabilities.prompt or not self.capabilities.snapshot_filter:
                raise ValueError(
                    "ready external indexes require L2, /prompt and snapshot filtering"
                )
        return self


class ExternalVectorPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = VECTOR_SERVICE_SCHEMA_VERSION
    project_id: str = Field(..., min_length=1, max_length=255)
    snapshot_id: str = Field(..., min_length=1, max_length=255)
    index_id: str = Field(..., min_length=1, max_length=512)
    query: str = Field(..., min_length=1, max_length=10_000_000)


class ExternalVectorPromptSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: int = Field(..., ge=1)
    document_id: str = Field(..., min_length=1, max_length=512)
    document_version_id: str | None = Field(default=None, max_length=512)
    chunk_id: str = Field(..., min_length=1, max_length=512)
    project_id: str = Field(..., min_length=1, max_length=255)
    snapshot_id: str = Field(..., min_length=1, max_length=255)
    path: str | None = Field(default=None, max_length=4096)
    language: str | None = Field(default=None, max_length=128)
    line_start: int | None = Field(default=None, ge=0)
    line_end: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, max_length=5_000_000)
    score: float
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    locator: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_text_or_locator(self) -> "ExternalVectorPromptSource":
        if not (self.text and self.text.strip()) and not self.locator:
            raise ValueError("source requires hydrated text or a locator")
        return self


class ExternalVectorPromptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = VECTOR_SERVICE_SCHEMA_VERSION
    project_id: str = Field(..., min_length=1, max_length=255)
    snapshot_id: str = Field(..., min_length=1, max_length=255)
    index_id: str = Field(..., min_length=1, max_length=512)
    index_version: str = Field(..., min_length=1, max_length=255)
    has_evidence: bool
    reason: str = Field(default="ok", min_length=1, max_length=512)
    messages: list[CanonicalContextMessage] = Field(default_factory=list)
    sources: list[ExternalVectorPromptSource] = Field(default_factory=list)
    top_score: float | None = None
    threshold: float | None = None

    @model_validator(mode="after")
    def validate_prompt_binding(self) -> "ExternalVectorPromptResponse":
        if self.has_evidence != bool(self.sources):
            raise ValueError("has_evidence must match sources")
        if self.has_evidence and not self.messages:
            raise ValueError("evidence requires VectorDB-owned messages")
        citations = [source.citation_id for source in self.sources]
        if citations != list(range(1, len(self.sources) + 1)):
            raise ValueError("sources must preserve contiguous citation order")
        if any(
            source.project_id != self.project_id
            or source.snapshot_id != self.snapshot_id
            for source in self.sources
        ):
            raise ValueError("source project/snapshot does not match prompt response")
        return self


def vector_service_contract() -> dict[str, Any]:
    return {
        "schema_version": VECTOR_SERVICE_SCHEMA_VERSION,
        "minimum_chat_conformance": "L2",
        "required_endpoints": [
            "GET /health",
            "GET /capabilities",
            "GET /projects",
            "GET /indexes?project_id={project_id}",
            "GET /indexes/{index_id}",
            "POST /prompt",
        ],
        "optional_endpoints": [
            "POST /search",
            "GET /sources/{source_id}",
            "GET /index/status",
        ],
        "schemas": {
            "capabilities": VectorServiceCapabilities.model_json_schema(),
            "index_descriptor": ExternalVectorIndexDescriptor.model_json_schema(),
            "prompt_request": ExternalVectorPromptRequest.model_json_schema(),
            "prompt_response": ExternalVectorPromptResponse.model_json_schema(),
        },
    }
