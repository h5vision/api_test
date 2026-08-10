from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.vector_gateway import (
    build_vector_adapter,
    build_vector_runtime_plan,
    build_vector_store,
)
from backend.vector_store import (
    ManagedVectorStoreFacade,
    QdrantVectorAdapter,
    VectorSelector,
    VectorSelectorConflictError,
    merge_selectors,
)


def _settings(**overrides):
    values = dict(
        vector_db_provider="qdrant",
        vector_target_id="vtarget_runtime_a",
        qdrant_url="http://vector-runtime.invalid:6333",
        qdrant_api_key="",
        qdrant_collection="vision_vectors",
        embedding_profile_id="eprof_runtime_a",
        embedding_deployment="api",
        embedding_provider="ollama",
        embedding_base_url="http://embedding-runtime.invalid:11434",
        embedding_model="provider/embed-model",
        embedding_model_id="embed-profile",
        embedding_dimension=1024,
        index_version="v1",
        request_timeout_seconds=10,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_persistent_target_id_is_runtime_identity():
    first = build_vector_runtime_plan(_settings())
    second = build_vector_runtime_plan(
        _settings(
            vector_target_id="vtarget_runtime_b",
            qdrant_url="https://example-qdrant.invalid",
        )
    )
    assert first.target.target_id == "vtarget_runtime_a"
    assert second.target.target_id == "vtarget_runtime_b"
    assert first.index.vector_index_id != second.index.vector_index_id


def test_persistent_embedding_profile_is_independent_from_vector_target():
    first = build_vector_runtime_plan(_settings())
    second = build_vector_runtime_plan(
        _settings(
            embedding_profile_id="eprof_runtime_b",
            embedding_provider="openai",
            embedding_base_url="https://embeddings.example.invalid/v1",
            embedding_model="custom-embed",
            embedding_model_id="custom-embed",
        )
    )
    assert first.target.target_id == second.target.target_id
    assert first.embedding.profile_id == "eprof_runtime_a"
    assert second.embedding.profile_id == "eprof_runtime_b"
    assert first.index.vector_index_id != second.index.vector_index_id


def test_missing_persistent_embedding_profile_is_rejected():
    with pytest.raises(RuntimeError, match="P2-D"):
        build_vector_runtime_plan(_settings(embedding_profile_id=""))


def test_qdrant_is_the_only_p2b_vector_engine():
    adapter = build_vector_adapter(_settings())
    store = build_vector_store(_settings())
    assert isinstance(adapter, QdrantVectorAdapter)
    assert isinstance(store, ManagedVectorStoreFacade)
    with pytest.raises(RuntimeError, match="Qdrant"):
        build_vector_adapter(_settings(vector_db_provider="sqlite"))


def test_selector_conflict_cannot_escape_logical_index_boundary():
    merged = merge_selectors(
        VectorSelector({"tenant": "a"}),
        VectorSelector({"language": "python"}),
    )
    assert merged.match == {"tenant": "a", "language": "python"}
    with pytest.raises(VectorSelectorConflictError):
        merge_selectors(
            VectorSelector({"tenant": "a"}),
            VectorSelector({"tenant": "b"}),
        )

