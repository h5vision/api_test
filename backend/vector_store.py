from __future__ import annotations

import json
import math
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import Source


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
    ) -> list[Source]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chunk_id, document_id, path, language, content,
                       embedding_json, metadata_json
                FROM document_chunks
                WHERE project_id = ?
                  AND embedding_provider = ?
                  AND embedding_model = ?
                """,
                (project_id, embedding_provider, embedding_model),
            ).fetchall()

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

    def delete_project(self, project_id: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM document_chunks WHERE project_id = ?", (project_id,)
            )
            return cursor.rowcount

    def stats(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS chunks, COUNT(DISTINCT project_id) AS projects FROM document_chunks"
            ).fetchone()
            return {"projects": int(row["projects"]), "chunks": int(row["chunks"])}

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

