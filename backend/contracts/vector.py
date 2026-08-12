from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
class VectorTargetWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Qdrant", min_length=1, max_length=255)
    endpoint: str = Field(..., min_length=8, max_length=2048)
    credential_ref: str | None = Field(default=None, max_length=512)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("endpoint must be an absolute HTTP(S) URL")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("endpoint must not contain path, query, or fragment")
        return normalized
class VectorTargetRecordResponse(BaseModel):
    vector_target_id: str
    tenant_id: str
    name: str
    engine: str
    endpoint: str
    credential_ref: str | None = None
    deployment_type: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    status: str
    error: str | None = None
    latency_ms: int | None = None
    last_checked_at: datetime | None = None
    active: bool = False
    created_at: datetime
    updated_at: datetime
class VectorTargetListResponse(BaseModel):
    targets: list[VectorTargetRecordResponse] = Field(default_factory=list)
    active_vector_target_id: str | None = None
class VectorIndexRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vector_index_id: str
    tenant_id: str
    name: str
    vector_target_id: str
    embedding_profile_id: str
    collection: str
    selector: dict[str, Any]
    index_version: str
    distance_metric: Literal["cosine", "dot", "euclid", "manhattan"]
    ownership_mode: Literal["vision_managed", "external_attached"]
    query_strategy: str
    status: Literal["building", "ready", "retired", "unavailable", "disabled"]
    created_at: datetime
    updated_at: datetime
class VectorIndexListResponse(BaseModel):
    indexes: list[VectorIndexRecordResponse]
    total: int = Field(..., ge=0)
class ExternalVectorIndexDiscoveryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection: str
    dimension: int | None = None
    distance_metric: str | None = None
    vector_type: str | None = None
    points_count: int | None = Field(default=None, ge=0)
    status: str
class ExternalVectorIndexDiscoveryResponse(BaseModel):
    vector_target_id: str
    indexes: list[ExternalVectorIndexDiscoveryItem] = Field(default_factory=list)
    total: int = Field(..., ge=0)
class ExternalVectorIndexAttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    vector_target_id: str = Field(..., min_length=1, max_length=255)
    embedding_profile_id: str = Field(..., min_length=1, max_length=255)
    collection: str = Field(..., min_length=1, max_length=255)
    selector: dict[str, str | int | float | bool] = Field(default_factory=dict)
    index_version: str = Field(default="external", min_length=1, max_length=255)
    distance_metric: Literal["cosine", "dot", "euclid", "manhattan"] | None = None
    query_strategy: Literal["qdrant-query-api"] = "qdrant-query-api"

    @field_validator("name")
    @classmethod
    def normalize_external_index_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("vector_target_id", "embedding_profile_id", "collection", "index_version")
    @classmethod
    def normalize_external_index_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("external VectorIndex field must not be blank")
        return normalized
class ExternalVectorIndexVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedding_profile_attested: bool = False
    sample_limit: int = Field(default=10, ge=1, le=100)
class ExternalVectorIndexVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vector_index_id: str
    tenant_id: str
    verification_state: Literal["unverified", "compatible", "incompatible", "unavailable"]
    verification_method: Literal["qdrant_probe"]
    embedding_profile_attested: bool
    expected_dimension: int = Field(..., ge=1)
    observed_dimension: int | None = Field(default=None, ge=1)
    expected_distance_metric: str
    observed_distance_metric: str | None = None
    observed_vector_type: str | None = None
    observed_points_count: int | None = Field(default=None, ge=0)
    selector_points_count: int | None = Field(default=None, ge=0)
    sample_size: int = Field(default=0, ge=0)
    sample_payload_keys: list[str] = Field(default_factory=list)
    last_verified_at: datetime | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
class ExternalVectorIndexAttachResponse(BaseModel):
    index: VectorIndexRecordResponse
    verification: ExternalVectorIndexVerificationResponse
class ExternalSnapshotVectorBindingVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(..., min_length=1, max_length=255)
    vector_index_id: str = Field(..., min_length=1, max_length=255)
    mode: Literal["probe", "manual"] = "probe"
    snapshot_attested: bool = False
    sample_limit: int = Field(default=25, ge=1, le=100)
class SnapshotVectorBindingRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: str
    tenant_id: str
    snapshot_id: str
    vector_index_id: str
    generation_id: str | None = None
    binding_source: Literal["managed_generation", "external_verification"]
    verification_state: Literal["pending", "verified", "failed", "revoked"]
    verification_method: Literal["managed_build", "external_probe", "manual"]
    snapshot_fingerprint: str
    vector_index_identity_key: str
    verification_evidence: dict[str, Any] = Field(default_factory=dict)
    verified_at: datetime | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
class SnapshotVectorBindingListResponse(BaseModel):
    bindings: list[SnapshotVectorBindingRecordResponse]
    total: int = Field(..., ge=0)
class ProjectVectorRouteCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: str
    snapshot_id: str
    generation_id: str | None = None
    generation_status: str | None = None
    vector_index_id: str
    ownership_mode: Literal["vision_managed", "external_attached"]
    binding_source: Literal["managed_generation", "external_verification"]
    verification_method: Literal["managed_build", "external_probe", "manual"]
    vector_target_id: str
    embedding_profile_id: str
    vector_index_status: str
    vector_target_status: str
    embedding_profile_status: str
    external_verification_state: str | None = None
    payload_keys: list[str] = Field(default_factory=list)
    eligible: bool
    routable: bool
    active: bool = False
    reason: str | None = None
class ProjectVectorRouteRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    tenant_id: str
    active_binding_id: str | None = None
    routing_mode: Literal["managed_auto", "pinned"]
    revision: int = Field(..., ge=0)
    selected_by: str | None = None
    selected_at: datetime | None = None
    reason: str | None = None
    active: ProjectVectorRouteCandidateResponse | None = None
    created_at: datetime
    updated_at: datetime
class ProjectVectorRouteWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: str = Field(..., min_length=1, max_length=255)
    routing_mode: Literal["managed_auto", "pinned"] = "pinned"
    expected_revision: int = Field(..., ge=0)
    reason: str | None = Field(default=None, max_length=2000)
class ProjectVectorRouteClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(..., ge=0)
    reason: str | None = Field(default=None, max_length=2000)
class ProjectVectorRouteCandidateListResponse(BaseModel):
    project_id: str
    active_binding_id: str | None = None
    routing_mode: Literal["managed_auto", "pinned"]
    revision: int = Field(..., ge=0)
    candidates: list[ProjectVectorRouteCandidateResponse] = Field(default_factory=list)
class ProjectVectorRouteEventResponse(BaseModel):
    event_id: str
    project_id: str
    tenant_id: str
    from_binding_id: str | None = None
    to_binding_id: str | None = None
    routing_mode: Literal["managed_auto", "pinned"]
    actor: str | None = None
    reason: str | None = None
    revision: int = Field(..., ge=1)
    created_at: datetime
class ProjectVectorRouteEventListResponse(BaseModel):
    project_id: str
    events: list[ProjectVectorRouteEventResponse] = Field(default_factory=list)
class EmbeddingProfileWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Embedding Profile", min_length=1, max_length=255)
    deployment: Literal["api", "local"]
    provider: Literal["ollama", "openai", "nvidia"]
    base_url: str = Field(..., min_length=8, max_length=2048)
    model: str = Field(..., min_length=1, max_length=255)
    model_id: str = Field(..., min_length=1, max_length=255)
    dimension: int = Field(..., ge=1, le=65_536)
    batch_size: int = Field(default=16, ge=1, le=256)
    credential_ref: str | None = Field(default=None, max_length=512)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.fragment:
            raise ValueError("base_url must not contain a fragment")
        return normalized

    @field_validator("name", "model", "model_id")
    @classmethod
    def normalize_embedding_profile_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("embedding profile field must not be blank")
        return normalized
class EmbeddingProfileRecordResponse(BaseModel):
    embedding_profile_id: str
    tenant_id: str
    name: str
    deployment: str
    provider: str
    base_url: str
    model: str
    model_id: str
    dimension: int
    batch_size: int
    credential_ref: str | None = None
    status: str
    error: str | None = None
    latency_ms: int | None = None
    last_checked_at: datetime | None = None
    active: bool = False
    created_at: datetime
    updated_at: datetime
class EmbeddingProfileListResponse(BaseModel):
    profiles: list[EmbeddingProfileRecordResponse] = Field(default_factory=list)
    active_embedding_profile_id: str | None = None
class VectorIndexValidationResponse(BaseModel):
    project_id: str
    snapshot_id: str
    generation_id: str
    postgres_chunks: int = Field(..., ge=0)
    qdrant_chunks: int = Field(..., ge=0)
    consistent: bool
    checked_at: datetime

__all__ = ['VectorDatabaseEngine', 'VectorDatabaseProviderWriteRequest', 'VectorDatabaseProviderRecord', 'VectorDatabaseProviderListResponse', 'VectorTargetWriteRequest', 'VectorTargetRecordResponse', 'VectorTargetListResponse', 'VectorIndexRecordResponse', 'VectorIndexListResponse', 'ExternalVectorIndexDiscoveryItem', 'ExternalVectorIndexDiscoveryResponse', 'ExternalVectorIndexAttachRequest', 'ExternalVectorIndexVerifyRequest', 'ExternalVectorIndexVerificationResponse', 'ExternalVectorIndexAttachResponse', 'ExternalSnapshotVectorBindingVerifyRequest', 'SnapshotVectorBindingRecordResponse', 'SnapshotVectorBindingListResponse', 'ProjectVectorRouteCandidateResponse', 'ProjectVectorRouteRecordResponse', 'ProjectVectorRouteWriteRequest', 'ProjectVectorRouteClearRequest', 'ProjectVectorRouteCandidateListResponse', 'ProjectVectorRouteEventResponse', 'ProjectVectorRouteEventListResponse', 'EmbeddingProfileWriteRequest', 'EmbeddingProfileRecordResponse', 'EmbeddingProfileListResponse', 'VectorIndexValidationResponse']
