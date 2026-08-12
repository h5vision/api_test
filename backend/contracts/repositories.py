from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import API_SCHEMA_VERSION, UploadEntryType, UploadStatusValue, _validate_metadata_size
from .projects import GitVersionInfo, ProjectTreeEntry

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

__all__ = ['DocumentInput', 'IngestRequest', 'ProjectMetadataDocumentInput', 'ProjectMetadataIngestRequest', 'IngestResponse', 'UploadCreateRequest', 'UploadManifestEntry', 'UploadManifestPageRequest', 'UploadSessionResponse', 'UploadProgressResponse', 'IndexingJobResponse', 'RepositorySourceType', 'RepositoryJobStatus', 'RepositorySourceWriteRequest', 'RepositorySourceRecord', 'RepositorySourceListResponse', 'RepositoryVersionStatus', 'RepositoryBrowserItem', 'RepositoryBrowserListResponse', 'RepositorySourceTreeResponse', 'OfflineEmbeddingArtifactSummary', 'OfflineEmbeddingArtifactListResponse', 'RepositoryIndexRequest', 'RepositoryIndexJobResponse', 'IndexingJobSummary', 'IndexingJobListResponse']
