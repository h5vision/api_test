from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from backend.config import settings
from backend.offline_embeddings import OfflineEmbeddingImporter
from backend.text import chunk_text_with_metadata


class FakeRepositoryStore:
    def __init__(self) -> None:
        self.source: dict | None = None
        self.snapshot: dict | None = None
        self.generation: dict | None = None
        self.chunks: dict[str, dict] = {}
        self.job: dict = {}

    def upsert_source(self, values: dict) -> dict:
        self.source = dict(values)
        return self.source

    def get_generation(self, generation_id: str) -> dict | None:
        if self.generation and self.generation["generation_id"] == generation_id:
            return self.generation
        return None

    def get_snapshot(self, snapshot_id: str) -> dict | None:
        if self.snapshot and self.snapshot["snapshot_id"] == snapshot_id:
            return self.snapshot
        return None

    def update_job(self, job_id: str, **values: object) -> None:
        self.job.update(values)

    def begin_snapshot(self, **values: object) -> None:
        source = values["source"]
        assert isinstance(source, dict)
        self.snapshot = {
            "snapshot_id": values["snapshot_id"],
            "project_id": source["project_id"],
            "manifest_sha256": values["manifest_sha256"],
            "status": "building",
        }
        self.generation = {
            "generation_id": values["generation_id"],
            "project_id": source["project_id"],
            "snapshot_id": values["snapshot_id"],
            "embedding_model": settings.embedding_model,
            "index_version": settings.index_version,
            "status": "building",
        }

    def prepare_generation_import(
        self, project_id: str, snapshot_id: str, generation_id: str
    ) -> None:
        assert self.generation is not None
        self.generation["status"] = "building"

    def generation_chunk_count(self, generation_id: str) -> int:
        return len(self.chunks)

    def append_generation_chunks(
        self, generation_id: str, records: list[dict]
    ) -> int:
        for record in records:
            self.chunks[record["chunk_id"]] = record
        return len(records)

    def activate_generation(self, **values: object) -> None:
        assert self.generation is not None
        self.generation["status"] = "active"

    def fail_generation(
        self,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        error: str,
    ) -> None:
        if self.generation:
            self.generation["status"] = "failed"


class FakeVectorStore:
    def __init__(self) -> None:
        self.points: dict[str, dict] = {}

    def upsert_generation_chunks(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        items: list[dict],
    ) -> list[str]:
        ids: list[str] = []
        for item in items:
            point_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project_id}:{generation_id}:{item['chunk_id']}",
                )
            )
            self.points[item["chunk_id"]] = item
            ids.append(point_id)
        return ids

    def count_generation(self, project_id: str, generation_id: str) -> int:
        return len(self.points)


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        project_root = root / "projects"
        source_root = project_root / "Vision"
        artifact_root = root / "embedding-results"
        source_root.mkdir(parents=True)
        artifact_root.mkdir()

        relative_path = "backend/example.py"
        source_path = source_root / relative_path
        source_path.parent.mkdir()
        source_content = "def health() -> str:\n    return 'ok'\n"
        source_path.write_text(source_content, encoding="utf-8")
        raw = source_path.read_bytes()
        manifest_rows = [
            {
                "relative_path": relative_path,
                "entry_type": "file",
                "size_bytes": len(raw),
                "content_sha256": hashlib.sha256(raw).hexdigest(),
            }
        ]
        manifest_sha256 = hashlib.sha256(
            json.dumps(
                manifest_rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        snapshot_id = f"snap_colab_{manifest_sha256[:20]}"
        generation_id = "gen_colab_verify"
        artifact_id = f"{snapshot_id}_bge-m3_latest"
        artifact_dir = artifact_root / "Vision" / artifact_id
        artifact_dir.mkdir(parents=True)

        spec = chunk_text_with_metadata(
            source_content,
            settings.chunk_size,
            settings.chunk_overlap,
        )[0]
        document_id = hashlib.sha256(
            relative_path.encode("utf-8")
        ).hexdigest()[:16]
        chunk_id = f"{document_id}#chunk-1"
        chunk_content = str(spec["content"])
        record = {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "path": relative_path,
            "language": "python",
            "content": chunk_content,
            "line_start": int(spec["line_start"]),
            "line_end": int(spec["line_end"]),
            "content_sha256": hashlib.sha256(
                chunk_content.encode("utf-8")
            ).hexdigest(),
            "metadata": {
                "source_id": "colab-drive:Vision",
                "revision": None,
                "snapshot_id": snapshot_id,
            },
            "project_id": "Vision",
            "snapshot_id": snapshot_id,
            "generation_id": generation_id,
            "embedding_provider": "ollama-colab-t4",
            "embedding_model": settings.embedding_model,
            "embedding_model_id": settings.embedding_model_id,
            "embedding_dimension": settings.embedding_dimension,
            "index_version": settings.index_version,
            "embedding": [0.001] * settings.embedding_dimension,
        }
        shard_path = artifact_dir / "part-00000.jsonl.gz"
        with gzip.open(shard_path, "wt", encoding="utf-8") as handle:
            handle.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
        shard_checksum = hashlib.sha256(shard_path.read_bytes()).hexdigest()
        manifest = {
            "schema_version": "vision.embedding-artifact.v1",
            "artifact_id": artifact_id,
            "project_id": "Vision",
            "source_id": "colab-drive:Vision",
            "source_root": "/content/drive/Vision",
            "source_relative_path": "Vision",
            "source_mode": "filesystem",
            "revision": None,
            "git_dirty": None,
            "snapshot_id": snapshot_id,
            "generation_id": generation_id,
            "manifest_sha256": manifest_sha256,
            "model_id": settings.embedding_model_id,
            "model_name": settings.embedding_model,
            "embedding_provider": "ollama-colab-t4",
            "embedding_dimension": settings.embedding_dimension,
            "index_version": settings.index_version,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "file_count": 1,
            "indexable_file_count": 1,
            "chunk_count": 1,
            "total_bytes": len(raw),
            "created_at": "2026-07-27T00:00:00+00:00",
        }
        progress = {
            "schema_version": "vision.embedding-progress.v1",
            "artifact_id": artifact_id,
            "next_chunk_index": 1,
            "embedded_chunks": 1,
            "shards": [
                {
                    "name": shard_path.name,
                    "sha256": shard_checksum,
                    "start_chunk_index": 0,
                    "end_chunk_index": 1,
                    "records": 1,
                    "size_bytes": shard_path.stat().st_size,
                }
            ],
            "updated_at": "2026-07-27T00:00:00+00:00",
        }
        completion = {
            **manifest,
            "status": "completed",
            "verified_chunks": 1,
            "shards": 1,
            "completed_at": "2026-07-27T00:00:00+00:00",
        }
        for name, value in (
            ("manifest.json", manifest),
            ("progress.json", progress),
            ("COMPLETE.json", completion),
        ):
            (artifact_dir / name).write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        resolved_settings = replace(
            settings,
            project_db_local_root=project_root,
            offline_embedding_root=artifact_root,
        )
        repository_store = FakeRepositoryStore()
        vector_store = FakeVectorStore()
        importer = OfflineEmbeddingImporter(
            resolved_settings,
            repository_store,  # type: ignore[arg-type]
            vector_store,  # type: ignore[arg-type]
        )

        artifacts = importer.list_artifacts()
        assert len(artifacts) == 1
        assert artifacts[0]["compatible"] is True
        assert artifacts[0]["imported"] is False
        importer.run("job_verify", artifact_id)
        assert repository_store.job["status"] == "completed"
        assert repository_store.generation is not None
        assert repository_store.generation["status"] == "active"
        assert len(repository_store.chunks) == 1
        assert len(vector_store.points) == 1
        assert importer.list_artifacts()[0]["imported"] is True

    print("Offline embedding artifact import verification passed")


if __name__ == "__main__":
    main()
