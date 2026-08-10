from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal

from .config import Settings
from .embedding_profiles import EmbeddingProfileRecord
from .vector_indexes import VectorIndexRecord
from .vector_targets import VectorTargetRecord
from .vector_store import (
    ManagedVectorStore,
    ManagedVectorStoreFacade,
    QdrantVectorAdapter,
    VectorEngineAdapter,
)

VectorEngine = Literal["qdrant"]
VectorOwnershipMode = Literal["vision_managed", "external_attached"]


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _secret_signature(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class VectorTarget:
    """Physical Qdrant deployment selected by runtime configuration."""

    target_id: str
    engine: VectorEngine
    endpoint: str
    deployment_type: str
    search_backend: str


@dataclass(frozen=True)
class EmbeddingProfile:
    """Embedding execution contract independent from vector storage."""

    profile_id: str
    deployment: str
    provider: str
    base_url: str
    model: str
    model_id: str
    dimension: int


@dataclass(frozen=True)
class VectorIndexDescriptor:
    """Logical searchable vector set on a physical target."""

    vector_index_id: str
    target_id: str
    collection: str
    selector: dict[str, Any]
    embedding_profile_id: str
    index_version: str
    ownership_mode: VectorOwnershipMode
    distance_metric: str
    query_strategy: str


@dataclass(frozen=True)
class VectorRuntimePlan:
    target: VectorTarget
    embedding: EmbeddingProfile
    index: VectorIndexDescriptor


def _deployment_type(endpoint: str) -> str:
    normalized = endpoint.lower()
    local_tokens = ("127.0.0.1", "localhost", "::1", "host.docker.internal")
    return "local" if any(token in normalized for token in local_tokens) else "remote_server"


def build_vector_runtime_plan(settings: Settings) -> VectorRuntimePlan:
    """Translate current administrator/bootstrap settings into P2 identities.

    P2-C resolves the physical target from PostgreSQL ``vector_targets``.
    P2-D resolves embedding compatibility from persistent ``embedding_profiles``.
    PostgreSQL remains Vision's relational persistence database and Qdrant the vector engine.
    """

    provider = settings.vector_db_provider.strip().lower()
    if provider != "qdrant":
        raise RuntimeError(
            "P2-B supports Qdrant as the canonical vector engine; "
            f"configured VECTOR_DB_PROVIDER={settings.vector_db_provider!r}"
        )

    endpoint = settings.qdrant_url.rstrip("/")
    target_id = settings.vector_target_id.strip()
    if not target_id:
        raise RuntimeError("P2-C requires a selected persistent vector_target_id")
    if not endpoint:
        raise RuntimeError("P2-C selected VectorTarget has no resolved endpoint")
    embedding_profile_id = settings.embedding_profile_id.strip()
    if not embedding_profile_id:
        raise RuntimeError("P2-D requires a selected persistent embedding_profile_id")
    selector_template = {"project_id": "$project_id", "generation_id": "$generation_id"}
    vector_index_id = _stable_id(
        "vindex",
        {
            "target_id": target_id,
            "collection": settings.qdrant_collection,
            "selector": selector_template,
            "embedding_profile_id": embedding_profile_id,
            "index_version": settings.index_version,
            "distance_metric": "cosine",
        },
    )

    target = VectorTarget(
        target_id=target_id,
        engine="qdrant",
        endpoint=endpoint,
        deployment_type=_deployment_type(endpoint),
        search_backend="qdrant-native",
    )
    embedding = EmbeddingProfile(
        profile_id=embedding_profile_id,
        deployment=settings.embedding_deployment,
        provider=settings.embedding_provider,
        base_url=settings.embedding_base_url.rstrip("/"),
        model=settings.embedding_model,
        model_id=settings.embedding_model_id,
        dimension=settings.embedding_dimension,
    )
    index = VectorIndexDescriptor(
        vector_index_id=vector_index_id,
        target_id=target_id,
        collection=settings.qdrant_collection,
        selector=selector_template,
        embedding_profile_id=embedding_profile_id,
        index_version=settings.index_version,
        ownership_mode="vision_managed",
        distance_metric="cosine",
        query_strategy="qdrant-query-api",
    )
    return VectorRuntimePlan(target=target, embedding=embedding, index=index)


def build_vector_adapter(settings: Settings) -> VectorEngineAdapter:
    """Build the target-scoped canonical P2-B vector engine adapter."""

    plan = build_vector_runtime_plan(settings)
    return QdrantVectorAdapter(
        plan.target.endpoint,
        settings.qdrant_api_key,
        settings.request_timeout_seconds,
    )


def build_vector_store(settings: Settings) -> ManagedVectorStore:
    """Build the temporary managed-index compatibility façade.

    Existing Vision callers still speak Project/Generation semantics. The façade
    translates those semantics into VectorIndexRef/Selector/Point contracts and
    delegates all physical I/O to the canonical VectorEngineAdapter.
    """

    plan = build_vector_runtime_plan(settings)
    return ManagedVectorStoreFacade(
        build_vector_adapter(settings),
        collection=plan.index.collection,
        vector_size=plan.embedding.dimension,
        index_version=plan.index.index_version,
        distance_metric=plan.index.distance_metric,
    )


class RuntimeVectorAdapter:
    """Dynamic target-scoped adapter following administrator-saved target config."""

    def __init__(self, settings_provider: Callable[[], Settings]) -> None:
        self._settings_provider = settings_provider
        self._lock = threading.RLock()
        self._signature: tuple[Any, ...] | None = None
        self._adapter: VectorEngineAdapter | None = None

    @staticmethod
    def _runtime_signature(settings: Settings) -> tuple[Any, ...]:
        return (
            settings.vector_db_provider,
            settings.qdrant_url,
            settings.request_timeout_seconds,
            _secret_signature(settings.qdrant_api_key),
        )

    def current(self) -> VectorEngineAdapter:
        settings = self._settings_provider()
        signature = self._runtime_signature(settings)
        with self._lock:
            if self._adapter is None or self._signature != signature:
                self._adapter = build_vector_adapter(settings)
                self._signature = signature
            return self._adapter


class RuntimeVectorStore:
    """Temporary dynamic managed-index façade for current API/search callers.

    API/search follows administrator settings immediately. Indexing/import jobs
    continue to call build_vector_store() with one immutable Settings snapshot.
    """

    def __init__(self, settings_provider: Callable[[], Settings]) -> None:
        self._settings_provider = settings_provider
        self._lock = threading.RLock()
        self._signature: tuple[Any, ...] | None = None
        self._store: ManagedVectorStore | None = None

    @staticmethod
    def _runtime_signature(settings: Settings) -> tuple[Any, ...]:
        return (
            settings.vector_db_provider,
            settings.qdrant_url,
            settings.qdrant_collection,
            settings.embedding_profile_id,
            settings.embedding_dimension,
            settings.index_version,
            settings.request_timeout_seconds,
            _secret_signature(settings.qdrant_api_key),
        )

    def _current_store(self) -> ManagedVectorStore:
        settings = self._settings_provider()
        signature = self._runtime_signature(settings)
        with self._lock:
            if self._store is None or self._signature != signature:
                self._store = build_vector_store(settings)
                self._signature = signature
            return self._store

    def replace_document(self, *args: Any, **kwargs: Any) -> int:
        return self._current_store().replace_document(*args, **kwargs)

    def upsert_generation_chunks(self, *args: Any, **kwargs: Any) -> list[str]:
        return self._current_store().upsert_generation_chunks(*args, **kwargs)

    def search(self, *args: Any, **kwargs: Any):
        return self._current_store().search(*args, **kwargs)

    def count_generation(self, *args: Any, **kwargs: Any) -> int:
        return self._current_store().count_generation(*args, **kwargs)

    def delete_generation(self, *args: Any, **kwargs: Any) -> int:
        return self._current_store().delete_generation(*args, **kwargs)

    def delete_project(self, *args: Any, **kwargs: Any) -> int:
        return self._current_store().delete_project(*args, **kwargs)

    def stats(self) -> dict[str, Any]:
        values = dict(self._current_store().stats())
        plan = build_vector_runtime_plan(self._settings_provider())
        values.update(
            {
                "target_id": plan.target.target_id,
                "vector_index_id": plan.index.vector_index_id,
                "runtime_source": "vector-target+embedding-profile-registry",
            }
        )
        return values


def settings_for_vector_index(
    base: Settings,
    *,
    index: VectorIndexRecord,
    target: VectorTargetRecord,
    profile: EmbeddingProfileRecord,
) -> Settings:
    """Resolve one persisted VectorIndex into an immutable retrieval Settings snapshot."""
    if index.vector_target_id != target.vector_target_id:
        raise RuntimeError("VectorIndex target reference is inconsistent")
    if index.embedding_profile_id != profile.embedding_profile_id:
        raise RuntimeError("VectorIndex embedding profile reference is inconsistent")
    if target.engine != "qdrant" or not target.enabled:
        raise RuntimeError("VectorIndex target is unavailable")
    if not profile.enabled:
        raise RuntimeError("VectorIndex embedding profile is unavailable")
    if index.status != "ready":
        raise RuntimeError(
            f"VectorIndex is not ready for retrieval: status={index.status}"
        )
    if not index.collection.strip():
        raise RuntimeError("VectorIndex collection is empty")
    if not index.index_version.strip():
        raise RuntimeError("VectorIndex version is empty")
    return replace(
        base,
        vector_db_provider=target.engine,
        vector_target_id=target.vector_target_id,
        qdrant_url=target.endpoint,
        qdrant_collection=index.collection,
        embedding_profile_id=profile.embedding_profile_id,
        embedding_deployment=profile.deployment,
        embedding_provider=profile.provider,
        embedding_base_url=profile.base_url,
        embedding_model=profile.model,
        embedding_model_id=profile.model_id,
        embedding_dimension=profile.dimension,
        embedding_batch_size=profile.batch_size,
        index_version=index.index_version,
    )


def build_vector_store_for_index(
    settings: Settings,
    *,
    index: VectorIndexRecord,
    target: VectorTargetRecord,
    profile: EmbeddingProfileRecord,
) -> ManagedVectorStore:
    """Build retrieval from persisted P2-E/P2-F provenance, not runtime defaults."""
    resolved = settings_for_vector_index(
        settings,
        index=index,
        target=target,
        profile=profile,
    )
    adapter = QdrantVectorAdapter(
        target.endpoint,
        resolved.qdrant_api_key,
        resolved.request_timeout_seconds,
    )
    return ManagedVectorStoreFacade(
        adapter,
        collection=index.collection,
        vector_size=profile.dimension,
        index_version=index.index_version,
        distance_metric=index.distance_metric,
        selector=index.selector,
        query_selector_authoritative=True,
    )

