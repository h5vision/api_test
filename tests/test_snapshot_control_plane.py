from __future__ import annotations


import pytest


pytest.skip(
    "Legacy multi-provider Snapshot draft is quarantined by the GitHub Commit MVP",
    allow_module_level=True,
)


import unittest
from datetime import datetime, timezone


from backend.contexts.registry import (
    ContextCreate,
    ContextService,
    SnapshotVectorMismatchError,
)
from backend.snapshots.contracts import (
    AccessMode,
    ConsumerType,
    CredentialStrategy,
    LocatorAvailability,
    LocatorProvider,
    RepositoryIdentity,
    SnapshotDescriptor,
    SnapshotLocatorCreate,
    SnapshotResolveRequest,
    SnapshotType,
    compute_snapshot_fingerprint,
)
from backend.snapshots.resolver import SnapshotResolver
from backend.vector_indexes.registry import VectorIndexRecord, VectorIndexStatus




COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
TREE_A = "c" * 40
MANIFEST_A = "d" * 64
NOW = datetime.now(timezone.utc)




def snapshot_row(snapshot_id: str = "snap_test") -> dict:
    return {
        "snapshot_id": snapshot_id,
        "tenant_id": "default",
        "repository_id": "repo_vision",
        "snapshot_type": "github-commit",
        "fingerprint": "f" * 64,
        "commit_sha": COMMIT_A,
        "tree_sha": TREE_A,
        "base_commit_sha": None,
        "manifest_hash": None,
        "branch": None,
        "tag": None,
        "metadata": {},
        "created_at": NOW,
    }




def locator_row(
    locator_id: str,
    *,
    provider: str,
    availability: str,
    access_mode: str,
    source_client_id: str | None = None,
    capabilities: list[str] | None = None,
) -> dict:
    return {
        "locator_id": locator_id,
        "snapshot_id": "snap_test",
        "provider": provider,
        "availability": availability,
        "access_mode": access_mode,
        "credential_strategy": "none",
        "source_client_id": source_client_id,
        "repository_url": (
            "https://github.com/h5vision/vision.git"
            if provider == "github"
            else None
        ),
        "source_ref": COMMIT_A if provider == "github" else None,
        "artifact_id": None,
        "capabilities": capabilities or ["tree.read", "file.read"],
        "details": {},
        "last_verified_at": None,
        "verification_status": "unknown",
        "verification_error": None,
        "created_at": NOW,
        "updated_at": NOW,
    }




class FakeResolverRepository:
    def __init__(self, locators: list[dict]) -> None:
        self.locators = locators


    def get_snapshot(self, snapshot_id: str):
        return snapshot_row(snapshot_id)


    def list_locators(self, snapshot_id: str):
        return self.locators




class SnapshotContractTests(unittest.TestCase):
    def test_github_commit_fingerprint_ignores_branch_and_metadata(self) -> None:
        first = SnapshotDescriptor(
            repository_id="repo_vision",
            snapshot_type=SnapshotType.GITHUB_COMMIT,
            commit_sha=COMMIT_A,
            branch="frontend",
            metadata={"capture": "a"},
        )
        second = SnapshotDescriptor(
            repository_id="repo_vision",
            snapshot_type=SnapshotType.GITHUB_COMMIT,
            commit_sha=COMMIT_A,
            branch="main",
            metadata={"capture": "b"},
        )
        self.assertEqual(
            compute_snapshot_fingerprint(first),
            compute_snapshot_fingerprint(second),
        )


    def test_different_commit_has_different_fingerprint(self) -> None:
        first = SnapshotDescriptor(
            repository_id="repo_vision",
            snapshot_type=SnapshotType.GITHUB_COMMIT,
            commit_sha=COMMIT_A,
        )
        second = SnapshotDescriptor(
            repository_id="repo_vision",
            snapshot_type=SnapshotType.GITHUB_COMMIT,
            commit_sha=COMMIT_B,
        )
        self.assertNotEqual(
            compute_snapshot_fingerprint(first),
            compute_snapshot_fingerprint(second),
        )


    def test_local_index_requires_base_and_tree(self) -> None:
        with self.assertRaises(ValueError):
            SnapshotDescriptor(
                repository_id="repo_vision",
                snapshot_type=SnapshotType.LOCAL_INDEX,
                base_commit_sha=COMMIT_A,
            )


    def test_local_worktree_requires_manifest(self) -> None:
        descriptor = SnapshotDescriptor(
            repository_id="repo_vision",
            snapshot_type=SnapshotType.LOCAL_WORKTREE,
            base_commit_sha=COMMIT_A,
            manifest_hash=MANIFEST_A,
        )
        self.assertEqual(descriptor.manifest_hash, MANIFEST_A)


    def test_client_locator_requires_source_client(self) -> None:
        with self.assertRaises(ValueError):
            SnapshotLocatorCreate(
                provider=LocatorProvider.CLIENT_LOCAL,
                availability=LocatorAvailability.CLIENT_REQUIRED,
                access_mode=AccessMode.CLIENT_RELAY,
                credential_strategy=CredentialStrategy.CLIENT_OWNED,
            )




class SnapshotResolverTests(unittest.TestCase):
    def test_durable_github_wins_other_client_locator(self) -> None:
        repository = FakeResolverRepository(
            [
                locator_row(
                    "client",
                    provider="client-local",
                    availability="client-required",
                    access_mode="client-relay",
                    source_client_id="client-a",
                ),
                locator_row(
                    "github",
                    provider="github",
                    availability="durable",
                    access_mode="backend-proxy",
                ),
            ]
        )
        result = SnapshotResolver(repository).resolve(
            "snap_test",
            SnapshotResolveRequest(
                consumer_type=ConsumerType.SLLM,
                consumer_id="sllm-main",
                required_capabilities=["file.read"],
            ),
        )
        self.assertTrue(result.available)
        self.assertEqual(result.plan.locator_id, "github")


    def test_same_client_direct_source_gets_preference(self) -> None:
        repository = FakeResolverRepository(
            [
                locator_row(
                    "client",
                    provider="client-local",
                    availability="client-required",
                    access_mode="client-direct",
                    source_client_id="client-a",
                ),
                locator_row(
                    "github",
                    provider="github",
                    availability="durable",
                    access_mode="backend-proxy",
                ),
            ]
        )
        result = SnapshotResolver(repository).resolve(
            "snap_test",
            SnapshotResolveRequest(
                consumer_type=ConsumerType.CLIENT,
                consumer_id="client-a",
                required_capabilities=["file.read"],
            ),
        )
        self.assertTrue(result.available)
        self.assertEqual(result.plan.locator_id, "client")


    def test_missing_capability_returns_unavailable_plan(self) -> None:
        repository = FakeResolverRepository(
            [
                locator_row(
                    "github",
                    provider="github",
                    availability="durable",
                    access_mode="backend-proxy",
                    capabilities=["tree.read"],
                )
            ]
        )
        result = SnapshotResolver(repository).resolve(
            "snap_test",
            SnapshotResolveRequest(
                consumer_type=ConsumerType.VECTOR_DB,
                required_capabilities=["file.read"],
            ),
        )
        self.assertFalse(result.available)
        self.assertEqual(result.plan.access_mode, AccessMode.UNAVAILABLE)




class FakeSnapshotRepository:
    def get_snapshot(self, snapshot_id: str):
        return snapshot_row(snapshot_id)




class FakeVectorRegistry:
    def get(self, vector_index_id: str):
        return VectorIndexRecord(
            vector_index_id=vector_index_id,
            tenant_id="default",
            repository_id="repo_vision",
            source_snapshot_id="snap_other",
            provider_id="qdrant-main",
            endpoint_ref=None,
            collection="vision",
            namespace="default",
            index_version="1",
            embedding_model="bge-m3",
            dimension=1024,
            status=VectorIndexStatus.READY,
            metadata={},
            last_verified_at=None,
            verification_error=None,
            created_at=NOW,
            updated_at=NOW,
        )




class FakeContextStore:
    def create(self, payload):
        raise AssertionError("context must not be stored on snapshot/vector mismatch")




class ContextBindingTests(unittest.TestCase):
    def test_snapshot_vector_mismatch_is_rejected(self) -> None:
        service = ContextService(
            FakeSnapshotRepository(),
            FakeVectorRegistry(),
            FakeContextStore(),
        )
        with self.assertRaises(SnapshotVectorMismatchError):
            service.create(
                ContextCreate(
                    client_id="client-a",
                    repository_id="repo_vision",
                    snapshot_id="snap_test",
                    vector_index_id="vec_test",
                )
            )




if __name__ == "__main__":
    unittest.main()
