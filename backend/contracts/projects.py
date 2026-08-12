from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import API_SCHEMA_VERSION, UploadEntryType, _validate_metadata_size

IndexedProjectStatus = Literal[
    "not_indexed",
    "queued",
    "indexing",
    "ready",
    "partially_ready",
    "failed",
    "stale",
]
class IndexedProjectItem(BaseModel):
    project_id: str
    project_name: str
    git_commit_sha: str | None = None
    git_short_sha: str | None = None
    git_branch: str | None = None
    git_dirty: bool | None = None
    git_committed_at: datetime | None = None
    active_snapshot_id: str | None = None
    index_status: IndexedProjectStatus
    indexed_at: datetime | None = None
class IndexedProjectListResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    request_id: str
    projects: list[IndexedProjectItem]
    total: int = Field(..., ge=0)
    generated_at: datetime
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
class GitVersionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    commit_sha: str | None = Field(
        default=None,
        min_length=7,
        max_length=64,
        pattern=r"^[a-fA-F0-9]{7,64}$",
        validation_alias=AliasChoices("commit_sha", "commit", "head"),
    )
    branch: str | None = Field(default=None, min_length=1, max_length=255)
    dirty: bool | None = None
    committed_at: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("committed_at", "committedAt"),
    )

    @field_validator("commit_sha")
    @classmethod
    def normalize_commit_sha(cls, value: str | None) -> str | None:
        return value.lower() if value else None
class ProjectTreeNode(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=512)
    path: str = Field(..., min_length=1, max_length=4096)
    type: Literal["file", "directory"]
    language: str | None = Field(default=None, max_length=100)
    size: int | None = Field(default=None, ge=0)
    modified_time: datetime | None = Field(default=None, alias="modifiedTime")
    children: list["ProjectTreeNode"] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_tree_name(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or normalized in {".", ".."}
            or "/" in normalized
            or "\\" in normalized
        ):
            raise ValueError("tree node name must be a single safe path segment")
        return normalized
class ProjectVersionDescriptor(BaseModel):
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=255)
    manifest_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-fA-F0-9]{64}$",
    )
    structure_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-fA-F0-9]{64}$",
    )
    entry_count: int | None = Field(default=None, ge=1, le=100_000)
    modified_at: datetime | None = None
    git: GitVersionInfo | None = None

    @field_validator("manifest_sha256", "structure_sha256")
    @classmethod
    def normalize_sha256(cls, value: str | None) -> str | None:
        return value.lower() if value else None
class ProjectVersionCheckRequest(ProjectVersionDescriptor):
    model_config = ConfigDict(extra="forbid")

    tree: ProjectTreeNode | None = Field(
        default=None,
        description=(
            "The complete loaded workspace tree. Absolute path values are accepted "
            "for compatibility but ignored when the structure fingerprint is built."
        ),
    )
VersionRelation = Literal[
    "same",
    "client_newer",
    "backend_newer",
    "diverged",
    "unknown",
    "not_found",
]
class ProjectVersionChecks(BaseModel):
    snapshot_id: bool | None = None
    manifest_sha256: bool | None = None
    structure_sha256: bool | None = None
    git_commit_sha: bool | None = None
    git_branch: bool | None = None
    git_dirty: bool | None = None
    modified_at: bool | None = None
class ProjectVersionCheckResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    project_id: str
    backend_registered: bool
    backend_source: Literal["local", "postgresql", "local+postgresql", "none"]
    same_version: bool | None
    relation: VersionRelation
    relation_basis: Literal[
        "exact_match",
        "git_committed_at",
        "modified_at",
        "version_signals",
        "none",
    ]
    checked_at: datetime
    client: ProjectVersionDescriptor
    backend: ProjectVersionDescriptor | None = None
    backend_updated_at: datetime | None = None
    checks: ProjectVersionChecks
    reasons: list[str] = Field(default_factory=list)
class ProjectTreeEntry(BaseModel):
    path: str
    name: str
    entry_type: UploadEntryType
    language: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    content_sha256: str | None = None
    indexable: bool = False
class ProjectTreeResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    project_id: str
    snapshot_id: str
    generation_id: str | None = None
    revision: str | None = None
    prefix: str = ""
    entries: list[ProjectTreeEntry]
    total: int = Field(..., ge=0)
class ProjectFileResponse(BaseModel):
    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    project_id: str
    snapshot_id: str
    generation_id: str | None = None
    path: str
    language: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    content_sha256: str
    content: str
class ProjectBriefingResponse(BaseModel):
    """Stable Vision projection of the external RAG briefing artifact."""

    schema_version: Literal["1.0"] = API_SCHEMA_VERSION
    project_id: str
    external_project_id: str
    briefing: str
    references: list[dict[str, Any]] = Field(default_factory=list)
    reference_files: list[dict[str, Any]] = Field(default_factory=list)
    mentioned_files: list[dict[str, Any]] = Field(default_factory=list)
    structure: dict[str, Any] = Field(default_factory=dict)
    commit: str | None = None
    index_commit: str | None = None
    requested_commit_id: str | None = None
    revision_status: Literal["same", "different", "unknown"] = "unknown"
    generated_at: str | None = None
    outdated: bool = False
    ok: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

__all__ = ['IndexedProjectStatus', 'IndexedProjectItem', 'IndexedProjectListResponse', 'MetadataScope', 'MetadataUpsertRequest', 'MetadataRecord', 'MetadataListResponse', 'GitVersionInfo', 'ProjectTreeNode', 'ProjectVersionDescriptor', 'ProjectVersionCheckRequest', 'VersionRelation', 'ProjectVersionChecks', 'ProjectVersionCheckResponse', 'ProjectTreeEntry', 'ProjectTreeResponse', 'ProjectFileResponse', 'ProjectBriefingResponse']
