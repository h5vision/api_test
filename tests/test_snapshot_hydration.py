from __future__ import annotations

import hashlib

import pytest

from backend.domains.snapshots.hydration import (
    SnapshotHydrationError,
    SnapshotHydrationService,
    normalize_hydration_path,
)


TOKEN = "h" * 40


class FakeHydrationRepository:
    def __init__(self) -> None:
        raw_a = b"print('a')\r\n"
        raw_b = b"hello\n"
        self.snapshot = {
            "snapshot_id": "snap_test",
            "tenant_id": "tenant-a",
            "project_id": "h5vision/api_test",
            "source_type": "git",
            "snapshot_kind": "git-commit",
            "revision": "a" * 40,
            "git_branch": "main",
            "git_dirty": False,
            "manifest_sha256": "f" * 64,
            "status": "completed",
        }
        self.entries = [
            {
                "snapshot_id": "snap_test",
                "relative_path": "z.txt",
                "entry_type": "file",
                "language": "text",
                "size_bytes": len(raw_b),
                "content_sha256": hashlib.sha256(raw_b).hexdigest(),
                "content": raw_b.decode("utf-8"),
                "indexable": True,
                "metadata": {"encoding": "utf-8"},
            },
            {
                "snapshot_id": "snap_test",
                "relative_path": "src/a.py",
                "entry_type": "file",
                "language": "python",
                "size_bytes": len(raw_a),
                "content_sha256": hashlib.sha256(raw_a).hexdigest(),
                "content": raw_a.decode("utf-8"),
                "indexable": True,
                "metadata": {"encoding": "utf-8"},
            },
            {
                "snapshot_id": "snap_test",
                "relative_path": "assets/blob.bin",
                "entry_type": "file",
                "language": None,
                "size_bytes": 4,
                "content_sha256": hashlib.sha256(b"\x00\x01\x02\x03").hexdigest(),
                "content": None,
                "indexable": False,
                "metadata": {"encoding": None},
            },
        ]

    def get_snapshot(self, snapshot_id: str, tenant_id: str):
        if snapshot_id != "snap_test" or tenant_id != "tenant-a":
            return None
        return dict(self.snapshot)

    def list_entries(self, snapshot_id: str, tenant_id: str):
        if self.get_snapshot(snapshot_id, tenant_id) is None:
            return []
        return [dict(item) for item in self.entries]

    def get_entry(self, snapshot_id: str, tenant_id: str, path: str):
        for item in self.list_entries(snapshot_id, tenant_id):
            if item["relative_path"] == path:
                item["project_id"] = self.snapshot["project_id"]
                return item
        return None


def _service() -> SnapshotHydrationService:
    return SnapshotHydrationService(
        FakeHydrationRepository(), tenant_id="tenant-a", token=TOKEN
    )


def test_hydration_info_uses_canonical_manifest_hash() -> None:
    service = _service()
    info = service.info("snap_test")
    assert info.manifest_sha256 != info.source_manifest_sha256
    assert info.immutable is True
    assert info.file_count == 3


def test_manifest_is_sorted_and_cursor_is_signed() -> None:
    service = _service()
    page1 = service.manifest("snap_test", limit=2)
    assert [item.path for item in page1.entries] == ["assets/blob.bin", "src/a.py"]
    assert page1.next_cursor

    page2 = service.manifest("snap_test", limit=2, cursor=page1.next_cursor)
    assert [item.path for item in page2.entries] == ["z.txt"]

    with pytest.raises(SnapshotHydrationError) as exc:
        service.manifest("snap_test", cursor=page1.next_cursor + "x")
    assert exc.value.status_code == 422


def test_file_reconstructs_and_verifies_raw_byte_hash() -> None:
    service = _service()
    value = service.file("snap_test", "src/a.py")
    assert value.transport_sha256 == value.content_sha256
    assert value.content.endswith("\r\n")


def test_binary_file_stays_in_manifest_but_is_not_materialized_as_json_text() -> None:
    service = _service()
    manifest = service.manifest("snap_test")
    binary = next(item for item in manifest.entries if item.path.endswith("blob.bin"))
    assert binary.indexable is False
    assert binary.encoding is None

    with pytest.raises(SnapshotHydrationError) as exc:
        service.file("snap_test", "assets/blob.bin")
    assert exc.value.status_code == 415


def test_path_traversal_and_cross_tenant_access_are_rejected() -> None:
    with pytest.raises(SnapshotHydrationError):
        normalize_hydration_path("../secret.txt")
    with pytest.raises(SnapshotHydrationError):
        normalize_hydration_path("C:\\secret.txt")

    service = SnapshotHydrationService(
        FakeHydrationRepository(), tenant_id="tenant-b", token=TOKEN
    )
    with pytest.raises(SnapshotHydrationError) as exc:
        service.info("snap_test")
    assert exc.value.status_code == 404


def test_service_credential_semantics() -> None:
    service = _service()
    service.authorize(TOKEN)
    with pytest.raises(SnapshotHydrationError) as missing:
        service.authorize(None)
    assert missing.value.status_code == 401
    with pytest.raises(SnapshotHydrationError) as wrong:
        service.authorize("x" * 40)
    assert wrong.value.status_code == 403
