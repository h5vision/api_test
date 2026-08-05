from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .config import Settings
from .schemas import GitCaptureStatus, SnapshotCaptureRecord
from .snapshot_control_store import PostgresSnapshotControlStore, SnapshotControlStoreError


class SnapshotCaptureError(RuntimeError):
    pass


class SnapshotCaptureService:
    """Record who observed or created a snapshot from a client workspace."""

    def __init__(
        self,
        settings: Settings,
        store: PostgresSnapshotControlStore | None = None,
    ) -> None:
        self._settings = settings
        self._store = store or PostgresSnapshotControlStore(settings)

    def _row_to_record(self, row: dict[str, Any]) -> SnapshotCaptureRecord:
        git_status = row.get("git_status")
        return SnapshotCaptureRecord(
            capture_id=row["capture_id"],
            snapshot_id=row["snapshot_id"],
            client_id=row["client_id"],
            workspace_id=row.get("workspace_id"),
            local_repository_id=row.get("local_repository_id"),
            git_branch=row.get("git_branch"),
            git_status=git_status if git_status is None else GitCaptureStatus(git_status),
            idempotency_key=row.get("idempotency_key"),
            captured_at=row["captured_at"],
        )

    def record(
        self,
        *,
        snapshot_id: str,
        client_id: str,
        workspace_id: str | None = None,
        local_repository_id: str | None = None,
        git_branch: str | None = None,
        git_status: GitCaptureStatus | None = None,
        idempotency_key: str | None = None,
        captured_at: datetime | None = None,
    ) -> SnapshotCaptureRecord:
        if not self._store.configured:
            raise SnapshotCaptureError("Snapshot capture requires PostgreSQL")

        if idempotency_key:
            existing = self._store.get_capture_by_idempotency(
                client_id, idempotency_key
            )
            if existing and existing["snapshot_id"] == snapshot_id:
                return self._row_to_record(existing)

        capture_id = f"capture_{uuid4().hex[:24]}"
        try:
            row = self._store.insert_capture(
                {
                    "capture_id": capture_id,
                    "snapshot_id": snapshot_id,
                    "client_id": client_id,
                    "workspace_id": workspace_id,
                    "local_repository_id": local_repository_id,
                    "git_branch": git_branch,
                    "git_status": git_status.value if git_status else None,
                    "idempotency_key": idempotency_key,
                    "captured_at": captured_at or datetime.now(timezone.utc),
                }
            )
            return self._row_to_record(row)
        except SnapshotControlStoreError as exc:
            raise SnapshotCaptureError(str(exc)) from exc

    def list_for_client(
        self, client_id: str, *, limit: int = 100
    ) -> list[SnapshotCaptureRecord]:
        try:
            rows = self._store.list_captures_for_client(client_id, limit=limit)
        except SnapshotControlStoreError as exc:
            raise SnapshotCaptureError(str(exc)) from exc
        return [self._row_to_record(row) for row in rows]
