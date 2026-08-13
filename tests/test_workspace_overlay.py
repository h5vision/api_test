from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.domains.snapshots.contracts import WorkspaceOverlayRequest
from backend.domains.snapshots.workspace_overlay import (
    WorkspaceOverlayError,
    WorkspaceOverlayService,
)


BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


class FakeOverlayRepository:
    def __init__(self) -> None:
        self.snapshots = {
            "snap_base": {
                "snapshot_id": "snap_base",
                "project_id": "h5vision/api_test",
                "source_id": "repo_1",
                "revision": BASE_SHA,
                "status": "completed",
                "tenant_id": "vision-default",
            }
        }
        self.entries = {
            "snap_base": [
                self._entry("src/keep.py", "keep\n"),
                self._entry("src/rename.py", "rename\n"),
                self._entry("src/delete.py", "delete\n"),
            ]
        }
        self.saved_entries = []

    @staticmethod
    def _entry(path: str, content: str):
        raw = content.encode("utf-8")
        return {
            "relative_path": path,
            "name": path.rsplit("/", 1)[-1],
            "entry_type": "file",
            "language": "python",
            "size_bytes": len(raw),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
            "content": content,
            "indexable": True,
            "metadata": {"encoding": "utf-8"},
        }

    def get_snapshot(self, snapshot_id, tenant_id):
        value = self.snapshots.get(snapshot_id)
        if value and value["tenant_id"] == tenant_id:
            return dict(value)
        return None

    def list_entries(self, snapshot_id, tenant_id):
        return [dict(entry) for entry in self.entries.get(snapshot_id, [])]

    def save_snapshot(self, snapshot, entries):
        stored = {**snapshot, "created_at": datetime.now(timezone.utc)}
        self.snapshots[snapshot["snapshot_id"]] = stored
        self.entries[snapshot["snapshot_id"]] = [dict(entry) for entry in entries]
        self.saved_entries = self.entries[snapshot["snapshot_id"]]
        return stored, False


def test_incremental_overlay_materializes_complete_snapshot_and_server_hashes():
    repository = FakeOverlayRepository()
    service = WorkspaceOverlayService(repository, tenant_id="vision-default")
    result = service.create(
        WorkspaceOverlayRequest(
            project_id="h5vision/api_test",
            base_snapshot_id="snap_base",
            base_commit_sha=BASE_SHA,
            head_commit_sha=HEAD_SHA,
            branch="frontend",
            files=[
                {
                    "path": "src/new.py",
                    "content": "print('new')\n",
                    "language": "python",
                }
            ],
            deleted_paths=["src/delete.py"],
            renames=[{"old_path": "src/rename.py", "new_path": "src/renamed.py"}],
        )
    )

    by_path = {entry["relative_path"]: entry for entry in repository.saved_entries}
    assert set(by_path) == {"src/keep.py", "src/renamed.py", "src/new.py"}
    assert by_path["src/new.py"]["content_sha256"] == hashlib.sha256(
        b"print('new')\n"
    ).hexdigest()
    assert result.revision == HEAD_SHA
    assert result.snapshot_id.startswith("snap_")
    assert result.file_count == 3
    assert result.hydration["manifest"].endswith("/manifest")


def test_full_snapshot_needs_no_frontend_generated_snapshot_or_content_hash():
    repository = FakeOverlayRepository()
    result = WorkspaceOverlayService(repository, tenant_id="vision-default").create(
        WorkspaceOverlayRequest(
            project_id="local/project",
            full_snapshot=True,
            head_commit_sha=HEAD_SHA,
            files=[{"path": "README.md", "content": "# Project\n"}],
        )
    )

    assert result.snapshot_id.startswith("snap_")
    assert result.manifest_sha256
    assert repository.saved_entries[0]["content_sha256"]


def test_only_real_git_sha_shape_is_accepted():
    with pytest.raises(ValidationError):
        WorkspaceOverlayRequest(
            project_id="h5vision/api_test",
            full_snapshot=True,
            head_commit_sha="frontend-made-version-1",
            files=[{"path": "README.md", "content": "text"}],
        )


def test_workspace_paths_reject_traversal_as_client_error():
    repository = FakeOverlayRepository()
    service = WorkspaceOverlayService(repository, tenant_id="vision-default")

    with pytest.raises(WorkspaceOverlayError) as error:
        service.create(
            WorkspaceOverlayRequest(
                project_id="local/project",
                full_snapshot=True,
                files=[{"path": "../secret.txt", "content": "text"}],
            )
        )

    assert error.value.status_code == 422
