from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.schema_guard import CURRENT_REVISION, require_schema  # noqa: E402


def _connect() -> psycopg.Connection[dict[str, Any]]:
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=settings.postgres_connect_timeout_seconds,
        row_factory=dict_row,
    )


CHECKS: dict[str, str] = {
    "invalid_active_route_chain": """
        SELECT pvr.project_id, pvr.active_binding_id
        FROM project_vector_routes AS pvr
        LEFT JOIN snapshot_vector_bindings AS svb
          ON svb.binding_id = pvr.active_binding_id
        LEFT JOIN project_snapshots AS ps ON ps.snapshot_id = svb.snapshot_id
        LEFT JOIN vector_indexes AS vi ON vi.vector_index_id = svb.vector_index_id
        LEFT JOIN vector_targets AS vt ON vt.vector_target_id = vi.vector_target_id
        LEFT JOIN embedding_profiles AS ep
          ON ep.embedding_profile_id = vi.embedding_profile_id
        LEFT JOIN index_generations AS ig ON ig.generation_id = svb.generation_id
        LEFT JOIN external_vector_index_verifications AS ev
          ON ev.vector_index_id = vi.vector_index_id
         AND ev.tenant_id = svb.tenant_id
        WHERE pvr.active_binding_id IS NOT NULL
          AND (
            svb.binding_id IS NULL
            OR ps.snapshot_id IS NULL
            OR vi.vector_index_id IS NULL
            OR vt.vector_target_id IS NULL
            OR ep.embedding_profile_id IS NULL
            OR ps.project_id <> pvr.project_id
            OR pvr.tenant_id <> svb.tenant_id
            OR svb.tenant_id <> ps.tenant_id
            OR svb.tenant_id <> vi.tenant_id
            OR svb.tenant_id <> vt.tenant_id
            OR svb.tenant_id <> ep.tenant_id
            OR svb.verification_state <> 'verified'
            OR ps.status <> 'completed'
            OR vi.status <> 'ready'
            OR vt.status = 'disabled'
            OR ep.status = 'disabled'
            OR (
              vi.ownership_mode = 'vision_managed'
              AND (
                svb.binding_source <> 'managed_generation'
                OR svb.generation_id IS NULL
                OR ig.status <> 'ready'
                OR ig.project_id <> pvr.project_id
                OR ig.snapshot_id <> svb.snapshot_id
                OR ig.vector_index_id <> svb.vector_index_id
                OR vi.selector ->> 'project_id' <> pvr.project_id
                OR vi.selector ->> 'generation_id' <> svb.generation_id
              )
            )
            OR (
              vi.ownership_mode = 'external_attached'
              AND (
                svb.binding_source <> 'external_verification'
                OR svb.generation_id IS NOT NULL
                OR ev.verification_state <> 'compatible'
              )
            )
            OR vi.ownership_mode NOT IN ('vision_managed', 'external_attached')
          )
        ORDER BY pvr.project_id
    """,
    "configured_tenant_violation": """
        SELECT project_id, tenant_id
        FROM project_vector_routes
        WHERE tenant_id <> %s
        ORDER BY project_id
    """,
    "incomplete_route_audit": """
        WITH aggregate AS (
          SELECT project_id, COUNT(*) AS event_count,
                 MIN(revision) AS min_revision, MAX(revision) AS max_revision
          FROM project_vector_route_events
          GROUP BY project_id
        )
        SELECT pvr.project_id, pvr.revision,
               COALESCE(a.event_count, 0) AS event_count,
               a.min_revision, a.max_revision
        FROM project_vector_routes AS pvr
        LEFT JOIN aggregate AS a ON a.project_id = pvr.project_id
        WHERE NOT (
          (pvr.revision = 0 AND COALESCE(a.event_count, 0) = 0)
          OR (
            pvr.revision >= 1
            AND a.event_count = pvr.revision
            AND a.min_revision = 1
            AND a.max_revision = pvr.revision
          )
        )
        ORDER BY pvr.project_id
    """,
    "broken_route_event_transition": """
        WITH ordered AS (
          SELECT project_id, revision, from_binding_id, to_binding_id,
                 LAG(to_binding_id) OVER (
                   PARTITION BY project_id ORDER BY revision
                 ) AS previous_to_binding_id
          FROM project_vector_route_events
        )
        SELECT project_id, revision, from_binding_id, previous_to_binding_id
        FROM ordered
        WHERE (revision = 1 AND from_binding_id IS NOT NULL)
           OR (revision > 1 AND from_binding_id IS DISTINCT FROM previous_to_binding_id)
        ORDER BY project_id, revision
    """,
    "route_head_event_mismatch": """
        SELECT pvr.project_id, pvr.active_binding_id, latest.to_binding_id
        FROM project_vector_routes AS pvr
        LEFT JOIN LATERAL (
          SELECT to_binding_id
          FROM project_vector_route_events AS event
          WHERE event.project_id = pvr.project_id
          ORDER BY event.revision DESC
          LIMIT 1
        ) AS latest ON TRUE
        WHERE pvr.revision > 0
          AND pvr.active_binding_id IS DISTINCT FROM latest.to_binding_id
        ORDER BY pvr.project_id
    """,
}


def main() -> int:
    if not settings.postgres_password:
        print("POSTGRES_PASSWORD is required", file=sys.stderr)
        return 2

    expected_tenant = settings.snapshot_tenant_id.strip() or "vision-default"
    report: dict[str, Any] = {
        "expected_revision": CURRENT_REVISION,
        "expected_tenant_id": expected_tenant,
        "checks": {},
    }
    with _connect() as connection:
        require_schema(connection)
        for name, sql in CHECKS.items():
            parameters = (expected_tenant,) if name == "configured_tenant_violation" else ()
            rows = connection.execute(sql, parameters).fetchall()
            report["checks"][name] = {
                "ok": not rows,
                "count": len(rows),
                "rows": rows[:25],
            }

    report["ok"] = all(item["ok"] for item in report["checks"].values())
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
