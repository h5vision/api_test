"""Shared normalization primitives for Vision backend identifiers and paths.

This module consolidates normalization logic that was previously duplicated
across ``snapshot_compare.py``, ``snapshots/contracts.py``,
``project_resolution.py``, ``language_registry.py``, ``local_projects.py``,
``model_access.py``, ``distributed.py``, and ``admin_snapshots.py``.

Design goals:
    - Pure functions only. No PostgreSQL, FastAPI, or filesystem access here,
      so every function is trivially unit-testable in isolation.
    - Never silently swallow malformed input where the caller needs to know
      it was rejected (validation-style functions raise ``ValueError``).
    - Cosmetic normalization (whitespace, casing, separators) is kept
      separate from *safety* normalization (path traversal, git SHA shape),
      because callers reason about them differently.

Usage:
    from backend.normalization import (
        normalize_optional_text,
        normalize_identifier,
        normalize_git_sha,
        normalize_repository_path,
        normalize_repository_full_name,
        normalize_path_separators,
        reference_key,
        slug_key,
    )
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Generic text normalization
# ---------------------------------------------------------------------------

_BLANK_TOKENS = {"", "none", "null", "undefined"}

_GIT_SHA_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


def normalize_optional_text(value: Any) -> str | None:
    """Trim a string and collapse blank/sentinel values to ``None``.

    Consolidates the pattern previously duplicated as
    ``normalize_optional_identity`` (snapshot_compare.py) and inline
    ``value.strip() or None`` checks scattered across the codebase.

    Non-string input is returned unchanged so this can be used as a
    Pydantic ``field_validator(mode="before")`` without breaking type
    coercion for already-correct types.
    """
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.casefold() in _BLANK_TOKENS:
            return None
        return normalized
    return value


def normalize_identifier(value: str, *, field_name: str = "value") -> str:
    """Trim a required identifier and reject blank results.

    Consolidates ``project_id.strip()`` / ``model_id.strip()`` /
    ``name.strip()`` guards that previously lived independently in
    local_projects.py, model_access.py, and elsewhere.
    """
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def normalize_path_like_identifier(value: str, *, field_name: str = "value") -> str:
    """Normalize an identifier and strip enclosing path separators.

    Matches the project_id normalization in ``snapshot_compare.py``
    (``value.strip().strip("/")``).
    """
    normalized = normalize_identifier(value, field_name=field_name).strip("/")
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def normalize_lock_key(value: str, *, field_name: str = "name") -> str:
    """Normalize a value for use as a Redis lock / cache key segment.

    Matches ``distributed.py``'s ``name.strip().replace(" ", "_")``.
    """
    normalized = value.strip().replace(" ", "_")
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def normalize_commit_sha_case(value: str | None) -> str | None:
    """Lowercase a commit SHA, leaving ``None`` untouched.

    Matches ``normalize_commit_id`` in snapshot_compare.py. This does not
    validate shape; pair with ``normalize_git_sha`` when the value must be
    a real SHA rather than a freeform ref.
    """
    return value.lower() if value else None


# ---------------------------------------------------------------------------
# Git / repository safety normalization
# ---------------------------------------------------------------------------


def normalize_git_sha(value: str) -> str:
    """Validate and lowercase a 40- or 64-character hex git SHA.

    Raises ``ValueError`` for any other length or non-hex content. This is
    the safety-critical counterpart to ``normalize_commit_sha_case``: use
    this one whenever the SHA will be used to key storage, form a URL path,
    or gate an access check.
    """
    if not _GIT_SHA_PATTERN.match(value):
        raise ValueError(f"invalid git SHA length or format: {len(value)} chars")
    return value.lower()


def normalize_repository_path(value: str) -> str:
    """Validate a project-relative POSIX path and reject traversal attempts.

    Rejects: parent-directory segments (``..``), absolute paths (leading
    ``/`` or drive letters), backslash path separators, percent-encoded
    traversal, and embedded NUL bytes. Mirrors the safety checks previously
    only exercised implicitly via ``snapshots/contracts.py`` and
    ``verify_github_snapshot_mvp.py``.
    """
    if not value or "\x00" in value or "%00" in value.casefold():
        raise ValueError(f"unsafe repository path: {value!r}")
    if "%2e%2e" in value.casefold() or "%2f" in value.casefold():
        raise ValueError(f"unsafe repository path: {value!r}")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"unsafe repository path: {value!r}")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError(f"unsafe repository path: {value!r}")
    return "/".join(parts)


def normalize_repository_full_name(value: str) -> str:
    """Normalize a ``owner/repo`` GitHub-style identifier.

    Strips whitespace, a trailing ``.git`` suffix, and surrounding
    slashes, then validates the ``owner/repo`` shape. Consolidates logic
    previously split between ``admin_snapshots.py``'s
    ``_repository_full_name_from_url`` and the contracts-level
    ``normalize_repository_full_name`` it delegated to.
    """
    candidate = value.strip().strip("/")
    if candidate.casefold().endswith(".git"):
        candidate = candidate[:-4]
    if candidate.count("/") != 1 or not all(candidate.split("/")):
        raise ValueError(f"expected owner/repo format, got: {value!r}")
    return candidate


# ---------------------------------------------------------------------------
# Path / display normalization (cosmetic, not safety-critical)
# ---------------------------------------------------------------------------


def normalize_path_separators(value: str | None) -> str:
    """Convert backslashes to forward slashes and strip a leading ``./``.

    Matches ``language_registry.py``'s ``_normalized_path`` and the path
    handling at the top of ``text.py``'s ``classify_index_path``. This is
    intentionally permissive (no traversal rejection) because it is used
    for display/classification, not access control. Use
    ``normalize_repository_path`` instead when the result gates file access.
    """
    if not value:
        return ""
    return value.strip().replace("\\", "/").lstrip("./")


def reference_key(value: str) -> str:
    """Build a casefolded comparison key for fuzzy/alias identifier matching.

    Matches the private ``_reference_key`` helper in
    ``project_resolution.py``. Exposed here so other domains (e.g. Snapshot
    binding lookups) can reuse the same comparison semantics instead of
    reimplementing casefold-based matching independently.
    """
    return value.strip().casefold()


def slug_key(value: str) -> str:
    """Collapse a display string into a comparison-friendly slug.

    Lowercases, collapses runs of whitespace/punctuation to a single
    hyphen, and trims leading/trailing hyphens. Useful anywhere a human
    display name needs to be compared loosely (client names, provider
    names) without each call site inventing its own regex.
    """
    normalized = re.sub(r"[^0-9A-Za-z]+", "-", value.strip().casefold())
    return normalized.strip("-")
