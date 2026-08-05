from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from .config import Settings
from .schemas import (
    SnapshotCreateRequest,
    SnapshotFingerprintInput,
    SnapshotRecord,
    SnapshotRegisterResponse,
    SnapshotType,
    compute_snapshot_fingerprint,
    snapshot_id_from_fingerprint,
)
from .snapshot_control_store import PostgresSnapshotControlStore, SnapshotControlStoreError


class SnapshotRegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotRegistrationResult:
    snapshot: SnapshotRecord
    deduplicated: bool
    capture_id: str | None = None


class SnapshotRegistry:
    """Register immutable snapshots with fingerprint-based deduplication."""

    def __init__(
        self,
        settings: Settings,
        store: PostgresSnapshotControlStore | None = None,
    ) -> None:
        self._settings = settings
        self._store = store or PostgresSnapshotControlStore(settings)

    @staticmethod
    def build_fingerprint_input(
        *,
        repository_id: str,
        snapshot_type: SnapshotType,
        commit_sha: str | None = None,
        tree_sha: str | None = None,
        manifest_sha256: str | None = None,
    ) -> SnapshotFingerprintInput:
        return SnapshotFingerprintInput(
            repository_id=repository_id,
            snapshot_type=snapshot_type,
            commit_sha=commit_sha,
            tree_sha=tree_sha,
            manifest_sha256=manifest_sha256,
        )

    def _ensure_repository(self, payload: SnapshotCreateRequest) -> None:
        display_name = payload.display_name or payload.repository_id
        self._store.upsert_repository(
            {
                "repository_id": payload.repository_id,
                "display_name": display_name,
                "legacy_project_id": payload.legacy_project_id or payload.repository_id,
                "default_branch": payload.git_branch,
                "repository_url": payload.repository_url,
            }
        )

    def _row_to_record(self, row: dict[str, Any]) -> SnapshotRecord:
        return SnapshotRecord(
            snapshot_id=row["snapshot_id"],
            repository_id=row["repository_id"],
            snapshot_type=row["snapshot_type"],
            fingerprint=row["fingerprint"],
            commit_sha=row.get("commit_sha"),
            tree_sha=row.get("tree_sha"),
            manifest_sha256=row.get("manifest_sha256"),
            created_at=row["created_at"],
        )

    def register(
        self,
        payload: SnapshotCreateRequest,
        *,
        idempotency_key: str | None = None,
        client_id: str | None = None,
    ) -> SnapshotRegisterResponse:
        if not self._store.configured:
            raise SnapshotRegistryError("Snapshot registry requires PostgreSQL")

        fingerprint_input = self.build_fingerprint_input(
            repository_id=payload.repository_id,
            snapshot_type=payload.snapshot_type,
            commit_sha=payload.commit_sha,
            tree_sha=payload.tree_sha,
            manifest_sha256=payload.manifest_sha256,
        )
        fingerprint = compute_snapshot_fingerprint(fingerprint_input)

        try:
            self._ensure_repository(payload)
            existing = self._store.get_snapshot_by_fingerprint(
                payload.repository_id, fingerprint
            )
            deduplicated = existing is not None
            if existing:
                snapshot_row = existing
            else:
                snapshot_row = self._store.insert_snapshot(
                    {
                        "snapshot_id": snapshot_id_from_fingerprint(fingerprint),
                        "repository_id": payload.repository_id,
                        "snapshot_type": payload.snapshot_type,
                        "fingerprint": fingerprint,
                        "commit_sha": payload.commit_sha,
                        "tree_sha": payload.tree_sha,
                        "manifest_sha256": payload.manifest_sha256,
                    }
                )

            capture_id: str | None = None
            if client_id:
                from .snapshot_capture import SnapshotCaptureService

                capture_service = SnapshotCaptureService(
                    self._settings, store=self._store
                )
                capture = capture_service.record(
                    snapshot_id=snapshot_row["snapshot_id"],
                    client_id=client_id,
                    workspace_id=payload.workspace_id,
                    local_repository_id=payload.local_repository_id,
                    git_branch=payload.git_branch,
                    git_status=payload.git_status,
                    idempotency_key=idempotency_key,
                )
                capture_id = capture.capture_id

            return SnapshotRegisterResponse(
                snapshot=self._row_to_record(snapshot_row),
                deduplicated=deduplicated,
                capture_id=capture_id,
            )
        except SnapshotControlStoreError as exc:
            raise SnapshotRegistryError(str(exc)) from exc

    def get(self, snapshot_id: str) -> SnapshotRecord | None:
        try:
            row = self._store.get_snapshot(snapshot_id)
        except SnapshotControlStoreError as exc:
            raise SnapshotRegistryError(str(exc)) from exc
        if row is None:
            return None
        return self._row_to_record(row)

    def fingerprint_for(self, payload: SnapshotCreateRequest) -> str:
        return compute_snapshot_fingerprint(
            self.build_fingerprint_input(
                repository_id=payload.repository_id,
                snapshot_type=payload.snapshot_type,
                commit_sha=payload.commit_sha,
                tree_sha=payload.tree_sha,
                manifest_sha256=payload.manifest_sha256,
            )
        )
