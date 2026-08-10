from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


GIT_OBJECT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
REPOSITORY_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SNAPSHOT_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")

SnapshotKind = Literal["git-commit", "working-tree", "upload"]
SnapshotVerifier = Literal["local", "frontend", "github"]




def canonical_snapshot_kind(revision: str | None, dirty: bool | None) -> SnapshotKind:
    normalized = (revision or "").strip().lower()
    if dirty is False and GIT_OBJECT_SHA_RE.fullmatch(normalized):
        return "git-commit"
    return "working-tree"

def normalize_git_sha(value: str) -> str:
    normalized = value.strip().lower()
    if not GIT_OBJECT_SHA_RE.fullmatch(normalized):
        raise ValueError("Git object SHA must contain exactly 40 or 64 hexadecimal characters")
    return normalized


def normalize_repository_full_name(value: str) -> str:
    normalized = value.strip().strip("/")
    if normalized.lower().endswith(".git"):
        normalized = normalized[:-4]
    if not REPOSITORY_FULL_NAME_RE.fullmatch(normalized):
        raise ValueError("repository_full_name must use the owner/name form")
    return normalized


def repository_full_name_from_url(value: str) -> str:
    raw = value.strip()
    if not raw or "\x00" in raw:
        raise ValueError("GitHub repository address must not be blank")
    if "://" not in raw:
        lowered = raw.casefold()
        if lowered.startswith("github.com/") or lowered.startswith("www.github.com/"):
            raw = "https://" + raw
        else:
            return normalize_repository_full_name(raw)
    parts = urlsplit(raw)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("GitHub repository address contains an invalid port") from exc
    if parts.scheme.casefold() != "https":
        raise ValueError("Only HTTPS GitHub repository addresses are accepted")
    if (parts.hostname or "").casefold() not in {"github.com", "www.github.com"}:
        raise ValueError("Only github.com repository addresses are accepted")
    if parts.username is not None or parts.password is not None or port is not None:
        raise ValueError("GitHub repository address must not contain credentials or a port")
    if parts.query or parts.fragment:
        raise ValueError("GitHub repository address must not contain a query or fragment")
    return normalize_repository_full_name(parts.path.strip("/"))


def repository_id_from_provider_id(tenant_id: str, provider: str, provider_repository_id: str) -> str:
    canonical = f"{tenant_id.strip()}:{provider.strip().lower()}:{provider_repository_id.strip()}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"repo_{digest[:24]}"


def snapshot_fingerprint(
    *,
    tenant_id: str,
    repository_id: str,
    snapshot_kind: SnapshotKind,
    revision: str | None = None,
    manifest_sha256: str | None = None,
    tree_sha: str | None = None,
) -> str:
    normalized_manifest = (manifest_sha256 or "").strip().lower() or None
    if normalized_manifest is not None and not re.fullmatch(r"[0-9a-f]{64}", normalized_manifest):
        raise ValueError("manifest_sha256 must be a SHA-256 hex digest")
    normalized_revision = (revision or "").strip().lower() or None
    if snapshot_kind == "git-commit" and normalized_revision is not None:
        normalized_revision = normalize_git_sha(normalized_revision)
    normalized_tree = (tree_sha or "").strip().lower() or None
    if normalized_tree is not None:
        normalized_tree = normalize_git_sha(normalized_tree)
    canonical = json.dumps(
        {
            "manifest_sha256": normalized_manifest,
            "repository_id": repository_id.strip(),
            "revision": normalized_revision,
            "snapshot_kind": snapshot_kind,
            "tenant_id": tenant_id.strip(),
            "tree_sha": normalized_tree,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot_id_from_fingerprint(fingerprint: str) -> str:
    normalized = fingerprint.strip().lower()
    if not SNAPSHOT_FINGERPRINT_RE.fullmatch(normalized):
        raise ValueError("snapshot fingerprint must be a SHA-256 hex digest")
    return f"snap_{normalized[:24]}"


class RepositoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: str
    tenant_id: str
    project_id: str
    source_type: str
    repository_url: str | None = None
    default_branch: str | None = None
    provider_repository_id: str | None = None
    enabled: bool = True
    created_at: datetime
    updated_at: datetime


class SnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    tenant_id: str
    repository_id: str
    project_id: str
    snapshot_kind: SnapshotKind
    revision: str | None = None
    git_branch: str | None = None
    git_dirty: bool | None = None
    git_committed_at: datetime | None = None
    tree_sha: str | None = None
    manifest_sha256: str | None = None
    fingerprint: str
    verified_by: SnapshotVerifier
    verified_at: datetime | None = None
    file_count: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    status: str
    locator: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    completed_at: datetime | None = None


class SnapshotImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_url: str = Field(..., min_length=3, max_length=500)
    ref: str | None = Field(default=None, max_length=255)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        repository_full_name_from_url(value)
        return value.strip()

    @field_validator("ref")
    @classmethod
    def normalize_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if "\x00" in normalized:
            raise ValueError("ref contains an invalid character")
        return normalized or None


class SnapshotImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: RepositoryRecord
    snapshot: SnapshotRecord
    deduplicated: bool
    resolved_ref: str
    github_authentication: Literal["authenticated", "anonymous"]

