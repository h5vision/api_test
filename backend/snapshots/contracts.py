from __future__ import annotations


import hashlib
import json
import re
from datetime import datetime
from typing import Literal
from urllib.parse import unquote


from pydantic import BaseModel, ConfigDict, Field, field_validator




GIT_OBJECT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
REPOSITORY_FULL_NAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
)
SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")


SnapshotType = Literal["commit"]
LocatorProvider = Literal["github"]
LocatorAvailability = Literal["durable", "unavailable"]
AccessMode = Literal["backend-proxy", "unavailable"]




def normalize_git_sha(value: str) -> str:
    normalized = value.strip().lower()
    if not GIT_OBJECT_SHA_RE.fullmatch(normalized):
        raise ValueError(
            "Git object SHA must contain exactly 40 or 64 hexadecimal characters"
        )
    return normalized




def normalize_repository_full_name(value: str) -> str:
    normalized = value.strip().strip("/")
    if not REPOSITORY_FULL_NAME_RE.fullmatch(normalized):
        raise ValueError("repository_full_name must use the owner/name form")
    return normalized




def normalize_repository_path(value: str) -> str:
    decoded = unquote(value).replace("\\", "/").strip()
    if not decoded or decoded.startswith("/") or "\x00" in decoded:
        raise ValueError("repository path is invalid")
    parts = decoded.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("repository path contains an unsafe segment")
    return "/".join(parts)




def repository_id_from_provider_id(tenant_id: str, provider_repository_id: str) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}:github:{provider_repository_id}".encode("utf-8")
    ).hexdigest()
    return f"repo_{digest[:24]}"




def snapshot_fingerprint(
    *,
    tenant_id: str,
    provider_repository_id: str,
    commit_sha: str,
) -> str:
    canonical = json.dumps(
        {
            "commit_sha": normalize_git_sha(commit_sha),
            "provider": "github",
            "provider_repository_id": str(provider_repository_id).strip(),
            "snapshot_type": "commit",
            "tenant_id": tenant_id.strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()




def snapshot_id_from_fingerprint(fingerprint: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("snapshot fingerprint must be a SHA-256 hex digest")
    return f"snap_{fingerprint[:24]}"




def locator_id_for_snapshot(tenant_id: str, snapshot_id: str) -> str:
    digest = hashlib.sha256(
        f"{tenant_id}:{snapshot_id}:github:backend-proxy".encode("utf-8")
    ).hexdigest()
    return f"loc_{digest[:24]}"




class RepositoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


    repository_full_name: str = Field(..., min_length=3, max_length=201)


    @field_validator("repository_full_name")
    @classmethod
    def validate_repository_full_name(cls, value: str) -> str:
        return normalize_repository_full_name(value)




class SnapshotCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


    ref: str = Field(default="main", min_length=1, max_length=255)


    @field_validator("ref")
    @classmethod
    def normalize_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("ref must not be blank")
        return normalized




class RepositoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


    repository_id: str
    provider: Literal["github"] = "github"
    provider_repository_id: str
    repository_full_name: str
    repository_url: str
    default_branch: str
    visibility: Literal["public"] = "public"
    created_at: datetime
    updated_at: datetime




class SnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


    snapshot_id: str
    repository_id: str
    snapshot_type: SnapshotType = "commit"
    commit_sha: str
    tree_sha: str
    fingerprint: str
    verified_by: Literal["github"] = "github"
    verified_at: datetime
    created_at: datetime


    @field_validator("commit_sha", "tree_sha")
    @classmethod
    def validate_sha(cls, value: str) -> str:
        return normalize_git_sha(value)




class SnapshotRegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


    snapshot: SnapshotRecord
    deduplicated: bool




class LocatorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


    locator_id: str
    snapshot_id: str
    provider: LocatorProvider = "github"
    access_mode: Literal["backend-proxy"] = "backend-proxy"
    availability: LocatorAvailability = "durable"
    last_verified_at: datetime




class AccessPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")


    snapshot_id: str
    available: bool
    provider: Literal["github"] = "github"
    access_mode: AccessMode
    commit_sha: str
    tree_sha: str
    capabilities: list[Literal["commit.read", "tree.read", "file.read"]]
    tree_endpoint: str | None = None
    file_endpoint: str | None = None
    reason: str | None = None




class TreeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")


    path: str
    entry_type: Literal["blob", "tree"]
    object_sha: str
    size: int | None = Field(default=None, ge=0)
    mode: str


    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_repository_path(value)


    @field_validator("object_sha")
    @classmethod
    def validate_object_sha(cls, value: str) -> str:
        return normalize_git_sha(value)




class SnapshotTreeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


    snapshot_id: str
    repository_id: str
    commit_sha: str
    tree_sha: str
    entries: list[TreeEntry]
    total: int = Field(..., ge=0)




class SnapshotFileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


    snapshot_id: str
    repository_id: str
    commit_sha: str
    tree_sha: str
    path: str
    blob_sha: str
    size: int = Field(..., ge=0)
    encoding: Literal["utf-8"] = "utf-8"
    content: str


    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_repository_path(value)


    @field_validator("commit_sha", "tree_sha", "blob_sha")
    @classmethod
    def validate_object_sha(cls, value: str) -> str:
        return normalize_git_sha(value)




class SnapshotListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


    repository_id: str
    snapshots: list[SnapshotRecord]
    total: int = Field(..., ge=0)
