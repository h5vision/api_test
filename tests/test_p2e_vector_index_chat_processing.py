from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_p2e_migration_adds_vector_index_and_provider_chat_policy():
    text = (ROOT / "migrations" / "versions" / "p2e_0004_vector_indexes_chat_processing.py").read_text(encoding="utf-8")
    assert 'revision = "p2e_0004_vector_indexes_chat_processing"' in text
    assert 'down_revision = "p2d_0003_embedding_profiles"' in text
    assert "CREATE TABLE IF NOT EXISTS vector_indexes" in text
    assert "chat_processing_mode" in text
    assert "jsonb_build_object('project_id', ig.project_id, 'generation_id', ig.generation_id)" in text
    assert "'unavailable'" in text  # legacy candidates are not silently verified


def test_vector_index_registry_uses_concrete_generation_selector():
    text = (ROOT / "backend" / "vector_indexes.py").read_text(encoding="utf-8")
    assert '{"project_id": project_id, "generation_id": generation_id}' in text
    assert "$project_id" not in text
    assert "$generation_id" not in text
    assert "vector_target_id" in text
    assert "embedding_profile_id" in text


def test_both_managed_index_paths_register_vector_index():
    indexer = (ROOT / "backend" / "repository_indexer.py").read_text(encoding="utf-8")
    offline = (ROOT / "backend" / "offline_embeddings.py").read_text(encoding="utf-8")
    assert "_register_generation_vector_index" in indexer
    assert "PostgresVectorIndexStore" in indexer
    assert "_register_vector_index" in offline
    assert "PostgresVectorIndexStore" in offline


def test_provider_managed_chat_bypasses_project_resolution_and_rag():
    app = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    mode = app.index("processing_mode = generation_router.chat_processing_mode")
    direct = app.index("if direct_generation:", mode)
    resolution = app.index("project_resolution = resolve_project_id", direct)
    assert mode < direct < resolution
    assert '"provider_managed" if processing_mode == "provider_managed" else "direct"' in app
    assert '"retrieval_ms": 0' in app


def test_general_chat_can_skip_project_resolution():
    routing = (
        ROOT / "backend" / "domains" / "chat" / "routing.py"
    ).read_text(encoding="utf-8")
    assert 'route="project_grounded" if project_required else "general"' in routing
    assert "deterministic" in routing


def test_p2e_does_not_preempt_p2f_generation_fk():
    migration = (ROOT / "migrations" / "versions" / "p2e_0004_vector_indexes_chat_processing.py").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS vector_index_id" not in migration
    assert "index_generations.vector_index_id" not in migration
