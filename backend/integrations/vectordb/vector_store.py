from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol, TypeAlias, runtime_checkable
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

from .schemas import Source


VectorScalar: TypeAlias = str | int | float | bool


class VectorStoreError(RuntimeError):
    pass


class VectorIndexNotFoundError(VectorStoreError):
    pass


class VectorIndexCompatibilityError(VectorStoreError):
    pass


class VectorSelectorConflictError(VectorStoreError):
    pass


class VectorCapabilityError(VectorStoreError):
    pass


@dataclass(frozen=True)
class VectorCapabilities:
    dense_vectors: bool
    sparse_vectors: bool
    payload_filter: bool
    exact_count: bool
    provision_index: bool
    named_vectors: bool = False
    hybrid_query: bool = False
    rrf: bool = False
    quantization: bool = False


@dataclass(frozen=True)
class VectorTargetHealth:
    reachable: bool
    engine: str
    latency_ms: float | None
    version: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class VectorSelector:
    """Portable P2-B selector: AND of exact scalar payload matches."""

    match: dict[str, VectorScalar] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorIndexRef:
    """Physical collection locator plus the immutable logical-index boundary."""

    collection: str
    selector: VectorSelector = field(default_factory=VectorSelector)


@dataclass(frozen=True)
class VectorIndexSpec:
    collection: str
    dimension: int
    distance_metric: str
    vector_type: str = "dense"


@dataclass(frozen=True)
class VectorIndexState:
    exists: bool
    collection: str
    dimension: int | None
    distance_metric: str | None
    vector_type: str | None
    points_count: int | None
    status: str


@dataclass(frozen=True)
class VectorPoint:
    point_id: str
    vector: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True)
class VectorWriteResult:
    point_ids: list[str]

    @property
    def written_count(self) -> int:
        return len(self.point_ids)


@dataclass(frozen=True)
class VectorQuery:
    vector: list[float]
    top_k: int
    selector: VectorSelector = field(default_factory=VectorSelector)
    include_payload: bool = True


@dataclass(frozen=True)
class VectorMatch:
    point_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class VectorDeleteResult:
    deleted_count: int | None


@dataclass(frozen=True)
class VectorPointSample:
    point_id: str
    payload: dict[str, Any]
    vector: list[float] | None = None


@runtime_checkable
class VectorEngineAdapter(Protocol):
    """Canonical P2-B vector engine I/O contract.

    The adapter is target-scoped. It deliberately knows nothing about Vision
    Project, Snapshot, Generation, EmbeddingProfile, or Source semantics.
    """

    def capabilities(self) -> VectorCapabilities: ...

    def health(self) -> VectorTargetHealth: ...

    def describe_index(self, index: VectorIndexRef) -> VectorIndexState: ...

    def discover_indexes(self) -> list[VectorIndexState]: ...

    def provision_index(self, spec: VectorIndexSpec) -> VectorIndexState: ...

    def upsert(
        self, index: VectorIndexRef, points: list[VectorPoint]
    ) -> VectorWriteResult: ...

    def query(self, index: VectorIndexRef, request: VectorQuery) -> list[VectorMatch]: ...

    def count(
        self, index: VectorIndexRef, selector: VectorSelector | None = None
    ) -> int: ...

    def delete(
        self, index: VectorIndexRef, selector: VectorSelector
    ) -> VectorDeleteResult: ...

    def sample(
        self,
        index: VectorIndexRef,
        *,
        limit: int = 10,
        include_vectors: bool = False,
    ) -> list[VectorPointSample]: ...


@runtime_checkable
class ManagedVectorStore(Protocol):
    """Temporary P2-B compatibility façade for current Vision callers.

    This is not the canonical engine contract. P2-C~F will migrate callers to
    persistent vector_index_id based orchestration and retire this façade.
    """

    def replace_document(
        self,
        project_id: str,
        document_id: str,
        path: str | None,
        language: str | None,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> int: ...

    def upsert_generation_chunks(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        items: list[dict[str, Any]],
    ) -> list[str]: ...

    def search(
        self,
        project_id: str,
        vector: list[float],
        embedding_provider: str,
        embedding_model: str,
        top_k: int,
        generation_id: str | None = None,
    ) -> list[Source]: ...

    def count_generation(self, project_id: str, generation_id: str) -> int: ...

    def delete_generation(self, project_id: str, generation_id: str) -> int: ...

    def delete_project(self, project_id: str) -> int: ...

    def stats(self) -> dict[str, Any]: ...


def merge_selectors(
    base: VectorSelector,
    operation: VectorSelector | None,
) -> VectorSelector:
    merged = dict(base.match)
    if operation is None:
        return VectorSelector(merged)
    for key, value in operation.match.items():
        if key in merged and merged[key] != value:
            raise VectorSelectorConflictError(
                f"Vector selector conflict for key={key!r}: "
                f"index={merged[key]!r}, operation={value!r}"
            )
        merged[key] = value
    return VectorSelector(merged)


def _normalize_distance(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return {
        "cosine": "cosine",
        "dot": "dot",
        "euclid": "euclid",
        "euclidean": "euclid",
        "manhattan": "manhattan",
    }.get(normalized, normalized)


class QdrantVectorAdapter:
    """Qdrant implementation of the target-scoped P2-B engine contract."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
        allow_http_statuses: frozenset[int] = frozenset(),
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
            if exc.code in allow_http_statuses:
                return {"status": "http_error", "status_code": exc.code}
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise VectorStoreError(
                f"Qdrant returned HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise VectorStoreError(f"Qdrant is unavailable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise VectorStoreError("Qdrant returned invalid JSON") from exc

    @staticmethod
    def _collection_path(collection: str) -> str:
        normalized = collection.strip()
        if not normalized:
            raise ValueError("collection must not be blank")
        return quote(normalized, safe="")

    @staticmethod
    def _qdrant_filter(selector: VectorSelector) -> dict[str, Any] | None:
        if not selector.match:
            return None
        return {
            "must": [
                {"key": key, "match": {"value": value}}
                for key, value in sorted(selector.match.items())
            ]
        }

    @staticmethod
    def _parse_vector_config(result: dict[str, Any]) -> tuple[int | None, str | None, str]:
        vectors = (
            result.get("config", {})
            .get("params", {})
            .get("vectors", {})
        )
        if isinstance(vectors, dict) and "size" in vectors:
            dimension = int(vectors["size"]) if vectors.get("size") is not None else None
            distance = _normalize_distance(str(vectors.get("distance"))) if vectors.get("distance") else None
            return dimension, distance, "dense"
        if isinstance(vectors, dict) and vectors:
            # Named-vector collections are discoverable in P2-B but managed writes
            # remain single unnamed dense-vector only until a later contract extension.
            return None, None, "named"
        return None, None, "dense"

    def capabilities(self) -> VectorCapabilities:
        return VectorCapabilities(
            dense_vectors=True,
            sparse_vectors=True,
            payload_filter=True,
            exact_count=True,
            provision_index=True,
            named_vectors=True,
            hybrid_query=True,
            rrf=True,
            quantization=True,
        )

    def health(self) -> VectorTargetHealth:
        started = perf_counter()
        try:
            data = self._request("GET", "/")
            latency_ms = round((perf_counter() - started) * 1000, 2)
            version = data.get("version")
            if version is None and isinstance(data.get("result"), dict):
                version = data["result"].get("version")
            return VectorTargetHealth(
                reachable=True,
                engine="qdrant",
                latency_ms=latency_ms,
                version=str(version) if version else None,
            )
        except VectorStoreError as exc:
            return VectorTargetHealth(
                reachable=False,
                engine="qdrant",
                latency_ms=round((perf_counter() - started) * 1000, 2),
                detail=str(exc),
            )

    def describe_index(self, index: VectorIndexRef) -> VectorIndexState:
        collection = self._collection_path(index.collection)
        data = self._request(
            "GET", f"/collections/{collection}", allow_not_found=True
        )
        if data.get("status") == "not_found":
            return VectorIndexState(
                exists=False,
                collection=index.collection,
                dimension=None,
                distance_metric=None,
                vector_type=None,
                points_count=None,
                status="missing",
            )
        result = data.get("result", {})
        dimension, distance, vector_type = self._parse_vector_config(result)
        return VectorIndexState(
            exists=True,
            collection=index.collection,
            dimension=dimension,
            distance_metric=distance,
            vector_type=vector_type,
            points_count=int(result.get("points_count") or 0),
            status=str(result.get("status") or "ok").lower(),
        )

    def discover_indexes(self) -> list[VectorIndexState]:
        """Discover physical Qdrant collections without registering logical indexes."""
        data = self._request("GET", "/collections")
        result = data.get("result", {})
        rows = result.get("collections", []) if isinstance(result, dict) else []
        states: list[VectorIndexState] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            states.append(self.describe_index(VectorIndexRef(collection=name)))
        return sorted(states, key=lambda state: state.collection)

    def provision_index(self, spec: VectorIndexSpec) -> VectorIndexState:
        if spec.vector_type != "dense":
            raise VectorCapabilityError(
                "P2-B managed provisioning supports only a single unnamed dense vector"
            )
        if spec.dimension < 1:
            raise ValueError("dimension must be positive")
        index = VectorIndexRef(collection=spec.collection)
        current = self.describe_index(index)
        requested_distance = _normalize_distance(spec.distance_metric)
        if current.exists:
            if current.vector_type not in {None, "dense"}:
                raise VectorIndexCompatibilityError(
                    f"Collection {spec.collection!r} is not a single dense-vector index"
                )
            if current.dimension not in {None, spec.dimension}:
                raise VectorIndexCompatibilityError(
                    "Qdrant collection vector size mismatch: "
                    f"expected={spec.dimension}, actual={current.dimension}"
                )
            if (
                current.distance_metric is not None
                and requested_distance is not None
                and current.distance_metric != requested_distance
            ):
                raise VectorIndexCompatibilityError(
                    "Qdrant collection distance mismatch: "
                    f"expected={requested_distance}, actual={current.distance_metric}"
                )
            return current

        qdrant_distance = {
            "cosine": "Cosine",
            "dot": "Dot",
            "euclid": "Euclid",
            "manhattan": "Manhattan",
        }.get(requested_distance or "cosine")
        if qdrant_distance is None:
            raise VectorCapabilityError(
                f"Unsupported Qdrant distance metric: {spec.distance_metric}"
            )
        collection = self._collection_path(spec.collection)
        self._request(
            "PUT",
            f"/collections/{collection}",
            {"vectors": {"size": spec.dimension, "distance": qdrant_distance}},
        )
        return self.describe_index(index)

    def _require_index(self, index: VectorIndexRef) -> VectorIndexState:
        state = self.describe_index(index)
        if not state.exists:
            raise VectorIndexNotFoundError(
                f"Vector collection does not exist: {index.collection}"
            )
        return state

    def upsert(
        self, index: VectorIndexRef, points: list[VectorPoint]
    ) -> VectorWriteResult:
        if not points:
            return VectorWriteResult([])
        state = self._require_index(index)
        if state.vector_type not in {None, "dense"}:
            raise VectorCapabilityError(
                "P2-B managed writes do not support named-vector collections"
            )
        rows: list[dict[str, Any]] = []
        point_ids: list[str] = []
        for point in points:
            if state.dimension is not None and len(point.vector) != state.dimension:
                raise VectorIndexCompatibilityError(
                    "Vector dimension does not match collection: "
                    f"expected={state.dimension}, actual={len(point.vector)}"
                )
            point_ids.append(point.point_id)
            rows.append(
                {
                    "id": point.point_id,
                    "vector": point.vector,
                    "payload": point.payload,
                }
            )
        collection = self._collection_path(index.collection)
        self._request(
            "PUT",
            f"/collections/{collection}/points?wait=true",
            {"points": rows},
        )
        return VectorWriteResult(point_ids)

    def query(self, index: VectorIndexRef, request: VectorQuery) -> list[VectorMatch]:
        if request.top_k < 1:
            return []
        state = self._require_index(index)
        if state.dimension is not None and len(request.vector) != state.dimension:
            raise VectorIndexCompatibilityError(
                "Query vector dimension does not match collection: "
                f"expected={state.dimension}, actual={len(request.vector)}"
            )
        selector = merge_selectors(index.selector, request.selector)
        payload: dict[str, Any] = {
            "query": request.vector,
            "limit": request.top_k,
            "with_payload": request.include_payload,
            "with_vector": False,
        }
        filter_value = self._qdrant_filter(selector)
        if filter_value is not None:
            payload["filter"] = filter_value
        collection = self._collection_path(index.collection)
        data = self._request(
            "POST",
            f"/collections/{collection}/points/query",
            payload,
            allow_http_statuses=frozenset({404, 405}),
        )
        if data.get("status") == "http_error":
            fallback = {
                "vector": request.vector,
                "limit": request.top_k,
                "with_payload": request.include_payload,
                "with_vector": False,
            }
            if filter_value is not None:
                fallback["filter"] = filter_value
            data = self._request(
                "POST",
                f"/collections/{collection}/points/search",
                fallback,
            )
            rows = data.get("result", [])
        else:
            result = data.get("result", {})
            rows = result.get("points", []) if isinstance(result, dict) else []

        matches: list[VectorMatch] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            payload_value = row.get("payload")
            matches.append(
                VectorMatch(
                    point_id=str(row.get("id") or ""),
                    score=float(row.get("score") or 0.0),
                    payload=payload_value if isinstance(payload_value, dict) else {},
                )
            )
        return matches

    def count(
        self, index: VectorIndexRef, selector: VectorSelector | None = None
    ) -> int:
        self._require_index(index)
        merged = merge_selectors(index.selector, selector)
        payload: dict[str, Any] = {"exact": True}
        filter_value = self._qdrant_filter(merged)
        if filter_value is not None:
            payload["filter"] = filter_value
        collection = self._collection_path(index.collection)
        data = self._request(
            "POST",
            f"/collections/{collection}/points/count",
            payload,
        )
        return int(data.get("result", {}).get("count", 0))

    def delete(
        self, index: VectorIndexRef, selector: VectorSelector
    ) -> VectorDeleteResult:
        self._require_index(index)
        merged = merge_selectors(index.selector, selector)
        if not merged.match:
            raise VectorStoreError(
                "Refusing unscoped vector delete; a non-empty selector is required"
            )
        count = self.count(index, selector)
        collection = self._collection_path(index.collection)
        self._request(
            "POST",
            f"/collections/{collection}/points/delete?wait=true",
            {"filter": self._qdrant_filter(merged)},
        )
        return VectorDeleteResult(deleted_count=count)

    def sample(
        self,
        index: VectorIndexRef,
        *,
        limit: int = 10,
        include_vectors: bool = False,
    ) -> list[VectorPointSample]:
        if limit < 1:
            return []
        self._require_index(index)
        payload: dict[str, Any] = {
            "limit": min(100, int(limit)),
            "with_payload": True,
            "with_vector": bool(include_vectors),
        }
        filter_value = self._qdrant_filter(index.selector)
        if filter_value is not None:
            payload["filter"] = filter_value
        collection = self._collection_path(index.collection)
        data = self._request(
            "POST",
            f"/collections/{collection}/points/scroll",
            payload,
        )
        result = data.get("result", {})
        rows = result.get("points", []) if isinstance(result, dict) else []
        samples: list[VectorPointSample] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            payload_value = row.get("payload")
            vector_value = row.get("vector") if include_vectors else None
            samples.append(
                VectorPointSample(
                    point_id=str(row.get("id") or ""),
                    payload=payload_value if isinstance(payload_value, dict) else {},
                    vector=(
                        [float(value) for value in vector_value]
                        if isinstance(vector_value, list)
                        else None
                    ),
                )
            )
        return samples


class ManagedVectorStoreFacade:
    """Translate current Vision managed-index semantics into the P2-B adapter."""

    def __init__(
        self,
        adapter: VectorEngineAdapter,
        *,
        collection: str,
        vector_size: int,
        index_version: str,
        distance_metric: str = "cosine",
        selector: dict[str, VectorScalar] | None = None,
        query_selector_authoritative: bool = False,
    ) -> None:
        self.adapter = adapter
        self.index = VectorIndexRef(
            collection=collection,
            selector=VectorSelector(dict(selector or {})),
        )
        self.vector_size = vector_size
        self.index_version = index_version
        self.distance_metric = _normalize_distance(distance_metric) or "cosine"
        self.query_selector_authoritative = bool(query_selector_authoritative)

    def _state(self) -> VectorIndexState:
        return self.adapter.describe_index(self.index)

    def _ensure_managed_index(self) -> VectorIndexState:
        return self.adapter.provision_index(
            VectorIndexSpec(
                collection=self.index.collection,
                dimension=self.vector_size,
                distance_metric=self.distance_metric,
            )
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
        self._ensure_managed_index()
        document_selector = VectorSelector(
            {"project_id": project_id, "document_id": document_id}
        )
        try:
            self.adapter.delete(self.index, document_selector)
        except VectorIndexNotFoundError:
            pass
        if not chunks:
            return 0
        points: list[VectorPoint] = []
        for chunk in chunks:
            point_id = str(uuid5(NAMESPACE_URL, f"{project_id}:{chunk['chunk_id']}"))
            points.append(
                VectorPoint(
                    point_id=point_id,
                    vector=[float(value) for value in chunk["embedding"]],
                    payload={
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
                )
            )
        return self.adapter.upsert(self.index, points).written_count

    def upsert_generation_chunks(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        generation_id: str,
        items: list[dict[str, Any]],
    ) -> list[str]:
        self._ensure_managed_index()
        points: list[VectorPoint] = []
        for chunk in items:
            point_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{project_id}:{generation_id}:{chunk['chunk_id']}",
                )
            )
            points.append(
                VectorPoint(
                    point_id=point_id,
                    vector=[float(value) for value in chunk["embedding"]],
                    payload={
                        "project_id": project_id,
                        "snapshot_id": snapshot_id,
                        "generation_id": generation_id,
                        "document_id": chunk["document_id"],
                        "document_version_id": f"{snapshot_id}:{chunk['document_id']}",
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
                )
            )
        return self.adapter.upsert(self.index, points).point_ids

    def search(
        self,
        project_id: str,
        vector: list[float],
        embedding_provider: str,
        embedding_model: str,
        top_k: int,
        generation_id: str | None = None,
    ) -> list[Source]:
        state = self._state()
        if not state.exists:
            return []
        if self.query_selector_authoritative:
            operation_selector = VectorSelector()
        else:
            selector_values: dict[str, VectorScalar] = {
                "project_id": project_id,
                "embedding_model": embedding_model,
                "index_version": self.index_version,
            }
            if generation_id is not None:
                selector_values["generation_id"] = generation_id
            operation_selector = VectorSelector(selector_values)
        matches = self.adapter.query(
            self.index,
            VectorQuery(
                vector=vector,
                top_k=top_k,
                selector=operation_selector,
            ),
        )
        results: list[Source] = []
        for match in matches:
            payload = match.payload
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
                    score=round(match.score, 6),
                    metadata=(
                        payload.get("metadata")
                        if isinstance(payload.get("metadata"), dict)
                        else {}
                    ),
                )
            )
        return results

    def count_generation(self, project_id: str, generation_id: str) -> int:
        if not self._state().exists:
            return 0
        return self.adapter.count(
            self.index,
            VectorSelector(
                {"project_id": project_id, "generation_id": generation_id}
            ),
        )

    def delete_generation(self, project_id: str, generation_id: str) -> int:
        if not self._state().exists:
            return 0
        result = self.adapter.delete(
            self.index,
            VectorSelector(
                {"project_id": project_id, "generation_id": generation_id}
            ),
        )
        return int(result.deleted_count or 0)

    def delete_project(self, project_id: str) -> int:
        if not self._state().exists:
            return 0
        result = self.adapter.delete(
            self.index,
            VectorSelector({"project_id": project_id}),
        )
        return int(result.deleted_count or 0)

    def stats(self) -> dict[str, Any]:
        health = self.adapter.health()
        if not health.reachable:
            return {
                "provider": health.engine,
                "status": "unavailable",
                "collection": self.index.collection,
                "projects": 0,
                "chunks": 0,
                "error": health.detail,
            }
        try:
            state = self.adapter.describe_index(self.index)
        except VectorStoreError as exc:
            return {
                "provider": health.engine,
                "status": "unavailable",
                "collection": self.index.collection,
                "projects": 0,
                "chunks": 0,
                "error": str(exc),
            }
        return {
            "provider": health.engine,
            "status": "ok" if state.exists else "missing",
            "collection": self.index.collection,
            "projects": 0,
            "chunks": int(state.points_count or 0),
            "dimension": state.dimension,
            "distance_metric": state.distance_metric,
        }
