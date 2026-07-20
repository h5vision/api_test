#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import hashlib
import json
import os
import sys
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(title="VS Code AI Assistant Backend", version="1.0.0")


class IngestRequest(BaseModel):
    document_id: str = Field(default="doc-001")
    text: str = Field(..., min_length=1)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


class EmbeddingClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("EMBEDDING_BASE_URL", "").strip().rstrip("/")
        self.api_key = os.getenv("EMBEDDING_API_KEY", "").strip()
        self.model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        self.provider = os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower()

    def embed(self, text: str) -> List[float]:
        if not text.strip():
            return []

        if self.base_url:
            try:
                return self._embed_via_http(text)
            except Exception as exc:
                print(f"Embedding API 호출 실패: {exc}", file=sys.stderr)

        return self._fallback_embedding(text)

    def _embed_via_http(self, text: str) -> List[float]:
        if self.provider == "ollama":
            payload = {"model": self.model, "input": text}
            url = f"{self.base_url}/api/embeddings"
        else:
            payload = {"input": text, "model": self.model}
            url = f"{self.base_url}/v1/embeddings"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body)

        if self.provider == "ollama":
            embedding = data.get("embedding")
        else:
            embedding = data.get("data", [{}])[0].get("embedding")

        if not isinstance(embedding, list):
            raise ValueError(f"Unexpected embedding response: {data}")
        return [float(v) for v in embedding]

    def _fallback_embedding(self, text: str) -> List[float]:
        dim = 128
        vector = []
        for i in range(dim):
            token = f"{text}:{i}".encode("utf-8")
            digest = hashlib.sha256(token).digest()
            value = (digest[0] + digest[1] + digest[2] + digest[3]) / 1020.0
            vector.append(round(value, 6))
        return vector


class VectorDBClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("VECTOR_DB_BASE_URL", "").strip().rstrip("/")
        self.collection_id = os.getenv("VECTOR_DB_COLLECTION", "default-collection").strip()
        self._local_store: List[Dict[str, Any]] = []

    def upsert(self, document_id: str, text: str, embedding: List[float], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "id": document_id,
            "collection_id": self.collection_id,
            "text": text,
            "embedding": embedding,
            "metadata": metadata or {},
        }

        if not self.base_url:
            self._local_store.append(payload)
            return {"status": "stored_locally", "collection_id": self.collection_id, "id": document_id}

        url = f"{self.base_url}/collections/{self.collection_id}/documents"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {"status": "ok"}

    def search(self, query: str, embedding: List[float], top_k: int = 3) -> List[Dict[str, Any]]:
        payload = {
            "collection_id": self.collection_id,
            "query": query,
            "embedding": embedding,
            "top_k": top_k,
        }

        if not self.base_url:
            results = []
            for item in self._local_store:
                score = self._cosine_similarity(embedding, item["embedding"])
                results.append({"id": item["id"], "text": item["text"], "score": round(score, 6)})
            return sorted(results, key=lambda x: x["score"], reverse=True)[:top_k]

        url = f"{self.base_url}/collections/{self.collection_id}/query"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body) if body else {}
            return data.get("results", [])

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0

        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = sum(a * a for a in vec_a) ** 0.5
        norm_b = sum(b * b for b in vec_b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


embedding_client = EmbeddingClient()
vector_db_client = VectorDBClient()


@app.get("/")
def health_check() -> Dict[str, str]:
    return {"status": "ok", "service": "vs-code-ai-assistant-backend"}


@app.post("/ingest")
def ingest(payload: IngestRequest) -> Dict[str, Any]:
    try:
        embedding = embedding_client.embed(payload.text)
        result = vector_db_client.upsert(payload.document_id, payload.text, embedding, metadata=payload.metadata)
        return {"message": "document ingested", **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/search")
def search(payload: QueryRequest) -> Dict[str, Any]:
    try:
        embedding = embedding_client.embed(payload.query)
        results = vector_db_client.search(payload.query, embedding, top_k=payload.top_k)
        return {"query": payload.query, "results": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/chat")
def chat(payload: QueryRequest) -> Dict[str, Any]:
    try:
        embedding = embedding_client.embed(payload.query)
        results = vector_db_client.search(payload.query, embedding, top_k=payload.top_k)
        return {
            "query": payload.query,
            "answer": "관련 문서를 기반으로 응답을 생성할 수 있습니다.",
            "results": results,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
