"""Add external VectorIndex discovery/verification persistence.

Revision ID: p2h_0007_external_vector_indexes
Revises: p2g_0006_snapshot_vector_bindings
"""
from __future__ import annotations

from alembic import op

revision = "p2h_0007_external_vector_indexes"
down_revision = "p2g_0006_snapshot_vector_bindings"
branch_labels = None
depends_on = None


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    _sql("""
    ALTER TABLE snapshot_vector_bindings
        ADD COLUMN IF NOT EXISTS verification_evidence JSONB NOT NULL DEFAULT '{}'::jsonb
    """)

    _sql("""
    CREATE TABLE IF NOT EXISTS external_vector_index_verifications (
        vector_index_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL DEFAULT 'vision-default',
        verification_state TEXT NOT NULL DEFAULT 'unverified'
            CHECK (verification_state IN ('unverified', 'compatible', 'incompatible', 'unavailable')),
        verification_method TEXT NOT NULL DEFAULT 'qdrant_probe'
            CHECK (verification_method IN ('qdrant_probe')),
        embedding_profile_attested BOOLEAN NOT NULL DEFAULT FALSE,
        expected_dimension INTEGER NOT NULL CHECK (expected_dimension > 0),
        observed_dimension INTEGER NULL CHECK (observed_dimension IS NULL OR observed_dimension > 0),
        expected_distance_metric TEXT NOT NULL
            CHECK (expected_distance_metric IN ('cosine', 'dot', 'euclid', 'manhattan')),
        observed_distance_metric TEXT NULL,
        observed_vector_type TEXT NULL,
        observed_points_count BIGINT NULL CHECK (observed_points_count IS NULL OR observed_points_count >= 0),
        selector_points_count BIGINT NULL CHECK (selector_points_count IS NULL OR selector_points_count >= 0),
        sample_size INTEGER NOT NULL DEFAULT 0 CHECK (sample_size >= 0),
        sample_payload_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
        last_verified_at TIMESTAMPTZ NULL,
        error TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT fk_external_vector_index_verifications_index
            FOREIGN KEY (vector_index_id) REFERENCES vector_indexes(vector_index_id) ON DELETE CASCADE
    )
    """)
    _sql("""
    CREATE INDEX IF NOT EXISTS idx_external_vector_index_verifications_state
        ON external_vector_index_verifications (tenant_id, verification_state)
    """)

    # Existing rows could only have been inserted out-of-band before P2-H because
    # P2-E~G exposed no attach mutation API. Preserve them as explicitly unverified;
    # never infer compatibility from mere registry presence.
    _sql("""
    INSERT INTO external_vector_index_verifications (
        vector_index_id, tenant_id, verification_state, verification_method,
        embedding_profile_attested, expected_dimension,
        expected_distance_metric, sample_size, sample_payload_keys,
        last_verified_at, error, created_at, updated_at
    )
    SELECT
        vi.vector_index_id,
        vi.tenant_id,
        'unverified',
        'qdrant_probe',
        FALSE,
        ep.dimension,
        vi.distance_metric,
        0,
        '[]'::jsonb,
        NULL,
        'P2-H verification required for pre-existing external_attached registry row',
        vi.created_at,
        NOW()
    FROM vector_indexes AS vi
    JOIN embedding_profiles AS ep
      ON ep.embedding_profile_id = vi.embedding_profile_id
    WHERE vi.ownership_mode = 'external_attached'
    ON CONFLICT (vector_index_id) DO NOTHING
    """)


def downgrade() -> None:
    _sql("DROP INDEX IF EXISTS idx_external_vector_index_verifications_state")
    _sql("DROP TABLE IF EXISTS external_vector_index_verifications")
    _sql("""
    ALTER TABLE snapshot_vector_bindings
        DROP COLUMN IF EXISTS verification_evidence
    """)
