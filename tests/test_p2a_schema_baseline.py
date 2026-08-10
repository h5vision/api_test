from __future__ import annotations

from pathlib import Path

import pytest

from backend.schema_guard import (
    BASELINE_REVISION,
    BASELINE_TABLE_COLUMNS,
    CURRENT_REVISION,
    SchemaStateError,
    inspect_schema,
    require_schema,
)


class _Result:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = many or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class _Connection:
    def __init__(self, *, revision=BASELINE_REVISION, omit_table=None, omit_column=None):
        self.revision = revision
        self.omit_table = omit_table
        self.omit_column = omit_column

    def execute(self, sql):
        text = str(sql)
        if "to_regclass('public.alembic_version')" in text:
            return _Result(one={"name": "alembic_version"} if self.revision else {"name": None})
        if "SELECT version_num FROM alembic_version" in text:
            return _Result(one={"version_num": self.revision})
        if "information_schema.columns" in text:
            rows = []
            for table, columns in BASELINE_TABLE_COLUMNS.items():
                if table == self.omit_table:
                    continue
                for column in columns:
                    if self.omit_column == f"{table}.{column}":
                        continue
                    rows.append({"table_name": table, "column_name": column})
            return _Result(many=rows)
        raise AssertionError(text)


def test_baseline_contract_is_structurally_compatible_but_not_current():
    inspection = inspect_schema(_Connection())
    assert inspection.baseline_compatible
    assert inspection.revision == BASELINE_REVISION
    with pytest.raises(SchemaStateError, match=f"expected={CURRENT_REVISION}"):
        require_schema(_Connection())


def test_baseline_contract_rejects_unmanaged_schema():
    with pytest.raises(SchemaStateError, match="not Alembic-managed"):
        require_schema(_Connection(revision=None))


def test_baseline_contract_reports_missing_column():
    missing = "project_snapshots.fingerprint"
    inspection = inspect_schema(_Connection(omit_column=missing))
    assert missing in inspection.missing_columns
    with pytest.raises(SchemaStateError, match="missing columns"):
        require_schema(_Connection(omit_column=missing))


def test_postgres_stores_do_not_contain_runtime_ddl():
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "backend" / "repository_store.py",
        root / "backend" / "project_store.py",
        root / "backend" / "runtime_services.py",
        root / "backend" / "runtime_config.py",
        root / "backend" / "ai_providers.py",
        root / "backend" / "connectivity.py",
        root / "backend" / "frontend_clients.py",
        root / "backend" / "metadata_store.py",
        root / "backend" / "model_access.py",
        root / "backend" / "snapshots" / "repository.py",
    ]
    forbidden = (
        "CREATE TABLE IF NOT EXISTS",
        "ALTER TABLE",
        "CREATE INDEX IF NOT EXISTS",
        "CREATE UNIQUE INDEX IF NOT EXISTS",
    )
    for path in files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"runtime DDL remains in {path.name}: {token}"


def test_baseline_normalizes_legacy_columns_before_dependent_indexes():
    root = Path(__file__).resolve().parents[1]
    migration = (
        root / "migrations" / "versions" / "p2a_0001_current_baseline.py"
    ).read_text(encoding="utf-8")

    snapshot_column = migration.index("ADD COLUMN IF NOT EXISTS fingerprint TEXT")
    snapshot_index = migration.index("CREATE UNIQUE INDEX IF NOT EXISTS uq_project_snapshots_fingerprint")
    client_column = migration.index("ADD COLUMN IF NOT EXISTS instance_id TEXT")
    client_index = migration.index("CREATE UNIQUE INDEX IF NOT EXISTS uq_frontend_clients_instance_id")

    assert snapshot_column < snapshot_index
    assert client_column < client_index


def test_p2_migrations_execute_literal_sql_without_bind_parameter_parsing():
    root = Path(__file__).resolve().parents[1]
    versions = root / "migrations" / "versions"

    for path in sorted(versions.glob("p2*.py")):
        text = path.read_text(encoding="utf-8")
        assert "op.get_bind().exec_driver_sql(statement)" in text, path.name
        assert "op.execute(statement)" not in text, path.name


def test_baseline_widens_alembic_revision_column_for_named_p2_revisions():
    root = Path(__file__).resolve().parents[1]
    migration = (
        root / "migrations" / "versions" / "p2a_0001_current_baseline.py"
    ).read_text(encoding="utf-8")

    assert "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)" in migration
