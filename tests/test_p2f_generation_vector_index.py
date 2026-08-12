from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def test_p2f_migration_adds_generation_vector_index_fk_without_legacy_guessing():
    text = (ROOT / "migrations" / "versions" / "p2f_0005_generation_vector_index.py").read_text(encoding="utf-8")
    assert 'revision = "p2f_0005_generation_vector_index"' in text
    assert 'down_revision = "p2e_0004_vector_indexes_chat_processing"' in text
    assert "ADD COLUMN IF NOT EXISTS vector_index_id TEXT" in text
    assert "FOREIGN KEY (vector_index_id)" in text
    assert "status IN ('building', 'ready', 'retired')" in text
    assert "unavailable" in text
    assert "ALTER COLUMN vector_index_id SET NOT NULL" not in text


def test_schema_guard_requires_p2f_but_p2a_shape_stays_historical():
    text = (ROOT / "backend" / "schema_guard.py").read_text(encoding="utf-8")
    assert 'CURRENT_REVISION = "p3_0009_chat_intake_normalization"' in text
    assert '"vector_index_id"' in text
    assert 'if column not in {"vector_index_id", "ready_at"}' in text


def test_generation_binding_is_immutable_and_completion_requires_provenance():
    text = (
        ROOT / "backend" / "domains" / "repositories" / "repository_store.py"
    ).read_text(encoding="utf-8")
    assert "def bind_generation_vector_index" in text
    assert "vector_index_id IS NULL OR vector_index_id = %s" in text
    assert "already bound to a different VectorIndex" in text
    assert "P2-F requires generation.vector_index_id before build completion" in text
    assert "ig.vector_index_id" in text


def test_git_and_offline_indexing_bind_the_registered_vector_index():
    git = (
        ROOT / "backend" / "domains" / "repositories" / "repository_indexer.py"
    ).read_text(encoding="utf-8")
    offline = (
        ROOT / "backend" / "domains" / "repositories" / "offline_embeddings.py"
    ).read_text(encoding="utf-8")
    assert "self.store.bind_generation_vector_index" in git
    assert "self.store.bind_generation_vector_index" in offline


def test_retrieval_uses_project_vector_route_binding_not_runtime_collection():
    app = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")
    block = app.split("def _resolve_project_vector_runtime", 1)[1].split("def _search_documents_with_runtime", 1)[0]
    assert "project_vector_route_store.get(project_id)" in block
    assert "route.active_binding_id" in block
    assert "candidate_context(route.active_binding_id)" in block
    assert "snapshot_vector_binding_store.get(route.active_binding_id)" in block
    assert "vector_index_store.get(binding.vector_index_id)" in block
    assert "vector_target_store.get(index.vector_target_id)" in block
    assert "embedding_profile_store.get(index.embedding_profile_id)" in block
    assert "settings_for_vector_index" in block
    assert "build_vector_store_for_index" in block
    assert "PROJECT_VECTOR_ROUTE_REQUIRED" in block
    assert "active_generation_id" not in block


def test_agentic_rag_reuses_one_resolved_vector_route():
    app = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")
    project_chat = app.split("effective_project_id = project_resolution.resolved_project_id", 1)[1]
    assert "retrieval_runtime = _resolve_project_vector_runtime(effective_project_id)" in project_chat
    assert "_search_documents_with_runtime(" in project_chat
    assert "retrieval_index.vector_index_id" in project_chat
    assert "retrieval_profile.embedding_profile_id" in project_chat


def _load_catalog_module(monkeypatch):
    # Load the pure catalog helper without importing backend package __init__ side effects.
    schemas = types.ModuleType("backend.schemas")

    class ModelInfo:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    schemas.ModelInfo = ModelInfo
    monkeypatch.setitem(sys.modules, "backend.schemas", schemas)
    package = types.ModuleType("backend")
    package.__path__ = [str(ROOT / "backend")]
    monkeypatch.setitem(sys.modules, "backend", package)
    import importlib
    return importlib.import_module("backend.model_catalog"), ModelInfo


def test_catalog_revision_is_content_derived_and_ignores_availability_flaps(monkeypatch):
    module, ModelInfo = _load_catalog_module(monkeypatch)
    base = dict(
        model_id="provider:a:m1",
        model_name="m1",
        display_name="A (m1)",
        provider="custom",
        location="internal",
        deployment_type="remote_server",
        endpoint="a:1",
        enabled=True,
        available=True,
        is_default=True,
        streaming=False,
    )
    first = ModelInfo(**base)
    changed_health = ModelInfo(**{**base, "available": False, "endpoint": "a:2"})
    r1 = module.model_catalog_revision("provider:a:m1", [first])
    r2 = module.model_catalog_revision("provider:a:m1", [changed_health])
    assert r1 == r2
    changed_catalog = ModelInfo(**{**base, "model_name": "m2", "display_name": "A (m2)"})
    assert module.model_catalog_revision("provider:a:m1", [changed_catalog]) != r1


def test_model_list_contract_exposes_catalog_revision():
    schemas = (
        ROOT / "backend" / "contracts" / "models.py"
    ).read_text(encoding="utf-8")
    app = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")
    assert "catalog_revision: str" in schemas
    assert "catalog_revision=model_catalog_revision" in app
