from __future__ import annotations

from types import SimpleNamespace

from backend.snapshots.repository import PostgresGithubSnapshotRepository


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, query, params=None):
        self.query = query
        self.params = params
        return self

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        return FakeCursor(self._rows)


def test_snapshot_repository_lists_repositories_and_snapshots(monkeypatch):
    settings = SimpleNamespace(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="vision",
        postgres_user="vision",
        postgres_password="secret",
        postgres_connect_timeout_seconds=5,
    )
    repository = PostgresGithubSnapshotRepository(settings)

    repositories = [
        {
            "repository_id": "repo_abc",
            "repository_full_name": "h5vision/api_test",
            "default_branch": "main",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
    ]
    snapshots = [
        {
            "snapshot_id": "snap_123",
            "repository_id": "repo_abc",
            "snapshot_type": "commit",
            "commit_sha": "a" * 40,
            "tree_sha": "b" * 40,
            "fingerprint": "c" * 64,
            "verified_by": "github",
            "verified_at": "2024-01-01T00:00:00Z",
            "created_at": "2024-01-01T00:00:00Z",
        }
    ]

    def query_router(query, params=None):
        if "FROM snapshot_mvp_snapshots" in query:
            return FakeCursor(snapshots)
        return FakeCursor(repositories)

    class QueryAwareConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            return query_router(query, params)

    monkeypatch.setattr(repository, "_connect", lambda: QueryAwareConnection())

    listed_repositories = repository.list_repositories("tenant-a", limit=10)
    listed_snapshots = repository.list_snapshots_for_tenant("tenant-a", limit=10)

    assert listed_repositories[0]["repository_id"] == "repo_abc"
    assert listed_snapshots[0]["snapshot_id"] == "snap_123"
