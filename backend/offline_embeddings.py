from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from dulwich.repo import Repo

from .config import Settings
from .repository_indexer import (
    EXCLUDED_INDEX_PARTS,
    LANGUAGES,
    _indexable_path,
    _safe_decode,
)
from .repository_store import PostgresRepositoryStore, RepositoryStoreError
from .text import chunk_text_with_metadata
from .vector_store import QdrantVectorStore, SQLiteVectorStore, VectorStoreError


ARTIFACT_SCHEMA_VERSION = "vision.embedding-artifact.v1"
PROGRESS_SCHEMA_VERSION = "vision.embedding-progress.v1"
SHARD_NAME_PATTERN = re.compile(r"^part-\d{5}\.jsonl\.gz$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
MAX_ARTIFACTS = 500
MAX_CHUNKS = 10_000_000
MAX_SHARD_RECORDS = 10_000
MAX_SHARD_BYTES = 512 * 1024 * 1024
MAX_JSON_LINE_BYTES = 8 * 1024 * 1024
OFFLINE_EXCLUDED_PARTS = {
    *EXCLUDED_INDEX_PARTS,
    "data",
    "embedding-results",
}


class OfflineEmbeddingArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class OfflineEmbeddingArtifact:
    artifact_id: str
    directory: Path
    relative_path: str
    manifest: dict[str, Any]
    progress: dict[str, Any]
    completion: dict[str, Any]

    @property
    def project_id(self) -> str:
        return str(self.manifest["project_id"])

    @property
    def source_id(self) -> str:
        return str(self.manifest["source_id"])

    @property
    def snapshot_id(self) -> str:
        return str(self.manifest["snapshot_id"])

    @property
    def generation_id(self) -> str:
        return str(self.manifest["generation_id"])

    @property
    def chunk_count(self) -> int:
        return int(self.manifest["chunk_count"])


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfflineEmbeddingArtifactError(
            f"Artifact JSON read failed: {path.name}"
        ) from exc
    if not isinstance(value, dict):
        raise OfflineEmbeddingArtifactError(
            f"Artifact JSON must be an object: {path.name}"
        )
    return value


def _safe_relative_path(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/").strip("/")
    parts = PurePosixPath(normalized).parts
    if (
        not normalized
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or ":" in parts[0]
    ):
        raise OfflineEmbeddingArtifactError(
            f"{field} must be a safe relative path"
        )
    return normalized


class OfflineEmbeddingImporter:
    """Imports Drive-synchronized Colab embedding packages idempotently."""

    def __init__(
        self,
        settings: Settings,
        store: PostgresRepositoryStore,
        vector_store: SQLiteVectorStore | QdrantVectorStore,
    ) -> None:
        self.settings = settings
        self.store = store
        self.vector_store = vector_store
        self.root = settings.offline_embedding_root.resolve()

    def _contract_errors(self, manifest: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if manifest.get("model_id") != self.settings.embedding_model_id:
            errors.append("embedding_model_id")
        if manifest.get("model_name") != self.settings.embedding_model:
            errors.append("embedding_model")
        if manifest.get("embedding_dimension") != self.settings.embedding_dimension:
            errors.append("embedding_dimension")
        if manifest.get("index_version") != self.settings.index_version:
            errors.append("index_version")
        return errors

    def _load_directory(self, directory: Path) -> OfflineEmbeddingArtifact:
        try:
            resolved = directory.resolve()
            relative = resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise OfflineEmbeddingArtifactError(
                "Artifact escaped OFFLINE_EMBEDDING_ROOT"
            ) from exc
        manifest = _read_json(resolved / "manifest.json")
        progress = _read_json(resolved / "progress.json")
        completion = _read_json(resolved / "COMPLETE.json")

        if manifest.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
            raise OfflineEmbeddingArtifactError(
                "Unsupported embedding artifact schema_version"
            )
        if progress.get("schema_version") != PROGRESS_SCHEMA_VERSION:
            raise OfflineEmbeddingArtifactError(
                "Unsupported embedding progress schema_version"
            )
        if completion.get("status") != "completed":
            raise OfflineEmbeddingArtifactError(
                "COMPLETE.json status must be completed"
            )

        required_text = (
            "artifact_id",
            "project_id",
            "source_id",
            "source_relative_path",
            "snapshot_id",
            "generation_id",
            "manifest_sha256",
            "model_id",
            "model_name",
            "index_version",
        )
        for field in required_text:
            if not isinstance(manifest.get(field), str) or not str(
                manifest[field]
            ).strip():
                raise OfflineEmbeddingArtifactError(
                    f"Artifact field is missing: {field}"
                )

        artifact_id = str(manifest["artifact_id"])
        if progress.get("artifact_id") != artifact_id:
            raise OfflineEmbeddingArtifactError(
                "progress.json artifact_id mismatch"
            )
        for field in (
            "artifact_id",
            "project_id",
            "source_id",
            "snapshot_id",
            "generation_id",
            "manifest_sha256",
            "model_id",
            "model_name",
            "embedding_dimension",
            "index_version",
            "chunk_count",
        ):
            if completion.get(field) != manifest.get(field):
                raise OfflineEmbeddingArtifactError(
                    f"COMPLETE.json field mismatch: {field}"
                )

        chunk_count = int(manifest.get("chunk_count", -1))
        if not 0 <= chunk_count <= MAX_CHUNKS:
            raise OfflineEmbeddingArtifactError("Artifact chunk_count is invalid")
        if int(progress.get("next_chunk_index", -1)) != chunk_count:
            raise OfflineEmbeddingArtifactError(
                "Embedding package is not fully checkpointed"
            )
        if int(progress.get("embedded_chunks", -1)) != chunk_count:
            raise OfflineEmbeddingArtifactError(
                "Embedding package embedded_chunks mismatch"
            )
        if int(completion.get("verified_chunks", -1)) != chunk_count:
            raise OfflineEmbeddingArtifactError(
                "COMPLETE.json verified_chunks mismatch"
            )

        shards = progress.get("shards")
        if not isinstance(shards, list) or int(
            completion.get("shards", -1)
        ) != len(shards):
            raise OfflineEmbeddingArtifactError("Artifact shard count mismatch")
        next_start = 0
        shard_records = 0
        for shard in shards:
            if not isinstance(shard, dict):
                raise OfflineEmbeddingArtifactError("Shard descriptor is invalid")
            name = str(shard.get("name") or "")
            if not SHARD_NAME_PATTERN.fullmatch(name):
                raise OfflineEmbeddingArtifactError(
                    f"Unsafe shard name: {name}"
                )
            if not SHA256_PATTERN.fullmatch(str(shard.get("sha256") or "")):
                raise OfflineEmbeddingArtifactError(
                    f"Invalid shard checksum: {name}"
                )
            start = int(shard.get("start_chunk_index", -1))
            end = int(shard.get("end_chunk_index", -1))
            records = int(shard.get("records", -1))
            if (
                start != next_start
                or end < start
                or records != end - start
                or not 0 <= records <= MAX_SHARD_RECORDS
            ):
                raise OfflineEmbeddingArtifactError(
                    f"Invalid shard range: {name}"
                )
            next_start = end
            shard_records += records
        if next_start != chunk_count or shard_records != chunk_count:
            raise OfflineEmbeddingArtifactError(
                "Shard ranges do not cover every chunk"
            )

        _safe_relative_path(
            manifest["source_relative_path"],
            field="source_relative_path",
        )
        if not SHA256_PATTERN.fullmatch(str(manifest["manifest_sha256"])):
            raise OfflineEmbeddingArtifactError("manifest_sha256 is invalid")

        return OfflineEmbeddingArtifact(
            artifact_id=artifact_id,
            directory=resolved,
            relative_path=relative.as_posix(),
            manifest=manifest,
            progress=progress,
            completion=completion,
        )

    def list_artifacts(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for complete_path in sorted(self.root.rglob("COMPLETE.json"))[:MAX_ARTIFACTS]:
            try:
                artifact = self._load_directory(complete_path.parent)
                contract_errors = self._contract_errors(artifact.manifest)
                generation = self.store.get_generation(artifact.generation_id)
                imported = bool(
                    generation and generation.get("status") == "active"
                )
                rows.append(
                    {
                        "artifact_id": artifact.artifact_id,
                        "project_id": artifact.project_id,
                        "snapshot_id": artifact.snapshot_id,
                        "generation_id": artifact.generation_id,
                        "model_id": artifact.manifest["model_id"],
                        "model_name": artifact.manifest["model_name"],
                        "embedding_dimension": artifact.manifest[
                            "embedding_dimension"
                        ],
                        "index_version": artifact.manifest["index_version"],
                        "chunk_count": artifact.chunk_count,
                        "shard_count": len(artifact.progress["shards"]),
                        "relative_path": artifact.relative_path,
                        "compatible": not contract_errors,
                        "contract_errors": contract_errors,
                        "imported": imported,
                        "completed_at": artifact.completion.get("completed_at"),
                        "error": None,
                    }
                )
            except (
                OfflineEmbeddingArtifactError,
                RepositoryStoreError,
                OSError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                rows.append(
                    {
                        "artifact_id": complete_path.parent.name,
                        "project_id": "unknown",
                        "snapshot_id": "",
                        "generation_id": "",
                        "model_id": "",
                        "model_name": "",
                        "embedding_dimension": 0,
                        "index_version": "",
                        "chunk_count": 0,
                        "shard_count": 0,
                        "relative_path": complete_path.parent.relative_to(
                            self.root
                        ).as_posix(),
                        "compatible": False,
                        "contract_errors": [],
                        "imported": False,
                        "completed_at": None,
                        "error": str(exc),
                    }
                )
        rows.sort(
            key=lambda row: str(row.get("completed_at") or ""),
            reverse=True,
        )
        return rows

    def get_artifact(self, artifact_id: str) -> OfflineEmbeddingArtifact:
        normalized = artifact_id.strip()
        if not normalized or len(normalized) > 255:
            raise OfflineEmbeddingArtifactError("artifact_id is invalid")
        matches: list[OfflineEmbeddingArtifact] = []
        if self.root.is_dir():
            for complete_path in self.root.rglob("COMPLETE.json"):
                try:
                    artifact = self._load_directory(complete_path.parent)
                except (
                    OfflineEmbeddingArtifactError,
                    OSError,
                    KeyError,
                    TypeError,
                    ValueError,
                ):
                    continue
                if artifact.artifact_id == normalized:
                    matches.append(artifact)
        if not matches:
            raise OfflineEmbeddingArtifactError("Embedding artifact was not found")
        if len(matches) > 1:
            raise OfflineEmbeddingArtifactError(
                "Duplicate embedding artifact_id was found"
            )
        return matches[0]

    def prepare_import(self, artifact_id: str) -> tuple[
        OfflineEmbeddingArtifact, dict[str, Any]
    ]:
        artifact = self.get_artifact(artifact_id)
        contract_errors = self._contract_errors(artifact.manifest)
        if contract_errors:
            raise OfflineEmbeddingArtifactError(
                "Artifact embedding contract differs from active settings: "
                + ", ".join(contract_errors)
            )
        source_relative_path = _safe_relative_path(
            artifact.manifest["source_relative_path"],
            field="source_relative_path",
        )
        source = self.store.upsert_source(
            {
                "source_id": artifact.source_id,
                "project_id": artifact.project_id,
                "source_type": "local",
                "root_relative_path": source_relative_path,
                "repository_url": None,
                "default_branch": None,
                "enabled": True,
            }
        )
        return artifact, source

    def _source_paths(
        self, source_root: Path, source_mode: str
    ) -> list[str]:
        if source_mode == "git_tracked_worktree":
            try:
                repo = Repo(str(source_root))
                index = repo.open_index()
                return sorted(
                    path.decode("utf-8", errors="replace")
                    for path in index
                    if (source_root / path.decode(
                        "utf-8", errors="replace"
                    )).is_file()
                )
            except Exception as exc:
                raise OfflineEmbeddingArtifactError(
                    "Local Git index cannot reproduce the Colab snapshot"
                ) from exc
        return sorted(
            path.relative_to(source_root).as_posix()
            for path in source_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() != ".ipynb"
            and not any(
                part.lower() in OFFLINE_EXCLUDED_PARTS
                for part in path.relative_to(source_root).parts
            )
        )

    def _build_source_snapshot(
        self, artifact: OfflineEmbeddingArtifact
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
        relative_root = _safe_relative_path(
            artifact.manifest["source_relative_path"],
            field="source_relative_path",
        )
        project_root = self.settings.project_db_local_root.resolve()
        source_root = (project_root / relative_root).resolve()
        try:
            source_root.relative_to(project_root)
        except ValueError as exc:
            raise OfflineEmbeddingArtifactError(
                "Artifact source escaped PROJECT_DB_LOCAL_ROOT"
            ) from exc
        if not source_root.is_dir():
            raise OfflineEmbeddingArtifactError(
                f"Local synchronized source does not exist: {relative_root}"
            )

        relative_paths = self._source_paths(
            source_root, str(artifact.manifest.get("source_mode") or "filesystem")
        )
        manifest_rows: list[dict[str, Any]] = []
        file_entries: list[dict[str, Any]] = []
        directories: set[str] = set()
        expected_chunks: dict[str, dict[str, Any]] = {}
        total_bytes = 0
        revision = artifact.manifest.get("revision")

        for relative_path in relative_paths:
            absolute_path = source_root / relative_path
            raw = absolute_path.read_bytes()
            size_bytes = len(raw)
            total_bytes += size_bytes
            content_sha256 = hashlib.sha256(raw).hexdigest()
            manifest_rows.append(
                {
                    "relative_path": relative_path,
                    "entry_type": "file",
                    "size_bytes": size_bytes,
                    "content_sha256": content_sha256,
                }
            )
            pure = PurePosixPath(relative_path)
            for parent in pure.parents:
                if str(parent) != ".":
                    directories.add(parent.as_posix())
            text, encoding = _safe_decode(raw)
            eligible = _indexable_path(
                relative_path,
                size_bytes,
                self.settings.max_indexable_file_bytes,
            )
            indexable = eligible and text is not None and bool(text.strip())
            content = (
                text
                if text is not None
                and size_bytes <= self.settings.max_indexable_file_bytes
                else None
            )
            file_entries.append(
                {
                    "relative_path": relative_path,
                    "name": pure.name,
                    "entry_type": "file",
                    "language": LANGUAGES.get(pure.suffix.lower()),
                    "size_bytes": size_bytes,
                    "content_sha256": content_sha256,
                    "content": content,
                    "indexable": indexable,
                    "metadata": {
                        "encoding": encoding,
                        "offline_artifact_id": artifact.artifact_id,
                    },
                }
            )
            if not indexable or content is None:
                continue
            path_hash = hashlib.sha256(
                relative_path.encode("utf-8")
            ).hexdigest()[:16]
            specs = chunk_text_with_metadata(
                content,
                self.settings.chunk_size,
                self.settings.chunk_overlap,
                path=relative_path,
                language=LANGUAGES.get(pure.suffix.lower()),
            )
            for ordinal, item in enumerate(specs, start=1):
                chunk_content = str(item["content"])
                chunk_id = f"{path_hash}#chunk-{ordinal}"
                expected_chunks[chunk_id] = {
                    "chunk_id": chunk_id,
                    "document_id": path_hash,
                    "path": relative_path,
                    "language": LANGUAGES.get(pure.suffix.lower()),
                    "content_sha256": hashlib.sha256(
                        chunk_content.encode("utf-8")
                    ).hexdigest(),
                    "content": chunk_content,
                    "line_start": int(item["line_start"]),
                    "line_end": int(item["line_end"]),
                    "metadata": {
                        "source_id": artifact.source_id,
                        "revision": revision,
                        "snapshot_id": artifact.snapshot_id,
                        "content_type": item.get("content_type"),
                        "path_category": item.get("path_category"),
                        "locale": item.get("locale"),
                        "is_translation": bool(item.get("is_translation")),
                        "chunking_strategy": item.get("chunking_strategy"),
                    },
                }

        manifest_encoded = json.dumps(
            manifest_rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        actual_manifest_sha256 = hashlib.sha256(manifest_encoded).hexdigest()
        if actual_manifest_sha256 != artifact.manifest["manifest_sha256"]:
            raise OfflineEmbeddingArtifactError(
                "Local synchronized source differs from the Colab artifact manifest"
            )
        if len(expected_chunks) != artifact.chunk_count:
            raise OfflineEmbeddingArtifactError(
                "Local source chunk count differs from the Colab artifact"
            )

        directory_entries = [
            {
                "relative_path": relative_path,
                "name": PurePosixPath(relative_path).name,
                "entry_type": "directory",
                "size_bytes": 0,
                "indexable": False,
                "metadata": {
                    "offline_artifact_id": artifact.artifact_id,
                },
            }
            for relative_path in sorted(directories)
        ]
        entries = [*directory_entries, *file_entries]
        return entries, expected_chunks, total_bytes

    def _iter_shard_records(
        self, artifact: OfflineEmbeddingArtifact
    ) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
        for shard in artifact.progress["shards"]:
            shard_path = artifact.directory / shard["name"]
            if not shard_path.is_file():
                raise OfflineEmbeddingArtifactError(
                    f"Shard is missing: {shard['name']}"
                )
            if shard_path.stat().st_size > MAX_SHARD_BYTES:
                raise OfflineEmbeddingArtifactError(
                    f"Shard is too large: {shard['name']}"
                )
            checksum = hashlib.sha256(shard_path.read_bytes()).hexdigest()
            if checksum != shard["sha256"]:
                raise OfflineEmbeddingArtifactError(
                    f"Shard checksum mismatch: {shard['name']}"
                )
            records: list[dict[str, Any]] = []
            try:
                with gzip.open(shard_path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        if len(line.encode("utf-8")) > MAX_JSON_LINE_BYTES:
                            raise OfflineEmbeddingArtifactError(
                                f"Shard record is too large: {shard['name']}"
                            )
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise OfflineEmbeddingArtifactError(
                                f"Shard record is invalid: {shard['name']}"
                            )
                        records.append(record)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise OfflineEmbeddingArtifactError(
                    f"Shard read failed: {shard['name']}"
                ) from exc
            if len(records) != int(shard["records"]):
                raise OfflineEmbeddingArtifactError(
                    f"Shard record count mismatch: {shard['name']}"
                )
            yield shard, records

    def _validate_record(
        self,
        artifact: OfflineEmbeddingArtifact,
        record: dict[str, Any],
        expected_chunks: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        chunk_id = str(record.get("chunk_id") or "")
        expected = expected_chunks.get(chunk_id)
        if expected is None:
            raise OfflineEmbeddingArtifactError(
                f"Artifact contains an unknown chunk_id: {chunk_id}"
            )
        comparisons = {
            "project_id": artifact.project_id,
            "snapshot_id": artifact.snapshot_id,
            "generation_id": artifact.generation_id,
            "embedding_model": self.settings.embedding_model,
            "embedding_model_id": self.settings.embedding_model_id,
            "embedding_dimension": self.settings.embedding_dimension,
            "index_version": self.settings.index_version,
            "document_id": expected["document_id"],
            "path": expected["path"],
            "content_sha256": expected["content_sha256"],
            "line_start": expected["line_start"],
            "line_end": expected["line_end"],
        }
        for field, expected_value in comparisons.items():
            if record.get(field) != expected_value:
                raise OfflineEmbeddingArtifactError(
                    f"Chunk field mismatch: {chunk_id}.{field}"
                )
        if record.get("content") != expected["content"]:
            raise OfflineEmbeddingArtifactError(
                f"Chunk content mismatch: {chunk_id}"
            )
        vector = record.get("embedding")
        if (
            not isinstance(vector, list)
            or len(vector) != self.settings.embedding_dimension
            or any(
                not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in vector
            )
        ):
            raise OfflineEmbeddingArtifactError(
                f"Chunk embedding is invalid: {chunk_id}"
            )
        return {
            **expected,
            "metadata": {
                **expected["metadata"],
                "offline_artifact_id": artifact.artifact_id,
            },
            "embedding": [float(value) for value in vector],
            "embedding_provider": str(
                record.get("embedding_provider") or "ollama-colab-t4"
            ),
            "embedding_model": self.settings.embedding_model,
        }

    def _validate_package_records(
        self,
        artifact: OfflineEmbeddingArtifact,
        expected_chunks: dict[str, dict[str, Any]],
    ) -> None:
        seen: set[str] = set()
        for _shard, records in self._iter_shard_records(artifact):
            for record in records:
                resolved = self._validate_record(
                    artifact, record, expected_chunks
                )
                chunk_id = resolved["chunk_id"]
                if chunk_id in seen:
                    raise OfflineEmbeddingArtifactError(
                        f"Duplicate chunk_id in artifact: {chunk_id}"
                    )
                seen.add(chunk_id)
        if seen != set(expected_chunks):
            raise OfflineEmbeddingArtifactError(
                "Artifact does not contain the complete local chunk set"
            )

    def run(self, job_id: str, artifact_id: str) -> None:
        artifact: OfflineEmbeddingArtifact | None = None
        try:
            artifact, source = self.prepare_import(artifact_id)
            self.store.update_job(
                job_id,
                status="inspecting",
                stage="artifact_contract_validation",
                error=None,
            )
            entries, expected_chunks, total_bytes = self._build_source_snapshot(
                artifact
            )
            self._validate_package_records(artifact, expected_chunks)

            self.store.update_job(
                job_id,
                snapshot_id=artifact.snapshot_id,
                generation_id=artifact.generation_id,
                status="snapshotting",
                stage="offline_snapshot_registration",
                files_total=int(
                    artifact.manifest.get("indexable_file_count", 0)
                ),
                bytes_total=total_bytes,
            )
            snapshot = self.store.get_snapshot(artifact.snapshot_id)
            generation = self.store.get_generation(artifact.generation_id)
            if snapshot is None and generation is None:
                self.store.begin_snapshot(
                    source=source,
                    snapshot_id=artifact.snapshot_id,
                    generation_id=artifact.generation_id,
                    revision=artifact.manifest.get("revision"),
                    branch=None,
                    dirty=artifact.manifest.get("git_dirty"),
                    committed_at=None,
                    manifest_sha256=artifact.manifest["manifest_sha256"],
                    entries=entries,
                    total_bytes=total_bytes,
                )
            elif snapshot is None or generation is None:
                raise OfflineEmbeddingArtifactError(
                    "Artifact snapshot/generation is only partially registered"
                )
            else:
                if (
                    snapshot["project_id"] != artifact.project_id
                    or snapshot["manifest_sha256"]
                    != artifact.manifest["manifest_sha256"]
                    or generation["project_id"] != artifact.project_id
                    or generation["snapshot_id"] != artifact.snapshot_id
                    or generation["embedding_model"]
                    != self.settings.embedding_model
                    or generation["index_version"]
                    != self.settings.index_version
                ):
                    raise OfflineEmbeddingArtifactError(
                        "Existing snapshot/generation contract mismatch"
                    )
                self.store.prepare_generation_import(
                    artifact.project_id,
                    artifact.snapshot_id,
                    artifact.generation_id,
                )

            total_chunks = artifact.chunk_count
            imported_chunks = 0
            indexable_files = int(
                artifact.manifest.get("indexable_file_count", 0)
            )
            self.store.update_job(
                job_id,
                status="embedding",
                stage="offline_shard_import",
                chunks_stored=self.store.generation_chunk_count(
                    artifact.generation_id
                ),
            )
            for _shard, records in self._iter_shard_records(artifact):
                embedded_items = [
                    self._validate_record(
                        artifact, record, expected_chunks
                    )
                    for record in records
                ]
                point_ids = self.vector_store.upsert_generation_chunks(
                    project_id=artifact.project_id,
                    snapshot_id=artifact.snapshot_id,
                    generation_id=artifact.generation_id,
                    items=embedded_items,
                )
                mappings = [
                    {
                        "chunk_id": item["chunk_id"],
                        "document_id": item["document_id"],
                        "relative_path": item["path"],
                        "external_point_id": point_id,
                        "content_sha256": item["content_sha256"],
                        "content": item["content"],
                        "line_start": item["line_start"],
                        "line_end": item["line_end"],
                        "metadata": item["metadata"],
                    }
                    for item, point_id in zip(embedded_items, point_ids)
                ]
                self.store.append_generation_chunks(
                    artifact.generation_id, mappings
                )
                imported_chunks += len(embedded_items)
                files_processed = (
                    round(indexable_files * imported_chunks / total_chunks)
                    if total_chunks
                    else indexable_files
                )
                self.store.update_job(
                    job_id,
                    files_processed=files_processed,
                    chunks_stored=imported_chunks,
                )

            self.store.update_job(
                job_id,
                status="publishing",
                stage="offline_index_validation",
            )
            postgres_count = self.store.generation_chunk_count(
                artifact.generation_id
            )
            vector_count = self.vector_store.count_generation(
                artifact.project_id, artifact.generation_id
            )
            if (
                postgres_count != total_chunks
                or vector_count != total_chunks
            ):
                raise OfflineEmbeddingArtifactError(
                    "Offline import count validation failed: "
                    f"expected={total_chunks}, postgres={postgres_count}, "
                    f"qdrant={vector_count}"
                )
            self.store.activate_generation(
                source_id=artifact.source_id,
                project_id=artifact.project_id,
                snapshot_id=artifact.snapshot_id,
                generation_id=artifact.generation_id,
                revision=artifact.manifest.get("revision"),
                branch=None,
                dirty=artifact.manifest.get("git_dirty"),
                committed_at=None,
                manifest_sha256=artifact.manifest["manifest_sha256"],
                file_count=indexable_files,
                chunk_count=total_chunks,
            )
            self.store.update_job(
                job_id,
                status="completed",
                stage="completed",
                files_processed=indexable_files,
                chunks_stored=total_chunks,
                error=None,
                completed_at=datetime.now(timezone.utc),
            )
        except (
            OfflineEmbeddingArtifactError,
            RepositoryStoreError,
            VectorStoreError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            if artifact is not None:
                try:
                    self.store.fail_generation(
                        artifact.project_id,
                        artifact.snapshot_id,
                        artifact.generation_id,
                        str(exc)[:4000],
                    )
                except RepositoryStoreError:
                    pass
            try:
                self.store.update_job(
                    job_id,
                    status="failed",
                    stage="offline_import_failed",
                    error=str(exc)[:4000],
                    completed_at=datetime.now(timezone.utc),
                )
            except RepositoryStoreError:
                pass
