from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from ...schema_guard import SchemaStateError, require_schema


HYDRATION_TOKEN_HEADER = "X-Vision-Hydration-Token"
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class SnapshotHydrationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 500, code: str = "HYDRATION_ERROR") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


from .contracts import (
    SnapshotHydrationEntry,
    SnapshotHydrationFile,
    SnapshotHydrationInfo,
    SnapshotHydrationManifestPage,
)


class HydrationSettingsLike(Protocol):
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_connect_timeout_seconds: int
    snapshot_tenant_id: str


class SnapshotHydrationRepositoryLike(Protocol):
    def get_snapshot(self, snapshot_id: str, tenant_id: str) -> dict[str, Any] | None: ...
    def list_entries(self, snapshot_id: str, tenant_id: str) -> list[dict[str, Any]]: ...
    def get_entry(self, snapshot_id: str, tenant_id: str, path: str) -> dict[str, Any] | None: ...


class PostgresSnapshotHydrationRepository:
    """Read-only facade over the canonical project_snapshots/snapshot_entries store."""

    def __init__(self, settings: HydrationSettingsLike) -> None:
        self._settings = settings
        self._schema_checked = False

    def _connect(self):
        if not self._settings.postgres_password:
            raise SnapshotHydrationError(
                "PostgreSQL Snapshot hydration store is not configured",
                503,
                "HYDRATION_STORE_UNAVAILABLE",
            )
        return psycopg.connect(
            host=self._settings.postgres_host,
            port=self._settings.postgres_port,
            dbname=self._settings.postgres_db,
            user=self._settings.postgres_user,
            password=self._settings.postgres_password,
            connect_timeout=self._settings.postgres_connect_timeout_seconds,
            row_factory=dict_row,
        )

    def ensure_schema(self) -> None:
        if self._schema_checked:
            return
        try:
            with self._connect() as connection:
                require_schema(connection)
            self._schema_checked = True
        except SnapshotHydrationError:
            raise
        except (psycopg.Error, OSError, SchemaStateError) as exc:
            raise SnapshotHydrationError(
                "PostgreSQL schema is not on the required Snapshot hydration baseline",
                503,
                "HYDRATION_SCHEMA_UNAVAILABLE",
            ) from exc

    def get_snapshot(self, snapshot_id: str, tenant_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT ps.*, rs.source_type, rs.repository_url,
                           rs.root_relative_path
                    FROM project_snapshots ps
                    JOIN repository_sources rs ON rs.source_id = ps.source_id
                    WHERE ps.snapshot_id=%s AND ps.tenant_id=%s
                    """,
                    (snapshot_id, tenant_id),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise SnapshotHydrationError(
                "Snapshot hydration lookup failed", 503, "HYDRATION_STORE_UNAVAILABLE"
            ) from exc

    def list_entries(self, snapshot_id: str, tenant_id: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT se.*
                    FROM snapshot_entries se
                    JOIN project_snapshots ps ON ps.snapshot_id = se.snapshot_id
                    WHERE se.snapshot_id=%s AND ps.tenant_id=%s
                    ORDER BY se.relative_path ASC
                    """,
                    (snapshot_id, tenant_id),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise SnapshotHydrationError(
                "Snapshot manifest read failed", 503, "HYDRATION_STORE_UNAVAILABLE"
            ) from exc

    def get_entry(self, snapshot_id: str, tenant_id: str, path: str) -> dict[str, Any] | None:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT se.*, ps.project_id
                    FROM snapshot_entries se
                    JOIN project_snapshots ps ON ps.snapshot_id = se.snapshot_id
                    WHERE se.snapshot_id=%s AND ps.tenant_id=%s
                      AND se.relative_path=%s
                    """,
                    (snapshot_id, tenant_id, path),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise SnapshotHydrationError(
                "Snapshot file read failed", 503, "HYDRATION_STORE_UNAVAILABLE"
            ) from exc


class SnapshotHydrationService:
    def __init__(
        self,
        repository: SnapshotHydrationRepositoryLike,
        *,
        tenant_id: str,
        token: str,
    ) -> None:
        self._repository = repository
        self._tenant_id = tenant_id.strip()
        self._token = token.strip()

    @classmethod
    def from_settings(cls, settings: HydrationSettingsLike) -> "SnapshotHydrationService":
        token = _env_or_file("SNAPSHOT_HYDRATION_TOKEN", "SNAPSHOT_HYDRATION_TOKEN_FILE")
        return cls(
            PostgresSnapshotHydrationRepository(settings),
            tenant_id=settings.snapshot_tenant_id,
            token=token,
        )

    def authorize(self, supplied: str | None) -> None:
        if len(self._token.encode("utf-8")) < 32:
            raise SnapshotHydrationError(
                "Snapshot hydration service credential is not configured",
                503,
                "HYDRATION_AUTH_NOT_CONFIGURED",
            )
        if supplied is None or not supplied.strip():
            raise SnapshotHydrationError(
                "Snapshot hydration credential is required",
                401,
                "HYDRATION_AUTH_REQUIRED",
            )
        if not hmac.compare_digest(supplied.strip(), self._token):
            raise SnapshotHydrationError(
                "Snapshot hydration credential is invalid",
                403,
                "HYDRATION_AUTH_INVALID",
            )

    def info(self, snapshot_id: str) -> SnapshotHydrationInfo:
        snapshot, entries = self._snapshot_and_entries(snapshot_id)
        manifest_sha256 = _manifest_sha256(entries)
        return SnapshotHydrationInfo(
            snapshot_id=str(snapshot["snapshot_id"]),
            project_id=str(snapshot["project_id"]),
            source_type=str(snapshot.get("source_type") or "unknown"),
            snapshot_kind=str(snapshot.get("snapshot_kind") or "working-tree"),
            revision=_optional_text(snapshot.get("revision")),
            branch=_optional_text(snapshot.get("git_branch")),
            dirty=(snapshot.get("git_dirty") if isinstance(snapshot.get("git_dirty"), bool) else None),
            immutable=(
                str(snapshot.get("snapshot_kind") or "") == "git-commit"
                and snapshot.get("git_dirty") is False
            ),
            manifest_sha256=manifest_sha256,
            source_manifest_sha256=_optional_text(snapshot.get("manifest_sha256")),
            file_count=sum(str(item.get("entry_type")) == "file" for item in entries),
            total_bytes=sum(max(0, int(item.get("size_bytes") or 0)) for item in entries if str(item.get("entry_type")) == "file"),
        )

    def manifest(
        self,
        snapshot_id: str,
        *,
        cursor: str | None = None,
        limit: int = 500,
    ) -> SnapshotHydrationManifestPage:
        if limit < 1 or limit > 2000:
            raise SnapshotHydrationError(
                "manifest limit must be between 1 and 2000", 422, "HYDRATION_LIMIT_INVALID"
            )
        snapshot, rows = self._snapshot_and_entries(snapshot_id)
        after = self._decode_cursor(cursor) if cursor else None
        selected = [row for row in rows if after is None or str(row["relative_path"]) > after]
        page = selected[:limit]
        next_cursor = None
        if len(selected) > limit and page:
            next_cursor = self._encode_cursor(str(page[-1]["relative_path"]))
        return SnapshotHydrationManifestPage(
            snapshot_id=str(snapshot["snapshot_id"]),
            project_id=str(snapshot["project_id"]),
            manifest_sha256=_manifest_sha256(rows),
            cursor=cursor,
            next_cursor=next_cursor,
            entries=[_entry_contract(row) for row in page],
        )

    def file(self, snapshot_id: str, path: str) -> SnapshotHydrationFile:
        normalized = normalize_hydration_path(path)
        snapshot = self._repository.get_snapshot(snapshot_id, self._tenant_id)
        if snapshot is None:
            raise SnapshotHydrationError(
                "Snapshot was not found for this tenant", 404, "SNAPSHOT_NOT_FOUND"
            )
        row = self._repository.get_entry(snapshot_id, self._tenant_id, normalized)
        if row is None or str(row.get("entry_type") or "") != "file":
            raise SnapshotHydrationError(
                "Snapshot file was not found", 404, "SNAPSHOT_FILE_NOT_FOUND"
            )
        content_hash = _optional_text(row.get("content_sha256"))
        content = row.get("content")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        encoding = _optional_text(metadata.get("encoding"))
        if not content_hash or not isinstance(content, str) or not encoding:
            raise SnapshotHydrationError(
                "Exact source bytes are not materialized for this file",
                415,
                "SNAPSHOT_FILE_NOT_MATERIALIZED",
            )
        try:
            raw = content.encode(encoding)
        except (LookupError, UnicodeEncodeError) as exc:
            raise SnapshotHydrationError(
                "Stored file text cannot be reconstructed with its source encoding",
                409,
                "SNAPSHOT_FILE_ENCODING_MISMATCH",
            ) from exc
        transport_hash = hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(transport_hash, content_hash.lower()):
            raise SnapshotHydrationError(
                "Stored text does not reconstruct the Snapshot raw-byte SHA-256",
                409,
                "SNAPSHOT_FILE_HASH_MISMATCH",
            )
        return SnapshotHydrationFile(
            snapshot_id=snapshot_id,
            project_id=str(snapshot["project_id"]),
            path=normalized,
            size_bytes=max(0, int(row.get("size_bytes") or len(raw))),
            content_sha256=content_hash.lower(),
            transport_sha256=transport_hash,
            encoding=encoding,
            content=content,
        )

    def _snapshot_and_entries(self, snapshot_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        snapshot = self._repository.get_snapshot(snapshot_id, self._tenant_id)
        if snapshot is None:
            raise SnapshotHydrationError(
                "Snapshot was not found for this tenant", 404, "SNAPSHOT_NOT_FOUND"
            )
        status = str(snapshot.get("status") or "")
        if status in {"deleted", "expired", "gone"}:
            raise SnapshotHydrationError(
                "Snapshot is no longer available", 410, "SNAPSHOT_GONE"
            )
        rows = self._repository.list_entries(snapshot_id, self._tenant_id)
        rows.sort(key=lambda item: str(item.get("relative_path") or ""))
        return snapshot, rows

    def _encode_cursor(self, path: str) -> str:
        if not self._token:
            raise SnapshotHydrationError("Hydration cursor secret is unavailable", 503)
        payload = json.dumps({"after": path}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self._token.encode("utf-8"), encoded, hashlib.sha256).digest()
        return (
            encoded.decode("ascii")
            + "."
            + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        )

    def _decode_cursor(self, cursor: str) -> str:
        try:
            encoded_text, signature_text = cursor.split(".", 1)
            encoded = encoded_text.encode("ascii")
            signature = _b64decode(signature_text)
            expected = hmac.new(self._token.encode("utf-8"), encoded, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("bad signature")
            payload = json.loads(_b64decode(encoded_text).decode("utf-8"))
            after = normalize_hydration_path(str(payload["after"]))
            return after
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SnapshotHydrationError(
                "Hydration cursor is invalid", 422, "HYDRATION_CURSOR_INVALID"
            ) from exc


def normalize_hydration_path(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or raw.startswith("//") or _DRIVE_RE.match(raw):
        raise SnapshotHydrationError(
            "Snapshot path must be a project-relative POSIX path",
            422,
            "HYDRATION_PATH_INVALID",
        )
    pure = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise SnapshotHydrationError(
            "Snapshot path contains an invalid segment", 422, "HYDRATION_PATH_INVALID"
        )
    return pure.as_posix()


def _entry_contract(row: dict[str, Any]) -> SnapshotHydrationEntry:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    encoding = _optional_text(metadata.get("encoding"))
    object_id = _optional_text(metadata.get("object_id") or metadata.get("git_object_id"))
    return SnapshotHydrationEntry(
        path=normalize_hydration_path(str(row["relative_path"])),
        entry_type=str(row.get("entry_type") or "file"),
        language=_optional_text(row.get("language")),
        size_bytes=max(0, int(row.get("size_bytes") or 0)),
        content_sha256=_optional_text(row.get("content_sha256")),
        object_id=object_id,
        indexable=bool(row.get("indexable", False)),
        encoding=encoding,
    )


def _manifest_sha256(rows: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "content_sha256": _optional_text(row.get("content_sha256")),
            "entry_type": str(row.get("entry_type") or "file"),
            "path": normalize_hydration_path(str(row["relative_path"])),
            "size_bytes": max(0, int(row.get("size_bytes") or 0)),
        }
        for row in sorted(rows, key=lambda item: str(item.get("relative_path") or ""))
    ]
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _env_or_file(name: str, file_name: str) -> str:
    file_path = os.getenv(file_name, "").strip()
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""
    return os.getenv(name, "").strip()


def _b64decode(value: str) -> bytes:
    raw = value.encode("ascii")
    raw += b"=" * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode(raw)
