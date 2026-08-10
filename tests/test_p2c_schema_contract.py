from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_p2c_migration_backfills_and_links_vector_target():
    text = (ROOT / 'migrations' / 'versions' / 'p2c_0002_vector_targets.py').read_text(encoding="utf-8")
    assert 'revision = "p2c_0002_vector_targets"' in text
    assert 'down_revision = "p2a_0001_baseline"' in text
    assert 'CREATE TABLE IF NOT EXISTS vector_targets' in text
    assert 'ADD COLUMN IF NOT EXISTS vector_target_id TEXT' in text
    assert 'FROM runtime_service_settings' in text
    assert 'FOREIGN KEY (vector_target_id)' in text


def test_runtime_store_no_longer_reads_vector_host_port():
    text = (ROOT / 'backend' / 'runtime_services.py').read_text(encoding="utf-8")
    select_block = text.split('SELECT groq_enabled', 1)[1].split('FROM runtime_service_settings', 1)[0]
    assert 'vector_host' not in select_block
    assert 'vector_port' not in select_block
    assert 'vector_target_id' in select_block


def test_p2a_adoption_keeps_historical_shape_separate_from_current_guard():
    guard = (ROOT / 'backend' / 'schema_guard.py').read_text(encoding="utf-8")
    adoption = (ROOT / 'tools' / 'adopt_p2a_baseline.py').read_text(encoding="utf-8")
    assert 'P2A_BASELINE_TABLE_COLUMNS' in guard
    assert 'CURRENT_TABLE_COLUMNS' in guard
    assert 'inspect_p2a_baseline_schema' in adoption

