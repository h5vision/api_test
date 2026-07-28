from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from dulwich.objects import Blob, Commit, Tree
from dulwich.porcelain import status as git_status
from dulwich.repo import Repo

from .config import Settings
from .repository_store import PostgresRepositoryStore, RepositoryStoreError
from .services import EmbeddingService, ServiceError
from .text import chunk_text_with_metadata
from .vector_store import QdrantVectorStore, SQLiteVectorStore


class RepositoryIndexingError(RuntimeError):
    pass


INDEXABLE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".cxx",
    ".dockerfile",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".htm",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".mjs",
    ".php",
    ".properties",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
INDEXABLE_NAMES = {
    "dockerfile",
    "gemfile",
    "makefile",
    "procfile",
    "readme",
}
EXCLUDED_INDEX_PARTS = {
    ".git",
    ".idea",
    ".next",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
EXCLUDED_INDEX_SUFFIXES = {
    ".lock",
    ".min.css",
    ".min.js",
    ".map",
}
LANGUAGES = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascriptreact",
    ".kt": "kotlin",
    ".md": "markdown",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shellscript",
    ".sql": "sql",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".vue": "vue",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}


def _safe_decode(data: bytes) -> tuple[str | None, str | None]:
    if b"\x00" in data:
        return None, None
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None, None


def _indexable_path(path: str, size_bytes: int, limit: int) -> bool:
    pure = PurePosixPath(path)
    lower_name = pure.name.lower()
    if size_bytes <= 0 or size_bytes > limit:
        return False
    if any(part.lower() in EXCLUDED_INDEX_PARTS for part in pure.parts):
        return False
    if any(lower_name.endswith(suffix) for suffix in EXCLUDED_INDEX_SUFFIXES):
        return False
    return (
        pure.suffix.lower() in INDEXABLE_EXTENSIONS
        or lower_name in INDEXABLE_NAMES
        or lower_name.startswith("readme.")
    )


class RepositoryIndexer:
    def __init__(
        self,
        settings: Settings,
        store: PostgresRepositoryStore,
        embedding_service: EmbeddingService,
        vector_store: SQLiteVectorStore | QdrantVectorStore,
    ) -> None:
        self.settings = settings
        self.store = store
        self.embedding_service = embedding_service
        self.vector_store = vector_store

    def _source_root(self, relative_path: str) -> Path:
        root = self.settings.project_db_local_root.resolve()
        source = (root / relative_path).resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise RepositoryIndexingError(
                "Repository source escaped PROJECT_DB_LOCAL_ROOT"
            ) from exc
        if not source.is_dir():
            raise RepositoryIndexingError(f"Repository source does not exist: {source}")
        return source

    @staticmethod
    def _git_head_details(repo: Repo) -> dict[str, Any]:
        """Read commit metadata without scanning the mounted working tree."""

        head = repo.head()
        commit = repo[head]
        if not isinstance(commit, Commit):
            raise RepositoryIndexingError("Git HEAD does not resolve to a commit")
        branch: str | None = None
        try:
            refs, _sha = repo.refs.follow(b"HEAD")
            branch_ref = refs[-1] if refs else b""
            if branch_ref.startswith(b"refs/heads/"):
                branch = branch_ref.removeprefix(b"refs/heads/").decode(
                    "utf-8", errors="replace"
                )
        except (KeyError, ValueError):
            branch = None
        return {
            "revision": head.decode("ascii"),
            "branch": branch,
            "dirty": None,
            "committed_at": datetime.fromtimestamp(
                commit.commit_time, tz=timezone.utc
            ),
            "tree_id": commit.tree,
        }

    @staticmethod
    def _git_details(repo: Repo) -> dict[str, Any]:
        details = RepositoryIndexer._git_head_details(repo)
        try:
            worktree = Path(repo.path).parent if Path(repo.path).name == ".git" else Path(repo.path)
            status = git_status(str(worktree))
            dirty = bool(status.unstaged or status.untracked)
            dirty = dirty or any(status.staged.values())
        except Exception:
            dirty = None
        return {**details, "dirty": dirty}

    @staticmethod
    def _walk_tree(
        repo: Repo,
        tree_id: bytes,
        prefix: str = "",
    ) -> list[tuple[str, int, bytes | None]]:
        tree = repo[tree_id]
        if not isinstance(tree, Tree):
            raise RepositoryIndexingError("Git commit tree is unavailable")
        items: list[tuple[str, int, bytes | None]] = []
        for raw_name, mode, sha in tree.iteritems():
            name = raw_name.decode("utf-8", errors="replace")
            path = f"{prefix}/{name}" if prefix else name
            if stat.S_ISDIR(mode):
                items.append((path, mode, None))
                items.extend(RepositoryIndexer._walk_tree(repo, sha, path))
            elif mode == 0o160000:
                items.append((path, mode, None))
            else:
                items.append((path, mode, sha))
        return items

    def _snapshot_entries(
        self, repo: Repo, tree_id: bytes
    ) -> tuple[list[dict[str, Any]], int, str]:
        entries: list[dict[str, Any]] = []
        total_bytes = 0
        for relative_path, mode, object_id in self._walk_tree(repo, tree_id):
            pure = PurePosixPath(relative_path)
            if object_id is None:
                entries.append(
                    {
                        "relative_path": relative_path,
                        "name": pure.name,
                        "entry_type": "directory",
                        "size_bytes": 0,
                        "indexable": False,
                        "metadata": {
                            "git_mode": oct(mode),
                            "submodule": mode == 0o160000,
                        },
                    }
                )
                continue
            obj = repo[object_id]
            if not isinstance(obj, Blob):
                continue
            raw = obj.data
            size_bytes = len(raw)
            total_bytes += size_bytes
            content_hash = hashlib.sha256(raw).hexdigest()
            text, encoding = _safe_decode(raw)
            eligible = _indexable_path(
                relative_path,
                size_bytes,
                self.settings.max_indexable_file_bytes,
            )
            indexable = eligible and text is not None and bool(text.strip())
            suffix = pure.suffix.lower()
            entries.append(
                {
                    "relative_path": relative_path,
                    "name": pure.name,
                    "entry_type": "file",
                    "language": LANGUAGES.get(suffix),
                    "size_bytes": size_bytes,
                    "content_sha256": content_hash,
                    "content": (
                        text
                        if text is not None
                        and size_bytes <= self.settings.max_indexable_file_bytes
                        else None
                    ),
                    "indexable": indexable,
                    "metadata": {
                        "git_mode": oct(mode),
                        "encoding": encoding,
                    },
                }
            )
        manifest_rows = [
            {
                "relative_path": entry["relative_path"],
                "entry_type": entry["entry_type"],
                "size_bytes": entry["size_bytes"],
                "content_sha256": entry.get("content_sha256"),
            }
            for entry in entries
        ]
        encoded = json.dumps(
            manifest_rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return entries, total_bytes, hashlib.sha256(encoded).hexdigest()

    def inspect_source(self, source: dict[str, Any]) -> dict[str, Any]:
        """Read the current Backend checkout HEAD without changing repository state."""

        source_root = self._source_root(source["root_relative_path"])
        repo = Repo(str(source_root))
        git = self._git_head_details(repo)
        return {
            "revision": git["revision"],
            "branch": git["branch"],
            "dirty": git["dirty"],
            "committed_at": git["committed_at"],
        }

    def list_source_tree(
        self,
        source: dict[str, Any],
        prefix: str = "",
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """List tracked entries from the current Git HEAD, not the DB snapshot."""

        normalized_prefix = prefix.replace("\\", "/").strip("/")
        source_root = self._source_root(source["root_relative_path"])
        repo = Repo(str(source_root))
        git = self._git_head_details(repo)
        entries: list[dict[str, Any]] = []
        for relative_path, mode, object_id in self._walk_tree(
            repo,
            git["tree_id"],
        ):
            if normalized_prefix and not relative_path.startswith(
                f"{normalized_prefix}/"
            ):
                continue
            pure = PurePosixPath(relative_path)
            if object_id is None:
                entries.append(
                    {
                        "path": relative_path,
                        "name": pure.name,
                        "entry_type": "directory",
                        "language": None,
                        "size_bytes": 0,
                        "content_sha256": None,
                        "indexable": False,
                    }
                )
                continue
            obj = repo[object_id]
            if not isinstance(obj, Blob):
                continue
            raw = obj.data
            size_bytes = len(raw)
            text, _encoding = _safe_decode(raw)
            eligible = _indexable_path(
                relative_path,
                size_bytes,
                self.settings.max_indexable_file_bytes,
            )
            entries.append(
                {
                    "path": relative_path,
                    "name": pure.name,
                    "entry_type": "file",
                    "language": LANGUAGES.get(pure.suffix.lower()),
                    "size_bytes": size_bytes,
                    "content_sha256": hashlib.sha256(raw).hexdigest(),
                    "indexable": eligible and text is not None and bool(text.strip()),
                }
            )
        return (
            {
                "revision": git["revision"],
                "branch": git["branch"],
                "dirty": git["dirty"],
                "committed_at": git["committed_at"],
            },
            entries,
        )

    def _chunk_tasks(
        self,
        entries: list[dict[str, Any]],
        *,
        source_id: str,
        revision: str | None,
        snapshot_id: str,
    ) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for entry in entries:
            content = str(entry["content"])
            chunk_specs = chunk_text_with_metadata(
                content,
                self.settings.chunk_size,
                self.settings.chunk_overlap,
            )
            path_hash = hashlib.sha256(
                entry["relative_path"].encode("utf-8")
            ).hexdigest()[:16]
            metadata = {
                "source_id": source_id,
                "revision": revision,
                "snapshot_id": snapshot_id,
            }
            for ordinal, item in enumerate(chunk_specs, start=1):
                tasks.append(
                    {
                        "chunk_id": f"{path_hash}#chunk-{ordinal}",
                        "document_id": path_hash,
                        "path": entry["relative_path"],
                        "language": entry.get("language"),
                        "content": str(item["content"]),
                        "line_start": int(item["line_start"]),
                        "line_end": int(item["line_end"]),
                        "metadata": metadata,
                        "is_last": ordinal == len(chunk_specs),
                    }
                )
        return tasks

    def _embed_and_publish(
        self,
        *,
        job_id: str,
        source: dict[str, Any],
        snapshot: dict[str, Any],
        generation_id: str,
        chunk_tasks: list[dict[str, Any]],
        completed_chunk_ids: set[str],
    ) -> None:
        snapshot_id = str(snapshot["snapshot_id"])
        completed_chunk_ids.intersection_update(
            str(item["chunk_id"]) for item in chunk_tasks
        )
        files_processed = sum(
            bool(item["is_last"]) and item["chunk_id"] in completed_chunk_ids
            for item in chunk_tasks
        )
        chunks_stored = len(completed_chunk_ids)
        pending_tasks = [
            item for item in chunk_tasks
            if item["chunk_id"] not in completed_chunk_ids
        ]
        self.store.update_job(
            job_id,
            status="embedding",
            stage="bge-m3_embedding",
            files_processed=files_processed,
            chunks_stored=chunks_stored,
            error=None,
            completed_at=None,
        )
        for batch_start in range(
            0, len(pending_tasks), self.settings.embedding_batch_size
        ):
            batch = pending_tasks[
                batch_start : batch_start + self.settings.embedding_batch_size
            ]
            embeddings = self.embedding_service.embed_many(
                [item["content"] for item in batch],
                input_type="passage",
            )
            embedded_batch = [
                {
                    **item,
                    "embedding": embedding.vector,
                    "embedding_provider": embedding.provider,
                    "embedding_model": embedding.model,
                }
                for item, embedding in zip(batch, embeddings)
            ]
            point_ids = self.vector_store.upsert_generation_chunks(
                project_id=source["project_id"],
                snapshot_id=snapshot_id,
                generation_id=generation_id,
                items=embedded_batch,
            )
            mappings = [
                {
                    "chunk_id": chunk["chunk_id"],
                    "document_id": chunk["document_id"],
                    "relative_path": chunk["path"],
                    "external_point_id": point_id,
                    "content_sha256": hashlib.sha256(
                        chunk["content"].encode("utf-8")
                    ).hexdigest(),
                    "content": chunk["content"],
                    "line_start": chunk["line_start"],
                    "line_end": chunk["line_end"],
                    "metadata": chunk["metadata"],
                }
                for chunk, point_id in zip(embedded_batch, point_ids)
            ]
            # This write is the durable checkpoint. Qdrant point IDs are
            # deterministic, and the PostgreSQL upsert is idempotent, so a
            # crash between either write only replays the unfinished batch.
            self.store.append_generation_chunks(generation_id, mappings)
            completed_chunk_ids.update(item["chunk_id"] for item in batch)
            files_processed += sum(bool(item["is_last"]) for item in batch)
            chunks_stored = len(completed_chunk_ids)
            self.store.update_job(
                job_id,
                files_processed=files_processed,
                chunks_stored=chunks_stored,
            )

        self.store.update_job(
            job_id, status="publishing", stage="active_generation_switch"
        )
        qdrant_count = self.vector_store.count_generation(
            source["project_id"], generation_id
        )
        postgres_count = self.store.generation_chunk_count(generation_id)
        if qdrant_count != postgres_count or qdrant_count != len(chunk_tasks):
            raise RepositoryIndexingError(
                "Index validation failed before activation: "
                f"expected={len(chunk_tasks)}, postgres={postgres_count}, "
                f"qdrant={qdrant_count}"
            )
        self.store.activate_generation(
            source_id=source["source_id"],
            project_id=source["project_id"],
            snapshot_id=snapshot_id,
            generation_id=generation_id,
            revision=snapshot.get("revision"),
            branch=snapshot.get("git_branch"),
            dirty=snapshot.get("git_dirty"),
            committed_at=snapshot.get("git_committed_at"),
            manifest_sha256=snapshot["manifest_sha256"],
            file_count=files_processed,
            chunk_count=len(chunk_tasks),
        )
        self.store.update_job(
            job_id,
            status="completed",
            stage="completed",
            files_processed=files_processed,
            chunks_stored=len(chunk_tasks),
            error=None,
            completed_at=datetime.now(timezone.utc),
        )

    def _pause_embedding_job(
        self,
        *,
        job_id: str,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        error: Exception,
    ) -> None:
        message = str(error)[:4000] or error.__class__.__name__
        try:
            self.store.pause_generation(
                project_id, snapshot_id, generation_id, message
            )
        except RepositoryStoreError:
            pass
        self.store.update_job(
            job_id,
            status="paused",
            stage="waiting_for_embedding",
            error=message,
            completed_at=None,
        )

    def _fail_job(
        self,
        *,
        job_id: str,
        project_id: str,
        snapshot_id: str | None,
        generation_id: str | None,
        error: Exception,
        delete_generation: bool,
    ) -> None:
        message = str(error)[:4000] or error.__class__.__name__
        if delete_generation and generation_id:
            try:
                self.vector_store.delete_generation(project_id, generation_id)
            except Exception:
                pass
        try:
            self.store.fail_generation(
                project_id, snapshot_id, generation_id, message
            )
        except RepositoryStoreError:
            pass
        self.store.update_job(
            job_id,
            status="failed",
            stage="failed",
            error=message,
            completed_at=datetime.now(timezone.utc),
        )

    def run(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None:
            return
        source = self.store.get_source(job["source_id"])
        if source is None:
            self.store.update_job(
                job_id,
                status="failed",
                stage="source_lookup",
                error="Repository source not found",
                completed_at=datetime.now(timezone.utc),
            )
            return
        snapshot_id: str | None = None
        generation_id: str | None = None
        repo: Repo | None = None
        try:
            if not source["enabled"]:
                raise RepositoryIndexingError("Repository source is disabled")
            self.store.update_job(
                job_id, status="inspecting", stage="git_inspection", error=None
            )
            source_root = self._source_root(source["root_relative_path"])
            repo = Repo(str(source_root))
            git = self._git_details(repo)
            active = self.store.get_active_generation(source["project_id"])
            if (
                not job["force_run"]
                and active is not None
                and source.get("last_revision") == git["revision"]
            ):
                self.store.update_job(
                    job_id,
                    snapshot_id=active["snapshot_id"],
                    generation_id=active["generation_id"],
                    status="completed",
                    stage="unchanged",
                    completed_at=datetime.now(timezone.utc),
                )
                return

            self.store.update_job(
                job_id, status="snapshotting", stage="manifest_build"
            )
            entries, total_bytes, manifest_sha256 = self._snapshot_entries(
                repo, git["tree_id"]
            )
            snapshot_id = f"snap_{git['revision'][:12]}_{uuid4().hex[:8]}"
            generation_id = f"gen_{uuid4().hex}"
            indexable_entries = [
                entry for entry in entries if entry.get("indexable")
            ]
            self.store.begin_snapshot(
                source=source,
                snapshot_id=snapshot_id,
                generation_id=generation_id,
                revision=git["revision"],
                branch=git["branch"],
                dirty=git["dirty"],
                committed_at=git["committed_at"],
                manifest_sha256=manifest_sha256,
                entries=entries,
                total_bytes=total_bytes,
            )
            self.store.update_job(
                job_id,
                snapshot_id=snapshot_id,
                generation_id=generation_id,
                status="chunking",
                stage="chunk_and_embed",
                files_total=len(indexable_entries),
                bytes_total=total_bytes,
            )
            snapshot = {
                "snapshot_id": snapshot_id,
                "revision": git["revision"],
                "git_branch": git["branch"],
                "git_dirty": git["dirty"],
                "git_committed_at": git["committed_at"],
                "manifest_sha256": manifest_sha256,
            }
            chunk_tasks = self._chunk_tasks(
                indexable_entries,
                source_id=source["source_id"],
                revision=git["revision"],
                snapshot_id=snapshot_id,
            )
            self._embed_and_publish(
                job_id=job_id,
                source=source,
                snapshot=snapshot,
                generation_id=generation_id,
                chunk_tasks=chunk_tasks,
                completed_chunk_ids=set(),
            )
        except ServiceError as exc:
            if snapshot_id and generation_id:
                self._pause_embedding_job(
                    job_id=job_id,
                    project_id=source["project_id"],
                    snapshot_id=snapshot_id,
                    generation_id=generation_id,
                    error=exc,
                )
            else:
                self._fail_job(
                    job_id=job_id,
                    project_id=source["project_id"],
                    snapshot_id=snapshot_id,
                    generation_id=generation_id,
                    error=exc,
                    delete_generation=False,
                )
        except Exception as exc:
            self._fail_job(
                job_id=job_id,
                project_id=source["project_id"],
                snapshot_id=snapshot_id,
                generation_id=generation_id,
                error=exc,
                delete_generation=True,
            )
        finally:
            if repo is not None:
                repo.close()

    def resume(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None:
            return
        source = self.store.get_source(job["source_id"])
        snapshot_id = job.get("snapshot_id")
        generation_id = job.get("generation_id")
        if source is None or not snapshot_id or not generation_id:
            return
        try:
            if not source["enabled"]:
                raise RepositoryIndexingError("Repository source is disabled")
            snapshot = self.store.get_snapshot(snapshot_id)
            generation = self.store.get_generation(generation_id)
            if snapshot is None or generation is None:
                raise RepositoryIndexingError(
                    "Durable snapshot checkpoint was not found"
                )
            if (
                snapshot["project_id"] != source["project_id"]
                or snapshot["source_id"] != source["source_id"]
                or generation["project_id"] != source["project_id"]
                or generation["snapshot_id"] != snapshot_id
            ):
                raise RepositoryIndexingError(
                    "Resume checkpoint does not match repository source"
                )
            if (
                generation["embedding_model"] != self.settings.embedding_model
                or generation["index_version"] != self.settings.index_version
            ):
                raise RepositoryIndexingError(
                    "Embedding model or index version changed; start a new generation"
                )
            entries = self.store.list_snapshot_indexable_entries(snapshot_id)
            chunk_tasks = self._chunk_tasks(
                entries,
                source_id=source["source_id"],
                revision=snapshot.get("revision"),
                snapshot_id=snapshot_id,
            )
            completed_chunk_ids = self.store.list_generation_chunk_ids(
                generation_id
            )
            qdrant_count = self.vector_store.count_generation(
                source["project_id"], generation_id
            )
            if qdrant_count < len(completed_chunk_ids):
                # PostgreSQL is ahead of Qdrant. Replaying every deterministic
                # point is the only safe repair for an arbitrary missing point.
                completed_chunk_ids.clear()
            self.store.update_job(
                job_id,
                status="chunking",
                stage="resume_checkpoint",
                files_total=len(entries),
                files_processed=sum(
                    bool(item["is_last"])
                    and item["chunk_id"] in completed_chunk_ids
                    for item in chunk_tasks
                ),
                chunks_stored=len(completed_chunk_ids),
                bytes_total=snapshot["total_bytes"],
                error=None,
                completed_at=None,
            )
            self._embed_and_publish(
                job_id=job_id,
                source=source,
                snapshot=snapshot,
                generation_id=generation_id,
                chunk_tasks=chunk_tasks,
                completed_chunk_ids=completed_chunk_ids,
            )
        except ServiceError as exc:
            self._pause_embedding_job(
                job_id=job_id,
                project_id=source["project_id"],
                snapshot_id=snapshot_id,
                generation_id=generation_id,
                error=exc,
            )
        except Exception as exc:
            # Preserve the checkpoint on resume failures. An administrator can
            # fix configuration and retry without losing completed vectors.
            self._fail_job(
                job_id=job_id,
                project_id=source["project_id"],
                snapshot_id=snapshot_id,
                generation_id=generation_id,
                error=exc,
                delete_generation=False,
            )
