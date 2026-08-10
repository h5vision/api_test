"""Add the project-scoped Vector routing control plane.

Revision ID: p2i_0008_project_vector_routes
Revises: p2h_0007_external_vector_indexes
"""
from __future__ import annotations

from alembic import op

revision = "p2i_0008_project_vector_routes"
down_revision = "p2h_0007_external_vector_indexes"
branch_labels = None
depends_on = None


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    # P2-I removes routing semantics from IndexGeneration.status. Historical
    # activated_at remains as audit information; ready_at becomes build-readiness time.
    _sql("""
    ALTER TABLE index_generations
        ADD COLUMN IF NOT EXISTS ready_at TIMESTAMPTZ NULL
    """)
    _sql("""
    UPDATE index_generations
    SET status = 'ready',
        ready_at = COALESCE(ready_at, activated_at, created_at)
    WHERE status = 'active'
    """)
    _sql("""
    UPDATE index_generations
    SET ready_at = COALESCE(ready_at, created_at)
    WHERE status = 'ready' AND ready_at IS NULL
    """)

    _sql("""
    CREATE TABLE IF NOT EXISTS project_vector_routes (
        project_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL DEFAULT 'vision-default',
        active_binding_id TEXT NULL,
        routing_mode TEXT NOT NULL DEFAULT 'managed_auto'
            CHECK (routing_mode IN ('managed_auto', 'pinned')),
        revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
        selected_by TEXT NULL,
        selected_at TIMESTAMPTZ NULL,
        reason TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT fk_project_vector_routes_project
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        CONSTRAINT fk_project_vector_routes_binding
            FOREIGN KEY (active_binding_id)
            REFERENCES snapshot_vector_bindings(binding_id) ON DELETE RESTRICT
    )
    """)
    _sql("""
    CREATE INDEX IF NOT EXISTS idx_project_vector_routes_active_binding
        ON project_vector_routes (active_binding_id)
    """)
    _sql("""
    CREATE INDEX IF NOT EXISTS idx_project_vector_routes_tenant_mode
        ON project_vector_routes (tenant_id, routing_mode)
    """)

    _sql("""
    CREATE TABLE IF NOT EXISTS project_vector_route_events (
        event_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL DEFAULT 'vision-default',
        from_binding_id TEXT NULL,
        to_binding_id TEXT NULL,
        routing_mode TEXT NOT NULL
            CHECK (routing_mode IN ('managed_auto', 'pinned')),
        actor TEXT NULL,
        reason TEXT NULL,
        revision BIGINT NOT NULL CHECK (revision >= 1),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT fk_project_vector_route_events_project
            FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
        CONSTRAINT fk_project_vector_route_events_from_binding
            FOREIGN KEY (from_binding_id)
            REFERENCES snapshot_vector_bindings(binding_id) ON DELETE RESTRICT,
        CONSTRAINT fk_project_vector_route_events_to_binding
            FOREIGN KEY (to_binding_id)
            REFERENCES snapshot_vector_bindings(binding_id) ON DELETE RESTRICT,
        CONSTRAINT uq_project_vector_route_events_revision
            UNIQUE (project_id, revision)
    )
    """)
    _sql("""
    CREATE INDEX IF NOT EXISTS idx_project_vector_route_events_project
        ON project_vector_route_events (project_id, revision DESC)
    """)

    # Deterministic migration from the P2-F/P2-G managed retrieval authority.
    # Do not guess when active_generation_id cannot be proven through a verified
    # managed SnapshotVectorBinding and a ready VectorIndex.
    _sql("""
    INSERT INTO project_vector_routes (
        project_id, tenant_id, active_binding_id, routing_mode, revision,
        selected_by, selected_at, reason
    )
    SELECT p.project_id,
           svb.tenant_id,
           svb.binding_id,
           'managed_auto',
           1,
           'p2i_migration',
           NOW(),
           'Backfilled from verified projects.active_generation_id provenance'
    FROM projects AS p
    JOIN index_generations AS ig
      ON ig.generation_id = p.active_generation_id
     AND ig.project_id = p.project_id
    JOIN snapshot_vector_bindings AS svb
      ON svb.generation_id = ig.generation_id
     AND svb.snapshot_id = ig.snapshot_id
     AND svb.vector_index_id = ig.vector_index_id
     AND svb.binding_source = 'managed_generation'
     AND svb.verification_state = 'verified'
    JOIN project_snapshots AS ps
      ON ps.snapshot_id = svb.snapshot_id
     AND ps.project_id = p.project_id
     AND ps.status = 'completed'
     AND ps.tenant_id = svb.tenant_id
    JOIN vector_indexes AS vi
      ON vi.vector_index_id = svb.vector_index_id
     AND vi.ownership_mode = 'vision_managed'
     AND vi.status = 'ready'
     AND vi.tenant_id = svb.tenant_id
     AND vi.selector ->> 'project_id' = p.project_id
     AND vi.selector ->> 'generation_id' = ig.generation_id
    JOIN vector_targets AS vt
      ON vt.vector_target_id = vi.vector_target_id
     AND vt.tenant_id = svb.tenant_id
     AND vt.status <> 'disabled'
    JOIN embedding_profiles AS ep
      ON ep.embedding_profile_id = vi.embedding_profile_id
     AND ep.tenant_id = svb.tenant_id
     AND ep.status <> 'disabled'
    WHERE p.active_generation_id IS NOT NULL
      AND ig.status = 'ready'
      AND NOT EXISTS (
          SELECT 1 FROM project_vector_routes AS existing
          WHERE existing.project_id = p.project_id
      )
    """)

    _sql("""
    INSERT INTO project_vector_route_events (
        event_id, project_id, tenant_id, from_binding_id, to_binding_id,
        routing_mode, actor, reason, revision, created_at
    )
    SELECT 'pvrevt_' || substr(md5(pvr.project_id || '|' || pvr.active_binding_id || '|p2i-backfill'), 1, 32),
           pvr.project_id,
           pvr.tenant_id,
           NULL,
           pvr.active_binding_id,
           pvr.routing_mode,
           pvr.selected_by,
           pvr.reason,
           pvr.revision,
           COALESCE(pvr.selected_at, NOW())
    FROM project_vector_routes AS pvr
    WHERE pvr.active_binding_id IS NOT NULL
      AND pvr.revision = 1
      AND pvr.selected_by = 'p2i_migration'
      AND NOT EXISTS (
          SELECT 1 FROM project_vector_route_events AS e
          WHERE e.project_id = pvr.project_id AND e.revision = pvr.revision
      )
    """)


def downgrade() -> None:
    # Restore the representable managed route for the P2-H runtime. External routes
    # have no IndexGeneration equivalent and therefore intentionally downgrade to NULL.
    _sql("""
    UPDATE projects AS p
    SET active_generation_id = svb.generation_id
    FROM project_vector_routes AS pvr
    LEFT JOIN snapshot_vector_bindings AS svb
      ON svb.binding_id = pvr.active_binding_id
     AND svb.generation_id IS NOT NULL
    WHERE p.project_id = pvr.project_id
    """)
    _sql("""
    UPDATE index_generations
    SET status = 'retired'
    WHERE status = 'ready'
    """)
    _sql("""
    UPDATE index_generations AS ig
    SET status = 'active',
        activated_at = COALESCE(ig.activated_at, NOW())
    FROM projects AS p
    WHERE p.active_generation_id = ig.generation_id
    """)

    _sql("DROP TABLE IF EXISTS project_vector_route_events")
    _sql("DROP TABLE IF EXISTS project_vector_routes")
    _sql("ALTER TABLE index_generations DROP COLUMN IF EXISTS ready_at")
