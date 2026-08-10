"""Add explicit Snapshot <-> VectorIndex provenance bindings.

Revision ID: p2g_0006_snapshot_vector_bindings
Revises: p2f_0005_generation_vector_index
"""
from __future__ import annotations

from alembic import op

revision = "p2g_0006_snapshot_vector_bindings"
down_revision = "p2f_0005_generation_vector_index"
branch_labels = None
depends_on = None


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    _sql("""
    CREATE TABLE IF NOT EXISTS snapshot_vector_bindings (
        binding_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL DEFAULT 'vision-default',
        snapshot_id TEXT NOT NULL,
        vector_index_id TEXT NOT NULL,
        generation_id TEXT NULL,
        binding_source TEXT NOT NULL
            CHECK (binding_source IN ('managed_generation', 'external_verification')),
        verification_state TEXT NOT NULL DEFAULT 'pending'
            CHECK (verification_state IN ('pending', 'verified', 'failed', 'revoked')),
        verification_method TEXT NOT NULL
            CHECK (verification_method IN ('managed_build', 'external_probe', 'manual')),
        snapshot_fingerprint TEXT NOT NULL,
        vector_index_identity_key TEXT NOT NULL,
        verified_at TIMESTAMPTZ NULL,
        error TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT fk_snapshot_vector_bindings_snapshot
            FOREIGN KEY (snapshot_id) REFERENCES project_snapshots(snapshot_id) ON DELETE RESTRICT,
        CONSTRAINT fk_snapshot_vector_bindings_index
            FOREIGN KEY (vector_index_id) REFERENCES vector_indexes(vector_index_id) ON DELETE RESTRICT,
        CONSTRAINT fk_snapshot_vector_bindings_generation
            FOREIGN KEY (generation_id) REFERENCES index_generations(generation_id) ON DELETE RESTRICT,
        CONSTRAINT uq_snapshot_vector_bindings_pair UNIQUE (snapshot_id, vector_index_id),
        CONSTRAINT ck_snapshot_vector_bindings_managed_generation
            CHECK (binding_source <> 'managed_generation' OR generation_id IS NOT NULL)
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_snapshot_vector_bindings_snapshot ON snapshot_vector_bindings (snapshot_id)")
    _sql("CREATE INDEX IF NOT EXISTS idx_snapshot_vector_bindings_vector_index ON snapshot_vector_bindings (vector_index_id)")
    _sql("CREATE INDEX IF NOT EXISTS idx_snapshot_vector_bindings_state ON snapshot_vector_bindings (tenant_id, verification_state)")
    _sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_vector_bindings_generation ON snapshot_vector_bindings (generation_id) WHERE generation_id IS NOT NULL")

    # Backfill only generations whose P2-F VectorIndex FK already proves the
    # managed relation and whose concrete selector agrees with the generation.
    # P2-E legacy compatibility candidates have no P2-F FK and remain excluded.
    _sql("""
    INSERT INTO snapshot_vector_bindings (
        binding_id, tenant_id, snapshot_id, vector_index_id, generation_id,
        binding_source, verification_state, verification_method,
        snapshot_fingerprint, vector_index_identity_key,
        verified_at, error, created_at, updated_at
    )
    SELECT
        'svb_' || substr(md5(
            COALESCE(ps.tenant_id, 'vision-default') || '|' ||
            ps.snapshot_id || '|' || vi.vector_index_id
        ), 1, 24),
        COALESCE(ps.tenant_id, 'vision-default'),
        ps.snapshot_id,
        vi.vector_index_id,
        ig.generation_id,
        'managed_generation',
        CASE
            WHEN ig.status IN ('active', 'retired')
             AND ps.status = 'completed'
             AND vi.status IN ('ready', 'retired') THEN 'verified'
            WHEN ig.status = 'failed' OR ps.status = 'failed' OR vi.status = 'unavailable' THEN 'failed'
            ELSE 'pending'
        END,
        'managed_build',
        COALESCE(NULLIF(ps.fingerprint, ''), ps.manifest_sha256),
        vi.identity_key,
        CASE
            WHEN ig.status IN ('active', 'retired')
             AND ps.status = 'completed'
             AND vi.status IN ('ready', 'retired')
            THEN COALESCE(ig.activated_at, ps.completed_at, NOW())
            ELSE NULL
        END,
        CASE WHEN ig.status = 'failed' THEN ig.error ELSE NULL END,
        LEAST(ps.created_at, ig.created_at, vi.created_at),
        GREATEST(
            COALESCE(ps.completed_at, ps.created_at),
            COALESCE(ig.activated_at, ig.created_at),
            vi.updated_at
        )
    FROM index_generations AS ig
    JOIN project_snapshots AS ps ON ps.snapshot_id = ig.snapshot_id
    JOIN vector_indexes AS vi ON vi.vector_index_id = ig.vector_index_id
    WHERE ig.vector_index_id IS NOT NULL
      AND vi.ownership_mode = 'vision_managed'
      AND vi.selector = jsonb_build_object(
          'project_id', ig.project_id,
          'generation_id', ig.generation_id
      )
      AND COALESCE(NULLIF(ps.fingerprint, ''), ps.manifest_sha256) IS NOT NULL
    ON CONFLICT (snapshot_id, vector_index_id) DO NOTHING
    """)


def downgrade() -> None:
    _sql("DROP INDEX IF EXISTS uq_snapshot_vector_bindings_generation")
    _sql("DROP INDEX IF EXISTS idx_snapshot_vector_bindings_state")
    _sql("DROP INDEX IF EXISTS idx_snapshot_vector_bindings_vector_index")
    _sql("DROP INDEX IF EXISTS idx_snapshot_vector_bindings_snapshot")
    _sql("DROP TABLE IF EXISTS snapshot_vector_bindings")
