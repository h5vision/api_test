from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ...schema_guard import SchemaStateError, require_schema
from .contracts import WorkspaceOverlayRequest, WorkspaceOverlayResponse
from .hydration import SnapshotHydrationError, normalize_hydration_path


class WorkspaceOverlayError(RuntimeError):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class OverlaySettingsLike(Protocol):
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_connect_timeout_seconds: int
    snapshot_tenant_id: str
    embedding_model: str
    embedding_model_id: str
    index_version: str


class WorkspaceOverlayRepositoryLike(Protocol):
    def get_snapshot(self, snapshot_id: str, tenant_id: str) -> dict[str, Any] | None: ...

    def list_entries(self, snapshot_id: str, tenant_id: str) -> list[dict[str, Any]]: ...

    def save_snapshot(
        self,
        snapshot: dict[str, Any],
        entries: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]: ...


class PostgresWorkspaceOverlayRepository:
    def __init__(self, settings: OverlaySettingsLike) -> None:
        self._settings = settings
        self._schema_checked = False

    def _connect(self):
        if not self._settings.postgres_password:
            raise WorkspaceOverlayError("PostgreSQL workspace overlay store is not configured", 503)
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
        except WorkspaceOverlayError:
            raise
        except (psycopg.Error, OSError, SchemaStateError) as exc:
            raise WorkspaceOverlayError(
                "PostgreSQL schema is not on the required workspace overlay baseline",
                503,
            ) from exc

    def get_snapshot(self, snapshot_id: str, tenant_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    "SELECT * FROM project_snapshots WHERE snapshot_id=%s AND tenant_id=%s",
                    (snapshot_id, tenant_id),
                ).fetchone()
        except (psycopg.Error, OSError) as exc:
            raise WorkspaceOverlayError("Workspace base Snapshot lookup failed", 503) from exc

    def list_entries(self, snapshot_id: str, tenant_id: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT se.* FROM snapshot_entries se
                    JOIN project_snapshots ps ON ps.snapshot_id=se.snapshot_id
                    WHERE se.snapshot_id=%s AND ps.tenant_id=%s
                    ORDER BY se.relative_path
                    """,
                    (snapshot_id, tenant_id),
                ).fetchall()
        except (psycopg.Error, OSError) as exc:
            raise WorkspaceOverlayError("Workspace base manifest lookup failed", 503) from exc

    def save_snapshot(
        self,
        snapshot: dict[str, Any],
        entries: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        self.ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO projects (
                        project_id, display_name, embedding_model, embedding_model_id,
                        index_version, index_status
                    ) VALUES (%s, %s, %s, %s, %s, 'not_indexed')
                    ON CONFLICT (project_id) DO NOTHING
                    """,
                    (
                        snapshot["project_id"],
                        snapshot["project_id"].rsplit("/", 1)[-1],
                        self._settings.embedding_model,
                        self._settings.embedding_model_id,
                        self._settings.index_version,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO repository_sources (
                        source_id, project_id, source_type, root_relative_path,
                        default_branch, last_revision, last_synced_at, tenant_id
                    ) VALUES (%s, %s, 'frontend-workspace', '.', %s, %s, NOW(), %s)
                    ON CONFLICT (source_id) DO UPDATE SET
                        default_branch=COALESCE(EXCLUDED.default_branch, repository_sources.default_branch),
                        last_revision=COALESCE(EXCLUDED.last_revision, repository_sources.last_revision),
                        last_synced_at=NOW(), updated_at=NOW()
                    """,
                    (
                        snapshot["source_id"],
                        snapshot["project_id"],
                        snapshot.get("git_branch"),
                        snapshot.get("revision"),
                        snapshot["tenant_id"],
                    ),
                )
                existing = connection.execute(
                    """
                    SELECT * FROM project_snapshots
                    WHERE snapshot_id=%s OR fingerprint=%s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (snapshot["snapshot_id"], snapshot["fingerprint"]),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["project_id"]) != snapshot["project_id"]
                        or str(existing["source_id"]) != snapshot["source_id"]
                        or str(existing.get("fingerprint") or "") != snapshot["fingerprint"]
                    ):
                        raise WorkspaceOverlayError(
                            "Workspace Snapshot identity is bound to different content",
                            409,
                        )
                    return existing, True

                row = connection.execute(
                    """
                    INSERT INTO project_snapshots (
                        snapshot_id, project_id, source_id, revision, git_branch,
                        git_dirty, manifest_sha256, file_count, total_bytes, status,
                        tenant_id, snapshot_kind, fingerprint, verified_by,
                        verified_at, locator, completed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, TRUE, %s, %s, %s, 'completed',
                        %s, 'working-tree', %s, 'frontend-git-observation',
                        NOW(), %s, NOW()
                    ) RETURNING *
                    """,
                    (
                        snapshot["snapshot_id"],
                        snapshot["project_id"],
                        snapshot["source_id"],
                        snapshot.get("revision"),
                        snapshot.get("git_branch"),
                        snapshot["manifest_sha256"],
                        snapshot["file_count"],
                        snapshot["total_bytes"],
                        snapshot["tenant_id"],
                        snapshot["fingerprint"],
                        Jsonb(snapshot["locator"]),
                    ),
                ).fetchone()
                connection.executemany(
                    """
                    INSERT INTO snapshot_entries (
                        snapshot_id, relative_path, name, entry_type, language,
                        size_bytes, content_sha256, content, indexable, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            snapshot["snapshot_id"],
                            entry["relative_path"],
                            entry["name"],
                            entry["entry_type"],
                            entry.get("language"),
                            entry["size_bytes"],
                            entry.get("content_sha256"),
                            entry.get("content"),
                            entry["indexable"],
                            Jsonb(entry.get("metadata") or {}),
                        )
                        for entry in entries
                    ],
                )
                return row, False
        except WorkspaceOverlayError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise WorkspaceOverlayError("Workspace Snapshot transaction failed", 503) from exc


class WorkspaceOverlayService:
    def __init__(
        self,
        repository: WorkspaceOverlayRepositoryLike,
        *,
        tenant_id: str,
    ) -> None:
        self._repository = repository
        self._tenant_id = tenant_id.strip()

    @classmethod
    def from_settings(cls, settings: OverlaySettingsLike) -> "WorkspaceOverlayService":
        return cls(
            PostgresWorkspaceOverlayRepository(settings),
            tenant_id=settings.snapshot_tenant_id,
        )

    def create(self, payload: WorkspaceOverlayRequest) -> WorkspaceOverlayResponse:
        base: dict[str, Any] | None = None
        entries: dict[str, dict[str, Any]] = {}
        if payload.base_snapshot_id:
            base = self._repository.get_snapshot(payload.base_snapshot_id, self._tenant_id)
            if base is None:
                raise WorkspaceOverlayError("Base Snapshot was not found", 404)
            if str(base.get("project_id") or "") != payload.project_id:
                raise WorkspaceOverlayError("Base Snapshot belongs to another project", 409)
            if str(base.get("status") or "") != "completed":
                raise WorkspaceOverlayError("Base Snapshot is not completed", 409)
            base_revision = str(base.get("revision") or "").strip().lower() or None
            if payload.base_commit_sha and base_revision and payload.base_commit_sha != base_revision:
                raise WorkspaceOverlayError(
                    "base_commit_sha does not match the selected base Snapshot",
                    409,
                )
            entries = {
                _overlay_path(str(entry["relative_path"])): dict(entry)
                for entry in self._repository.list_entries(
                    payload.base_snapshot_id,
                    self._tenant_id,
                )
            }

        normalized_files: dict[str, Any] = {}
        for item in payload.files:
            path = _overlay_path(item.path)
            if path in normalized_files:
                raise WorkspaceOverlayError(f"Duplicate file path: {path}", 422)
            normalized_files[path] = item

        for rename in payload.renames:
            old_path = _overlay_path(rename.old_path)
            new_path = _overlay_path(rename.new_path)
            if old_path == new_path:
                continue
            original = entries.pop(old_path, None)
            if original is None and new_path not in normalized_files:
                raise WorkspaceOverlayError(f"Rename source was not found: {old_path}", 409)
            if original is not None:
                original["relative_path"] = new_path
                original["name"] = PurePosixPath(new_path).name
                metadata = dict(original.get("metadata") or {})
                metadata["renamed_from"] = old_path
                original["metadata"] = metadata
                entries[new_path] = original

        for raw_path in payload.deleted_paths:
            entries.pop(_overlay_path(raw_path), None)

        for path, item in normalized_files.items():
            raw = item.content.encode("utf-8")
            entries[path] = {
                "relative_path": path,
                "name": PurePosixPath(path).name,
                "entry_type": "file",
                "language": item.language,
                "size_bytes": len(raw),
                "content_sha256": hashlib.sha256(raw).hexdigest(),
                "content": item.content,
                "indexable": True,
                "metadata": {"encoding": "utf-8", "source": "frontend-workspace"},
            }

        ordered = [entries[path] for path in sorted(entries)]
        manifest_sha256 = _manifest_sha256(ordered)
        file_count = sum(entry.get("entry_type") == "file" for entry in ordered)
        total_bytes = sum(
            max(0, int(entry.get("size_bytes") or 0))
            for entry in ordered
            if entry.get("entry_type") == "file"
        )
        source_id = str((base or {}).get("source_id") or "").strip()
        if not source_id:
            source_digest = hashlib.sha256(
                f"{self._tenant_id}:{payload.project_id}:frontend-workspace".encode("utf-8")
            ).hexdigest()
            source_id = f"src_workspace_{source_digest[:24]}"
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "base_snapshot_id": payload.base_snapshot_id,
                    "branch": payload.branch,
                    "head_commit_sha": payload.head_commit_sha,
                    "manifest_sha256": manifest_sha256,
                    "project_id": payload.project_id,
                    "source_id": source_id,
                    "tenant_id": self._tenant_id,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        snapshot_id = f"snap_{fingerprint[:24]}"
        created_at = datetime.now(timezone.utc)
        snapshot = {
            "snapshot_id": snapshot_id,
            "project_id": payload.project_id,
            "source_id": source_id,
            "revision": payload.head_commit_sha,
            "git_branch": payload.branch,
            "manifest_sha256": manifest_sha256,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "tenant_id": self._tenant_id,
            "fingerprint": fingerprint,
            "locator": {
                "type": "materialized-workspace",
                "base_snapshot_id": payload.base_snapshot_id,
                "full_snapshot": payload.full_snapshot,
            },
            "created_at": created_at,
        }
        stored, deduplicated = self._repository.save_snapshot(snapshot, ordered)
        stored_created_at = stored.get("created_at")
        return WorkspaceOverlayResponse(
            project_id=payload.project_id,
            snapshot_id=str(stored.get("snapshot_id") or snapshot_id),
            base_snapshot_id=payload.base_snapshot_id,
            revision=payload.head_commit_sha,
            branch=payload.branch,
            manifest_sha256=manifest_sha256,
            file_count=file_count,
            total_bytes=total_bytes,
            deduplicated=deduplicated,
            created_at=(
                stored_created_at
                if isinstance(stored_created_at, datetime)
                else created_at
            ),
            hydration={
                "info": f"/v1/snapshot-hydrations/{snapshot_id}",
                "manifest": f"/v1/snapshot-hydrations/{snapshot_id}/manifest",
                "file": f"/v1/snapshot-hydrations/{snapshot_id}/file?path={{path}}",
            },
        )


def _manifest_sha256(entries: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "content_sha256": str(entry.get("content_sha256") or "") or None,
            "entry_type": str(entry.get("entry_type") or "file"),
            "path": normalize_hydration_path(str(entry["relative_path"])),
            "size_bytes": max(0, int(entry.get("size_bytes") or 0)),
        }
        for entry in sorted(entries, key=lambda item: str(item.get("relative_path") or ""))
    ]
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _overlay_path(value: str) -> str:
    try:
        return normalize_hydration_path(value)
    except SnapshotHydrationError as exc:
        raise WorkspaceOverlayError(str(exc), exc.status_code) from exc
