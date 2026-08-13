from __future__ import annotations

from pathlib import Path

from backend.schema_guard import CURRENT_REVISION, BASELINE_TABLE_COLUMNS


ROOT = Path(__file__).resolve().parents[1]


def test_external_project_registry_is_the_current_alembic_revision() -> None:
    assert CURRENT_REVISION == "p3_0010_external_project_registry"
    for table in ("rag_targets", "external_project_catalog", "project_external_bindings"):
        assert table in BASELINE_TABLE_COLUMNS


def test_external_project_registry_migration_keeps_binding_identity_only() -> None:
    text = (
        ROOT
        / "migrations"
        / "versions"
        / "p3_0010_external_project_registry.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "p3_0009_chat_intake_normalization"' in text
    assert "CREATE TABLE IF NOT EXISTS rag_targets" in text
    assert "CREATE TABLE IF NOT EXISTS external_project_catalog" in text
    assert "CREATE TABLE IF NOT EXISTS project_external_bindings" in text
    binding_section = text.split("CREATE TABLE IF NOT EXISTS project_external_bindings", 1)[1]
    binding_section = binding_section.split('"""', 1)[0]
    assert "active_snapshot_id" not in binding_section
    assert "verified_revision" not in binding_section
