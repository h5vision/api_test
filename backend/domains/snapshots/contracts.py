from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


GIT_SHA_PATTERN = r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$"


class WorkspaceOverlayFile(BaseModel):
    """Final text of one added or modified workspace file."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=4096)
    content: str = Field(..., max_length=16 * 1024 * 1024)
    encoding: Literal["utf-8"] = "utf-8"
    language: str | None = Field(default=None, max_length=100)


class WorkspaceOverlayRename(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_path: str = Field(..., min_length=1, max_length=4096)
    new_path: str = Field(..., min_length=1, max_length=4096)


class WorkspaceOverlayRequest(BaseModel):
    """Frontend working-tree state; supplied hashes must be real Git object IDs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    project_id: str = Field(..., min_length=1, max_length=255)
    base_snapshot_id: str | None = Field(default=None, min_length=1, max_length=255)
    base_commit_sha: str | None = Field(default=None, pattern=GIT_SHA_PATTERN)
    head_commit_sha: str | None = Field(default=None, pattern=GIT_SHA_PATTERN)
    branch: str | None = Field(default=None, max_length=255)
    full_snapshot: bool = False
    files: list[WorkspaceOverlayFile] = Field(default_factory=list, max_length=10_000)
    deleted_paths: list[str] = Field(default_factory=list, max_length=10_000)
    renames: list[WorkspaceOverlayRename] = Field(default_factory=list, max_length=10_000)

    @field_validator("project_id", "branch", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("base_commit_sha", "head_commit_sha", mode="after")
    @classmethod
    def normalize_git_sha(cls, value: str | None) -> str | None:
        return value.lower() if value else None

    @model_validator(mode="after")
    def validate_snapshot_mode(self):
        if self.full_snapshot and self.base_snapshot_id:
            raise ValueError("full_snapshot cannot be combined with base_snapshot_id")
        if not self.full_snapshot and not self.base_snapshot_id:
            raise ValueError("base_snapshot_id is required for an incremental overlay")
        if self.full_snapshot and (self.deleted_paths or self.renames):
            raise ValueError("full_snapshot accepts final files only")
        if not self.files and not self.deleted_paths and not self.renames:
            raise ValueError("at least one file, deletion, or rename is required")
        return self


class WorkspaceOverlayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    project_id: str
    snapshot_id: str
    base_snapshot_id: str | None = None
    revision: str | None = None
    branch: str | None = None
    snapshot_kind: Literal["working-tree"] = "working-tree"
    status: Literal["completed"] = "completed"
    manifest_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    deduplicated: bool = False
    created_at: datetime
    hydration: dict[str, str]


class SnapshotHydrationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    entry_type: str
    language: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    content_sha256: str | None = None
    object_id: str | None = None
    indexable: bool = False
    encoding: str | None = None


class SnapshotHydrationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: str
    project_id: str
    source_type: str
    snapshot_kind: str
    revision: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    immutable: bool
    manifest_sha256: str
    source_manifest_sha256: str | None = None
    file_count: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    capabilities: list[Literal["manifest.read", "file.read"]] = Field(
        default_factory=lambda: ["manifest.read", "file.read"]
    )


class SnapshotHydrationManifestPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: str
    project_id: str
    manifest_sha256: str
    cursor: str | None = None
    next_cursor: str | None = None
    entries: list[SnapshotHydrationEntry]


class SnapshotHydrationFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: str
    project_id: str
    path: str
    size_bytes: int = Field(ge=0)
    content_sha256: str
    transport_sha256: str
    encoding: str
    content: str
