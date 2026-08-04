from __future__ import annotations

import json
import math
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .schemas import Source


class VectorStoreError(RuntimeError):
    pass


class SQLiteVectorStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS document_chunks (
                    project_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    path TEXT,
                    language TEXT,
                    content TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    embedding_provider TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, chunk_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_project ON document_chunks(project_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(project_id, document_id)"
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(document_chunks)")
            }
            if "generation_id" not in columns:
                connection.execute(
                    "ALTER TABLE document_chunks ADD COLUMN generation_id TEXT"
                )
            if "snapshot_id" not in columns:
                connection.execute(
                    "ALTER TABLE document_chunks ADD COLUMN snapshot_id TEXT"
                )

    def replace_document(
        self,
        project_id: str,
        document_id: str,
        path: str | None,
        language: str | None,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> int:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM document_chunks WHERE project_id = ? AND document_id = ?",
                (project_id, document_id),
            )
            for chunk in chunks:
                connection.execute(
                    """
                    INSERT INTO document_chunks (
                        project_id, chunk_id, document_id, path, language, content,
                        embedding_json, embedding_provider, embedding_model,
                        metadata_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        chunk["chunk_id"],
                        document_id,
                        path,
                        language,
                        chunk["content"],
                        json.dumps(chunk["embedding"], separators=(",", ":")),
                        chunk["embedding_provider"],
                        chunk["embedding_model"],
                        json.dumps(metadata, ensure_ascii=False),
                        timestamp,
                    ),
                )
        return len(chunks)

    def search(
        self,
        project_id: str,
        vector: list[float],
        embedding_provider: str,
        embedding_model: str,
        top_k: int,
        generation_id: str | None = None,
    ) -> list[Source]:
        with self._lock, self._connect() as connection:
            query = """
                SELECT chunk_id, document_id, path, language, content,
                       embedding_json, metadata_json
                FROM document_chunks
                WHERE project_id = ?
                  AND embedding_provider = ?
                  AND embedding_model = ?
            """
            parameters: list[Any] = [project_id, embedding_provider, embedding_model]
            if generation_id is not None:
                query += " AND generation_id = ?"
                parameters.append(generation_id)
            rows = connection.execute(query, tuple(parameters)).fetchall()

        results: list[Source] = []
        for row in rows:
            stored = json.loads(row["embedding_json"])
            score = self._cosine_similarity(vector, stored)
            results.append(
                Source(
                    document_id=row["document_id"],
                    chunk_id=row["chunk_id"],
                    path=row["path"],
                    language=row["language"],
                    text=row["content"],
                    score=round(score, 6),
                    metadata=json.loads(row["metadata_json"]),
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    def upsert_generation_document(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        document_id: str,
        path: str,
        language: str | None,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> list[str]:
        return self.upsert_generation_chunks(
            project_id=project_id,
            snapshot_id=snapshot_id,
            generation_id=generation_id,
            items=[
                {
                    **chunk,
                    "document_id": document_id,
                    "path": path,
                    "language": language,
                    "metadata": metadata,
                }
                for chunk in chunks
            ],
        )

    def upsert_generation_chunks(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        items: list[dict[str, Any]],
    ) -> list[str]:
        timestamp = datetime.now(timezone.utc).isoformat()
        point_ids: list[str] = []
        with self._lock, self._connect() as connection:
            for chunk in items:
                point_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"{project_id}:{generation_id}:{chunk['chunk_id']}",
                    )
                )
                point_ids.append(point_id)
                connection.execute(
                    """
                    INSERT OR REPLACE INTO document_chunks (
                        project_id, chunk_id, document_id, path, language, content,
                        embedding_json, embedding_provider, embedding_model,
                        metadata_json, updated_at, generation_id, snapshot_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        chunk["chunk_id"],
                        chunk["document_id"],
                        chunk["path"],
                        chunk.get("language"),
                        chunk["content"],
                        json.dumps(chunk["embedding"], separators=(",", ":")),
                        chunk["embedding_provider"],
                        chunk["embedding_model"],
                        json.dumps(chunk.get("metadata", {}), ensure_ascii=False),
                        timestamp,
                        generation_id,
                        snapshot_id,
                    ),
                )
        return point_ids

    def count_generation(self, project_id: str, generation_id: str) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM document_chunks
                WHERE project_id = ? AND generation_id = ?
                """,
                (project_id, generation_id),
            ).fetchone()
            return int(row["count"])

    def delete_generation(self, project_id: str, generation_id: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM document_chunks
                WHERE project_id = ? AND generation_id = ?
                """,
                (project_id, generation_id),
            )
            return cursor.rowcount

    def delete_project(self, project_id: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM document_chunks WHERE project_id = ?", (project_id,)
            )
            return cursor.rowcount

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS chunks, COUNT(DISTINCT project_id) AS projects FROM document_chunks"
            ).fetchone()
            return {
                "provider": "sqlite",
                "status": "ok",
                "projects": int(row["projects"]),
                "chunks": int(row["chunks"]),
            }

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return dot / (left_norm * right_norm)


class QdrantVectorStore:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        collection: str,
        vector_size: int,
        index_version: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.collection = collection
        self.vector_size = vector_size
        self.index_version = index_version
        self.timeout_seconds = timeout_seconds
        self._initialized = False
        self._initialize_lock = threading.Lock()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["api-key"] = self.api_key
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return {"status": "not_found"}
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise VectorStoreError(
                f"Qdrant가 HTTP {exc.code}을 반환했습니다: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise VectorStoreError(f"Qdrant에 연결할 수 없습니다: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise VectorStoreError("Qdrant 응답이 올바른 JSON이 아닙니다.") from exc

    def _ensure_collection(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            current = self._request(
                "GET", f"/collections/{self.collection}", allow_not_found=True
            )
            if current.get("status") == "not_found":
                self._request(
                    "PUT",
                    f"/collections/{self.collection}",
                    {
                        "vectors": {
                            "size": self.vector_size,
                            "distance": "Cosine",
                        }
                    },
                )
            else:
                configured_size = (
                    current.get("result", {})
                    .get("config", {})
                    .get("params", {})
                    .get("vectors", {})
                    .get("size")
                )
                if configured_size not in {None, self.vector_size}:
                    raise VectorStoreError(
                        "Qdrant collection vector size mismatch: "
                        f"expected={self.vector_size}, actual={configured_size}"
                    )
            self._initialized = True

    @staticmethod
    def _filter(
        project_id: str,
        document_id: str | None = None,
        generation_id: str | None = None,
    ) -> dict[str, Any]:
        must: list[dict[str, Any]] = [
            {"key": "project_id", "match": {"value": project_id}}
        ]
        if document_id is not None:
            must.append({"key": "document_id", "match": {"value": document_id}})
        if generation_id is not None:
            must.append({"key": "generation_id", "match": {"value": generation_id}})
        return {"must": must}

    def replace_document(
        self,
        project_id: str,
        document_id: str,
        path: str | None,
        language: str | None,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> int:
        self._ensure_collection()
        self._request(
            "POST",
            f"/collections/{self.collection}/points/delete?wait=true",
            {"filter": self._filter(project_id, document_id)},
        )
        if not chunks:
            return 0
        points = []
        for chunk in chunks:
            vector = chunk["embedding"]
            if len(vector) != self.vector_size:
                raise VectorStoreError(
                    "저장할 embedding 차원이 Qdrant collection과 일치하지 않습니다."
                )
            point_id = str(uuid5(NAMESPACE_URL, f"{project_id}:{chunk['chunk_id']}"))
            points.append(
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "project_id": project_id,
                        "document_id": document_id,
                        "document_version_id": chunk.get("document_version_id"),
                        "chunk_id": chunk["chunk_id"],
                        "path": path,
                        "language": language,
                        "content": chunk["content"],
                        "line_start": chunk.get("line_start"),
                        "line_end": chunk.get("line_end"),
                        "embedding_provider": chunk["embedding_provider"],
                        "embedding_model": chunk["embedding_model"],
                        "index_version": self.index_version,
                        "metadata": metadata,
                    },
                }
            )
        self._request(
            "PUT",
            f"/collections/{self.collection}/points?wait=true",
            {"points": points},
        )
        return len(points)

    def upsert_generation_document(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        document_id: str,
        path: str,
        language: str | None,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> list[str]:
        return self.upsert_generation_chunks(
            project_id=project_id,
            snapshot_id=snapshot_id,
            generation_id=generation_id,
            items=[
                {
                    **chunk,
                    "document_id": document_id,
                    "path": path,
                    "language": language,
                    "metadata": metadata,
                }
                for chunk in chunks
            ],
        )

    def upsert_generation_chunks(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        items: list[dict[str, Any]],
    ) -> list[str]:
        self._ensure_collection()
        points: list[dict[str, Any]] = []
        point_ids: list[str] = []
        for chunk in items:
            vector = chunk["embedding"]
            if len(vector) != self.vector_size:
                raise VectorStoreError(
                    "저장할 embedding 차원이 Qdrant collection과 일치하지 않습니다."
                )
            point_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project_id}:{generation_id}:{chunk['chunk_id']}",
                )
            )
            point_ids.append(point_id)
            points.append(
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "project_id": project_id,
                        "snapshot_id": snapshot_id,
                        "generation_id": generation_id,
                        "document_id": chunk["document_id"],
                        "document_version_id": (
                            f"{snapshot_id}:{chunk['document_id']}"
                        ),
                        "chunk_id": chunk["chunk_id"],
                        "path": chunk["path"],
                        "language": chunk.get("language"),
                        "content": chunk["content"],
                        "line_start": chunk.get("line_start"),
                        "line_end": chunk.get("line_end"),
                        "embedding_provider": chunk["embedding_provider"],
                        "embedding_model": chunk["embedding_model"],
                        "index_version": self.index_version,
                        "metadata": chunk.get("metadata", {}),
                    },
                }
            )
        if points:
            self._request(
                "PUT",
                f"/collections/{self.collection}/points?wait=true",
                {"points": points},
            )
        return point_ids

    def search(
        self,
        project_id: str,
        vector: list[float],
        embedding_provider: str,
        embedding_model: str,
        top_k: int,
        generation_id: str | None = None,
    ) -> list[Source]:
        self._ensure_collection()
        must = [
            {"key": "project_id", "match": {"value": project_id}},
            {
                "key": "embedding_provider",
                "match": {"value": embedding_provider},
            },
            {
                "key": "embedding_model",
                "match": {"value": embedding_model},
            },
            {
                "key": "index_version",
                "match": {"value": self.index_version},
            },
        ]
        if generation_id is not None:
            must.append(
                {"key": "generation_id", "match": {"value": generation_id}}
            )
        data = self._request(
            "POST",
            f"/collections/{self.collection}/points/search",
            {
                "vector": vector,
                "filter": {"must": must},
                "limit": top_k,
                "with_payload": True,
                "with_vector": False,
            },
        )
        rows = data.get("result", [])
        results: list[Source] = []
        for row in rows if isinstance(rows, list) else []:
            payload = row.get("payload", {}) if isinstance(row, dict) else {}
            results.append(
                Source(
                    document_id=str(payload.get("document_id", "")),
                    document_version_id=payload.get("document_version_id"),
                    chunk_id=str(payload.get("chunk_id", "")),
                    path=payload.get("path"),
                    language=payload.get("language"),
                    line_start=payload.get("line_start"),
                    line_end=payload.get("line_end"),
                    text=str(payload.get("content", "")),
                    score=round(float(row.get("score", 0.0)), 6),
                    metadata=payload.get("metadata", {}),
                )
            )
        return results

    def count_generation(self, project_id: str, generation_id: str) -> int:
        self._ensure_collection()
        data = self._request(
            "POST",
            f"/collections/{self.collection}/points/count",
            {
                "filter": self._filter(
                    project_id, generation_id=generation_id
                ),
                "exact": True,
            },
        )
        return int(data.get("result", {}).get("count", 0))

    def delete_generation(self, project_id: str, generation_id: str) -> int:
        self._ensure_collection()
        count = self.count_generation(project_id, generation_id)
        self._request(
            "POST",
            f"/collections/{self.collection}/points/delete?wait=true",
            {
                "filter": self._filter(
                    project_id, generation_id=generation_id
                )
            },
        )
        return count

    def delete_project(self, project_id: str) -> int:
        self._ensure_collection()
        count_data = self._request(
            "POST",
            f"/collections/{self.collection}/points/count",
            {"filter": self._filter(project_id), "exact": True},
        )
        count = int(count_data.get("result", {}).get("count", 0))
        self._request(
            "POST",
            f"/collections/{self.collection}/points/delete?wait=true",
            {"filter": self._filter(project_id)},
        )
        return count

    def stats(self) -> dict[str, Any]:
        try:
            self._ensure_collection()
            data = self._request("GET", f"/collections/{self.collection}")
            result = data.get("result", {})
            return {
                "provider": "qdrant",
                "status": "ok",
                "collection": self.collection,
                "projects": 0,
                "chunks": int(result.get("points_count") or 0),
            }
        except VectorStoreError as exc:
            return {
                "provider": "qdrant",
                "status": "unavailable",
                "collection": self.collection,
                "projects": 0,
                "chunks": 0,
                "error": str(exc),
            }
