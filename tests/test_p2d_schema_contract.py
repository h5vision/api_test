from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_p2d_migration_backfills_and_links_embedding_profile():
    text = (ROOT / "migrations" / "versions" / "p2d_0003_embedding_profiles.py").read_text(encoding="utf-8")
    assert 'revision = "p2d_0003_embedding_profiles"' in text
    assert 'down_revision = "p2c_0002_vector_targets"' in text
    assert "CREATE TABLE IF NOT EXISTS embedding_profiles" in text
    assert "ADD COLUMN IF NOT EXISTS embedding_profile_id TEXT" in text
    assert "FROM runtime_service_settings" in text
    assert "FOREIGN KEY (embedding_profile_id)" in text
    assert "embedding_batch_size" not in text.split("substr(md5(", 1)[1].split("), 1, 24)", 1)[0]


def test_runtime_store_reads_selected_profile_id_not_legacy_embedding_fields():
    text = (ROOT / "backend" / "runtime_services.py").read_text(encoding="utf-8")
    select_block = text.split("SELECT groq_enabled", 1)[1].split("FROM runtime_service_settings", 1)[0]
    assert "embedding_profile_id" in select_block
    assert "embedding_provider" not in select_block
    assert "embedding_base_url" not in select_block
    assert "embedding_model_id" not in select_block
    assert "embedding_dimension" not in select_block


def test_p2d_shape_remains_required_under_p2f_current_guard():
    guard = (ROOT / "backend" / "schema_guard.py").read_text(encoding="utf-8")
    assert 'CURRENT_REVISION = "p3_0010_external_project_registry"' in guard
    assert '"embedding_profiles"' in guard
    assert 'P2A_BASELINE_TABLE_COLUMNS.pop("embedding_profiles", None)' in guard
    assert '"embedding_profile_id"' in guard
