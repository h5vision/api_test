from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import API_SCHEMA_VERSION

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
    catalog_revision: str
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
class OllamaScanTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    base_url: str
    status: Literal["online", "degraded", "offline"]
    models: list[str] = Field(default_factory=list)
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

__all__ = ['ModelInfo', 'ModelListResponse', 'ModelAccessUpdateRequest', 'ModelAccessUpdateResponse', 'OllamaScanTarget', 'OllamaScanResponse']
