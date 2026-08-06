from __future__ import annotations


from datetime import datetime, timezone
from pathlib import Path


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


from backend.admin_snapshots import (
    SnapshotAdminImportResponse,
    _repository_full_name_from_url,
    create_admin_snapshot_router,
)
from backend.config import settings
from backend.snapshots.contracts import (
    RepositoryRecord,
    SnapshotRecord,
    SnapshotRegistrationResponse,
)
from backend.snapshots.service import GithubSnapshotService




NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]




def repository_record() -> RepositoryRecord:
    return RepositoryRecord(
        repository_id="repo_test",
        provider_repository_id="123",
        repository_full_name="octocat/Hello-World",
        repository_url="https://github.com/octocat/Hello-World.git",
        default_branch="master",
        created_at=NOW,
        updated_at=NOW,
    )




def snapshot_record() -> SnapshotRecord:
    return SnapshotRecord(
        snapshot_id="snap_test",
        repository_id="repo_test",
        commit_sha="a" * 40,
        tree_sha="b" * 40,
        fingerprint="c" * 64,
        verified_at=NOW,
        created_at=NOW,
    )




@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://github.com/octocat/Hello-World", "octocat/Hello-World"),
        ("https://github.com/octocat/Hello-World.git/", "octocat/Hello-World"),
        ("https://www.github.com/octocat/Hello-World", "octocat/Hello-World"),
        ("github.com/octocat/Hello-World", "octocat/Hello-World"),
        ("octocat/Hello-World", "octocat/Hello-World"),
    ],
)
def test_repository_url_parser_accepts_supported_public_github_forms(
    value: str,
    expected: str,
) -> None:
    assert _repository_full_name_from_url(value) == expected




@pytest.mark.parametrize(
    "value",
    [
        "http://github.com/octocat/Hello-World",
        "https://gitlab.com/octocat/Hello-World",
        "https://user:secret@github.com/octocat/Hello-World",
        "https://github.com/octocat/Hello-World?tab=readme",
        "https://github.com/octocat/Hello-World/tree/master",
    ],
)
def test_repository_url_parser_rejects_unsafe_or_non_github_addresses(value: str) -> None:
    with pytest.raises(ValueError):
        _repository_full_name_from_url(value)




def test_service_admin_import_uses_default_branch_without_public_allowlist() -> None:
    service = object.__new__(GithubSnapshotService)
    repository = repository_record()
    registration = SnapshotRegistrationResponse(
        snapshot=snapshot_record(),
        deduplicated=False,
    )
    observed: dict[str, str] = {}


    def register(value: str) -> RepositoryRecord:
        observed["repository"] = value
        return repository


    def create(repository_id: str, ref: str) -> SnapshotRegistrationResponse:
        observed["repository_id"] = repository_id
        observed["ref"] = ref
        return registration


    service._register_public_repository = register  # type: ignore[method-assign]
    service.create_snapshot = create  # type: ignore[method-assign]


    result_repository, result_registration, resolved_ref = (
        GithubSnapshotService.import_public_repository_snapshot(
            service,
            "octocat/Hello-World",
            None,
        )
    )


    assert result_repository == repository
    assert result_registration == registration
    assert resolved_ref == "master"
    assert observed == {
        "repository": "octocat/Hello-World",
        "repository_id": "repo_test",
        "ref": "master",
    }




class FakeAdminImportService:
    tenant_id = "vision-default"


    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []


    def import_public_repository_snapshot(
        self,
        repository_full_name: str,
        ref: str | None = None,
    ) -> tuple[RepositoryRecord, SnapshotRegistrationResponse, str]:
        self.calls.append((repository_full_name, ref))
        repository = repository_record()
        registration = SnapshotRegistrationResponse(
            snapshot=snapshot_record(),
            deduplicated=False,
        )
        return repository, registration, ref or repository.default_branch




def test_admin_import_endpoint_is_proxy_guarded_and_returns_repo_and_snapshot() -> None:
    fake = FakeAdminImportService()
    guard_calls: list[str] = []
    app = FastAPI()
    app.include_router(
        create_admin_snapshot_router(
            settings,
            lambda request: guard_calls.append(request.url.path),
            service_factory=lambda: fake,
        )
    )
    client = TestClient(app)


    response = client.post(
        "/v1/admin/snapshots/import",
        json={
            "repository_url": "https://github.com/octocat/Hello-World",
            "ref": "master",
        },
    )


    assert response.status_code == 201
    payload = SnapshotAdminImportResponse.model_validate(response.json())
    assert payload.repository.repository_full_name == "octocat/Hello-World"
    assert payload.snapshot.snapshot_id == "snap_test"
    assert payload.resolved_ref == "master"
    assert fake.calls == [("octocat/Hello-World", "master")]
    assert guard_calls == ["/v1/admin/snapshots/import"]




def test_snapshot_frontend_contains_repository_import_form_and_post_contract() -> None:
    source = (ROOT / "admin" / "src" / "snapshots.ts").read_text(encoding="utf-8")
    assert 'id="snapshot-import-form"' in source
    assert 'id="snapshot-import-url"' in source
    assert 'id="snapshot-import-ref"' in source
    assert '"/snapshots/import"' in source
    assert 'method: "POST"' in source
    assert "X-Vision-Snapshot-Token" not in source
