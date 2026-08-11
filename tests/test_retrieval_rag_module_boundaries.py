from __future__ import annotations

import importlib


def test_legacy_retrieval_rag_imports_alias_canonical_owners() -> None:
    mappings = {
        "backend.retrieval": "backend.domains.retrieval.reranking",
        "backend.agentic_rag": "backend.domains.rag.agentic",
        "backend.rag_evaluation": "backend.domains.retrieval.evaluation",
        "backend.rag_lab": "backend.integrations.vectordb.rag_lab",
    }
    for legacy_name, canonical_name in mappings.items():
        legacy = importlib.import_module(legacy_name)
        canonical = importlib.import_module(canonical_name)
        assert legacy is canonical


def test_agentic_rag_uses_canonical_retrieval_types() -> None:
    agentic = importlib.import_module("backend.domains.rag.agentic")
    retrieval = importlib.import_module("backend.domains.retrieval.reranking")
    assert agentic.AdaptiveReranker is retrieval.AdaptiveReranker
    assert agentic.RetrievalDecision is retrieval.RetrievalDecision
