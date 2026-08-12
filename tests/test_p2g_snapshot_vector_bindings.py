from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_p2g_migration_adds_binding_registry_and_backfills_only_p2f_bound_managed_rows():
    text = (ROOT / "migrations" / "versions" / "p2g_0006_snapshot_vector_bindings.py").read_text(encoding="utf-8")
    assert 'revision = "p2g_0006_snapshot_vector_bindings"' in text
    assert 'down_revision = "p2f_0005_generation_vector_index"' in text
    assert "CREATE TABLE IF NOT EXISTS snapshot_vector_bindings" in text
    assert "FOREIGN KEY (snapshot_id) REFERENCES project_snapshots(snapshot_id)" in text
    assert "FOREIGN KEY (vector_index_id) REFERENCES vector_indexes(vector_index_id)" in text
    assert "FOREIGN KEY (generation_id) REFERENCES index_generations(generation_id)" in text
    assert "ig.vector_index_id IS NOT NULL" in text
    assert "vi.ownership_mode = 'vision_managed'" in text
    assert "vi.selector = jsonb_build_object" in text


def test_schema_guard_requires_p2g_binding_shape():
    text = (ROOT / "backend" / "schema_guard.py").read_text(encoding="utf-8")
    assert 'CURRENT_REVISION = "p3_0009_chat_intake_normalization"' in text
    assert '"snapshot_vector_bindings"' in text
    assert '"snapshot_fingerprint"' in text
    assert '"vector_index_identity_key"' in text
    assert 'P2A_BASELINE_TABLE_COLUMNS.pop("snapshot_vector_bindings", None)' in text


def test_managed_binding_registration_requires_generation_snapshot_index_agreement():
    text = (ROOT / "backend" / "domains" / "vector_indexes" / "snapshot_vector_bindings.py").read_text(encoding="utf-8")
    assert "JOIN project_snapshots AS ps ON ps.snapshot_id = ig.snapshot_id" in text
    assert "JOIN vector_indexes AS vi ON vi.vector_index_id = ig.vector_index_id" in text
    assert "Managed Snapshot/Generation/VectorIndex provenance does not agree" in text
    assert "ownership_mode" in text
    assert 'selector.get("generation_id") != generation_id' in text
    assert "verification_state = CASE" in text


def test_both_managed_index_paths_register_snapshot_vector_binding():
    indexer = (
        ROOT / "backend" / "domains" / "repositories" / "repository_indexer.py"
    ).read_text(encoding="utf-8")
    offline = (
        ROOT / "backend" / "domains" / "repositories" / "offline_embeddings.py"
    ).read_text(encoding="utf-8")
    assert "PostgresSnapshotVectorBindingStore" in indexer
    assert "register_managed_generation" in indexer
    assert "snapshot_id=snapshot_id" in indexer
    assert "PostgresSnapshotVectorBindingStore" in offline
    assert "register_managed_generation" in offline
    assert "snapshot_id=snapshot_id" in offline


def test_build_completion_requires_binding_and_verifies_it_atomically_without_routing():
    text = (
        ROOT / "backend" / "domains" / "repositories" / "repository_store.py"
    ).read_text(encoding="utf-8")
    completion = text.split("def complete_generation", 1)[1].split("def fail_generation", 1)[0]
    assert "FROM snapshot_vector_bindings" in completion
    assert "P2-G requires SnapshotVectorBinding before build completion" in completion
    assert "verification_state = 'verified'" in completion
    assert "verified_at = COALESCE(verified_at, NOW())" in completion
    assert "SET status = 'ready'" in completion
    assert "active_generation_id =" not in completion
    assert "SET status = 'retired'" not in completion


def test_generation_failure_marks_unverified_binding_failed():
    text = (
        ROOT / "backend" / "domains" / "repositories" / "repository_store.py"
    ).read_text(encoding="utf-8")
    failure = text.split("def fail_generation", 1)[1]
    assert "UPDATE snapshot_vector_bindings" in failure
    assert "verification_state = 'failed'" in failure
    assert "verification_state <> 'verified'" in failure


def test_retrieval_requires_verified_active_route_binding_before_vector_target_resolution():
    text = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")
    runtime = text.split("def _resolve_project_vector_runtime", 1)[1].split("def _search_documents_with_runtime", 1)[0]
    assert "snapshot_vector_binding_store.get(route.active_binding_id)" in runtime
    assert "SNAPSHOT_VECTOR_BINDING_REQUIRED" in runtime
    assert runtime.index("snapshot_vector_binding_store.get(route.active_binding_id)") < runtime.index("vector_target_store.get")
    assert '"binding": binding' in runtime


def test_search_and_chat_expose_binding_provenance_for_p3():
    schemas = (
        ROOT / "backend" / "contracts" / "common.py"
    ).read_text(encoding="utf-8")
    app = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")
    assert "snapshot_vector_binding_id: str | None = None" in schemas
    assert 'snapshot_vector_binding_id=binding.binding_id' in app
    assert '"snapshot_vector_binding_id": retrieval_binding.binding_id' in app
    assert '"snapshot_id": retrieval_binding.snapshot_id' in app
    assert '"generation_id": retrieval_binding.generation_id' in app


def test_p2g_managed_contract_remains_distinct_under_p2h_external_extension():
    text = (ROOT / "backend" / "domains" / "vector_indexes" / "snapshot_vector_bindings.py").read_text(encoding="utf-8")
    assert "register_managed_generation" in text
    assert "register_external_verification" in text
    assert "binding_source='external_verification'" in text
    assert "generation_id IS NULL" in text
