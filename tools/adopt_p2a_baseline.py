from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.schema_guard import BASELINE_REVISION, inspect_p2a_baseline_schema  # noqa: E402


def _connect():
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=settings.postgres_connect_timeout_seconds,
        row_factory=dict_row,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify an existing Vision DB and optionally adopt the P2-A Alembic baseline."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Stamp the verified existing database at the P2-A baseline revision.",
    )
    args = parser.parse_args()

    if not settings.postgres_password:
        print("POSTGRES_PASSWORD is required", file=sys.stderr)
        return 2

    with _connect() as connection:
        inspection = inspect_p2a_baseline_schema(connection)

    print(f"current_revision={inspection.revision or 'unmanaged'}")
    if inspection.missing_tables:
        print("missing_tables=" + ",".join(inspection.missing_tables))
    if inspection.missing_columns:
        print("missing_columns=" + ",".join(inspection.missing_columns))
    if not inspection.baseline_compatible:
        print(
            "Database is a partial pre-P2 schema and cannot be stamped safely. "
            "After backup, run `alembic upgrade head` so the idempotent baseline "
            "migration creates only missing tables/columns/indexes.",
            file=sys.stderr,
        )
        return 3
    if inspection.revision and inspection.revision != BASELINE_REVISION:
        print(
            "Baseline adoption refused: database is already managed by another revision.",
            file=sys.stderr,
        )
        return 4
    if inspection.revision == BASELINE_REVISION:
        print("Database is already on the P2-A baseline.")
        return 0
    if not args.apply:
        print(
            "Baseline-compatible unmanaged DB. Re-run with --apply to stamp it; "
            "no application data will be rewritten."
        )
        return 0

    config = Config(str(ROOT / "alembic.ini"))
    command.stamp(config, BASELINE_REVISION)
    print(f"Stamped existing database at {BASELINE_REVISION}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

