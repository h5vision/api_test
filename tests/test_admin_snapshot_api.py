from __future__ import annotations


from datetime import datetime, timezone
from types import SimpleNamespace


from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient


from backend.admin_snapshots import create_admin_snapshot_router
from backend.snapshots.contracts import (
    AccessPlan,
    LocatorRecord,
    RepositoryRecord,
    SnapshotFileResponse,
    SnapshotRecord,
    SnapshotTreeResponse,
    TreeEntry,
)
from backend.snapshots.service import GithubSnapshotServiceError




NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
REPOSITORY = RepositoryRecord(
    repository_id="repo_test",
    provider_repository_id="1306244300",
    repository_full_name="h5vision/api_test",
    repository_url="https://github.com/h5vision/api_test.git",
    default_branch="main",
    created_at=NOW,
    updated_at=NOW,
)
SNAPSHOT = SnapshotRecord(
    snapshot_id="snap_test",
    repository_id=REPOSITORY.repository_id,
    commit_sha="a" * 40,
    tree_sha="b" * 40,
    fingerprint="c" * 64,
    verified_at=NOW,
    created_at=NOW,
)
LOCATOR = LocatorRecord(
    locator_id="loc_test",
    snapshot_id=SNAPSHOT.snapshot_id,
    availability="durable",
    last_verified_at=NOW,
)
PLAN = AccessPlan(
    snapshot_id=SNAPSHOT.snapshot_id,
    available=True,
    access_mode="backend-proxy",
    commit_sha=SNAPSHOT.commit_sha,
    tree_sha=SNAPSHOT.tree_sha,
    capabilities=["commit.read", "tree.read", "file.read"],
    tree_endpoint=f"/v1/snapshot-control/snapshots/{SNAPSHOT.snapshot_id}/tree",
    file_endpoint=f"/v1/snapshot-control/snapshots/{SNAPSHOT.snapshot_id}/file",
)




class FakeSnapshotService:
    tenant_id = "vision-default"


    def admin_status(self):
        return {"table_count": 3, "repositories": 1, "snapshots": 1, "locators": 1}


    def list_repositories(self, *, limit=100, offset=0):
        return [REPOSITORY] if offset == 0 else []


    def list_snapshots_for_tenant(self, *, limit=100, offset=0):
        return [SNAPSHOT] if offset == 0 else []


    def get_repository(self, repository_id):
        assert repository_id == REPOSITORY.repository_id
        return REPOSITORY


    def get_snapshot(self, snapshot_id):
        assert snapshot_id == SNAPSHOT.snapshot_id
        return SNAPSHOT


    def get_locator(self, snapshot_id):
        assert snapshot_id == SNAPSHOT.snapshot_id
        return LOCATOR


    def resolve(self, snapshot_id):
        assert snapshot_id == SNAPSHOT.snapshot_id
        return PLAN


    def tree(self, snapshot_id):
        assert snapshot_id == SNAPSHOT.snapshot_id
        return SnapshotTreeResponse(
            snapshot_id=SNAPSHOT.snapshot_id,
            repository_id=REPOSITORY.repository_id,
            commit_sha=SNAPSHOT.commit_sha,
            tree_sha=SNAPSHOT.tree_sha,
            entries=[TreeEntry(path="README.md", entry_type="blob", object_sha="d" * 40, size=12, mode="100644")],
            total=1,
        )


    def file(self, snapshot_id, path):
        assert snapshot_id == SNAPSHOT.snapshot_id
        return SnapshotFileResponse(
            snapshot_id=SNAPSHOT.snapshot_id,
            repository_id=REPOSITORY.repository_id,
            commit_sha=SNAPSHOT.commit_sha,
            tree_sha=SNAPSHOT.tree_sha,
            path=path,
            blob_sha="d" * 40,
            size=12,
            content="# API test\n",
        )




class FailingSnapshotService(FakeSnapshotService):
    def admin_status(self):
        raise GithubSnapshotServiceError("Snapshot storage is unavailable", status_code=503)




def build_client(service):
    app = FastAPI()


    def require_proxy(request: Request):
        if request.headers.get("x-vision-admin-proxy") != "dashboard-internal":
            raise HTTPException(status_code=403, detail="dashboard only")


    app.include_router(
        create_admin_snapshot_router(
            SimpleNamespace(),
            require_proxy,
            service_factory=lambda: service,
        )
    )
    return TestClient(app)




def admin_headers():
    return {"X-Vision-Admin-Proxy": "dashboard-internal"}




def test_admin_snapshot_routes_require_internal_proxy():
    response = build_client(FakeSnapshotService()).get("/v1/admin/snapshots/status")
    assert response.status_code == 403




def test_admin_snapshot_overview_has_exact_counts_and_no_secret():
    response = build_client(FakeSnapshotService()).get(
        "/v1/admin/snapshots?page=1&page_size=50",
        headers=admin_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_repositories"] == 1
    assert body["total_snapshots"] == 1
    assert body["total_locators"] == 1
    assert body["repositories"][0]["repository_full_name"] == "h5vision/api_test"
    assert body["snapshots"][0]["snapshot_id"] == "snap_test"
    assert "SNAPSHOT_MVP_TOKEN" not in response.text




def test_admin_snapshot_detail_tree_and_file():
    client = build_client(FakeSnapshotService())
    detail = client.get("/v1/admin/snapshots/snap_test", headers=admin_headers())
    tree = client.get("/v1/admin/snapshots/snap_test/tree", headers=admin_headers())
    file_response = client.get(
        "/v1/admin/snapshots/snap_test/file",
        params={"path": "README.md"},
        headers=admin_headers(),
    )
    assert detail.status_code == 200
    assert detail.json()["locator"]["availability"] == "durable"
    assert detail.json()["access_plan"]["available"] is True
    assert tree.status_code == 200
    assert tree.json()["entries"][0]["path"] == "README.md"
    assert file_response.status_code == 200
    assert file_response.json()["content"] == "# API test\n"




def test_admin_snapshot_storage_failure_uses_503_for_listing():
    response = build_client(FailingSnapshotService()).get(
        "/v1/admin/snapshots",
        headers=admin_headers(),
    )
    assert response.status_code == 503
