from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ExternalAvailability = Literal["online", "stale", "offline", "unknown"]
BindingMethod = Literal[
    "manual",
    "revision_exact",
    "project_id_exact",
    "leaf_candidate",
]
BindingVerification = Literal["verified", "candidate", "unverified", "conflict"]


class RagTargetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    name: str
    base_url: str
    enabled: bool = True
    availability: ExternalAvailability = "unknown"
    last_seen_at: datetime | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExternalProjectCatalogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    external_project_id: str
    name: str | None = None
    state: str | None = None
    revision: str | None = None
    dirty: bool | None = None
    chunk_count: int | None = Field(default=None, ge=0)
    actual_chunks: int | None = Field(default=None, ge=0)
    indexed_at: datetime | None = None
    fingerprint: dict[str, Any] = Field(default_factory=dict)
    availability: ExternalAvailability = "unknown"
    last_seen_at: datetime | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectExternalBindingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    target_id: str
    external_project_id: str
    binding_method: BindingMethod
    binding_strength: str
    verification_state: BindingVerification
    last_verified_at: datetime | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExternalProjectSyncReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    observed_projects: int
    verified_bindings: int
    candidate_bindings: int
    ambiguous_projects: int
    unbound_projects: int
    stale_projects: int
    availability: ExternalAvailability
    error: str | None = None
