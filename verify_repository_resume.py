from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from backend.repository_indexer import RepositoryIndexer
from backend.services import EmbeddingResult, ServiceError


class FakeStore:
    def __init__(self) -> None:
        self.chunk_ids = {"chunk-1"}
        self.job_updates: list[dict[str, Any]] = []
        self.activated = False
        self.paused = False

    def update_job(self, _job_id: str, **values: Any) -> None:
        self.job_updates.append(values)

    def append_generation_chunks(
        self, _generation_id: str, records: list[dict[str, Any]]
    ) -> int:
        self.chunk_ids.update(str(row["chunk_id"]) for row in records)
        return len(records)

    def generation_chunk_count(self, _generation_id: str) -> int:
        return len(self.chunk_ids)

    def activate_generation(self, **_values: Any) -> None:
        self.activated = True

    def pause_generation(
        self,
        _project_id: str,
        _snapshot_id: str,
        _generation_id: str,
        _error: str,
    ) -> None:
        self.paused = True


class FakeEmbeddingService:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def embed_many(
        self, texts: list[str], input_type: str
    ) -> list[EmbeddingResult]:
        assert input_type == "passage"
        self.inputs.extend(texts)
        return [
            EmbeddingResult(vector=[1.0, 0.0, 0.0], provider="test", model="test")
            for _ in texts
        ]


class FakeVectorStore:
    def __init__(self) -> None:
        self.chunk_ids = {"chunk-1"}

    def upsert_generation_chunks(
        self, *, items: list[dict[str, Any]], **_values: Any
    ) -> list[str]:
        self.chunk_ids.update(str(item["chunk_id"]) for item in items)
        return [f"00000000-0000-0000-0000-{index:012d}" for index, _ in enumerate(items, 1)]

    def count_generation(
        self, _project_id: str, _generation_id: str
    ) -> int:
        return len(self.chunk_ids)


def main() -> None:
    settings = SimpleNamespace(
        embedding_batch_size=1,
        chunk_size=1600,
        chunk_overlap=200,
    )
    store = FakeStore()
    embedding = FakeEmbeddingService()
    vector = FakeVectorStore()
    indexer = RepositoryIndexer(settings, store, embedding, vector)  # type: ignore[arg-type]
    tasks = [
        {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "path": "one.py",
            "language": "python",
            "content": "already checkpointed",
            "line_start": 1,
            "line_end": 1,
            "metadata": {},
            "is_last": True,
        },
        {
            "chunk_id": "chunk-2",
            "document_id": "doc-2",
            "path": "two.py",
            "language": "python",
            "content": "resume this",
            "line_start": 1,
            "line_end": 1,
            "metadata": {},
            "is_last": True,
        },
        {
            "chunk_id": "chunk-3",
            "document_id": "doc-3",
            "path": "three.py",
            "language": "python",
            "content": "and this",
            "line_start": 1,
            "line_end": 1,
            "metadata": {},
            "is_last": True,
        },
    ]
    indexer._embed_and_publish(
        job_id="job-test",
        source={"source_id": "source-test", "project_id": "project-test"},
        snapshot={
            "snapshot_id": "snapshot-test",
            "revision": "abc",
            "git_branch": "main",
            "git_dirty": False,
            "git_committed_at": None,
            "manifest_sha256": "0" * 64,
        },
        generation_id="generation-test",
        chunk_tasks=tasks,
        completed_chunk_ids={"chunk-1"},
    )
    assert embedding.inputs == ["resume this", "and this"]
    assert store.chunk_ids == {"chunk-1", "chunk-2", "chunk-3"}
    assert vector.chunk_ids == {"chunk-1", "chunk-2", "chunk-3"}
    assert store.activated is True
    assert store.job_updates[-1]["status"] == "completed"
    assert store.job_updates[-1]["chunks_stored"] == 3

    indexer._pause_embedding_job(
        job_id="job-test",
        project_id="project-test",
        snapshot_id="snapshot-test",
        generation_id="generation-test",
        error=ServiceError("temporary endpoint outage"),
    )
    assert store.paused is True
    assert store.job_updates[-1]["status"] == "paused"
    assert store.job_updates[-1]["stage"] == "waiting_for_embedding"
    print("Repository checkpoint resume verification passed")


if __name__ == "__main__":
    main()
