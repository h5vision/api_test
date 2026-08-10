"""Add provider-owned chat processing policy and persistent VectorIndex registry.

Revision ID: p2e_0004_vector_indexes_chat_processing
Revises: p2d_0003_embedding_profiles
"""
from __future__ import annotations

from alembic import op

revision = "p2e_0004_vector_indexes_chat_processing"
down_revision = "p2d_0003_embedding_profiles"
branch_labels = None
depends_on = None


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    _sql("""
    ALTER TABLE ai_provider_configs
        ADD COLUMN IF NOT EXISTS chat_processing_mode TEXT NOT NULL DEFAULT 'vision_managed'
    """)
    _sql("""
    ALTER TABLE ai_provider_configs
        DROP CONSTRAINT IF EXISTS ck_ai_provider_chat_processing_mode
    """)
    _sql("""
    ALTER TABLE ai_provider_configs
        ADD CONSTRAINT ck_ai_provider_chat_processing_mode
        CHECK (chat_processing_mode IN ('vision_managed', 'provider_managed'))
    """)

    _sql("""
    CREATE TABLE IF NOT EXISTS vector_indexes (
        vector_index_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL DEFAULT 'vision-default',
        name TEXT NOT NULL,
        vector_target_id TEXT NOT NULL,
        embedding_profile_id TEXT NOT NULL,
        collection TEXT NOT NULL,
        selector JSONB NOT NULL DEFAULT '{}'::jsonb,
        index_version TEXT NOT NULL,
        distance_metric TEXT NOT NULL DEFAULT 'cosine'
            CHECK (distance_metric IN ('cosine', 'dot', 'euclid', 'manhattan')),
        ownership_mode TEXT NOT NULL DEFAULT 'vision_managed'
            CHECK (ownership_mode IN ('vision_managed', 'external_attached')),
        query_strategy TEXT NOT NULL DEFAULT 'qdrant-query-api',
        status TEXT NOT NULL DEFAULT 'building'
            CHECK (status IN ('building', 'ready', 'retired', 'unavailable', 'disabled')),
        identity_key TEXT NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT fk_vector_indexes_target
            FOREIGN KEY (vector_target_id)
            REFERENCES vector_targets(vector_target_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_vector_indexes_embedding_profile
            FOREIGN KEY (embedding_profile_id)
            REFERENCES embedding_profiles(embedding_profile_id)
            ON DELETE RESTRICT
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_vector_indexes_tenant_status ON vector_indexes (tenant_id, status)")
    _sql("CREATE INDEX IF NOT EXISTS idx_vector_indexes_target ON vector_indexes (vector_target_id)")
    _sql("CREATE INDEX IF NOT EXISTS idx_vector_indexes_embedding ON vector_indexes (embedding_profile_id)")
    _sql("CREATE INDEX IF NOT EXISTS idx_vector_indexes_selector ON vector_indexes USING GIN (selector)")

    # Legacy generations did not persist target/profile IDs. Backfill only when the
    # administrator singleton currently points to both registries; this is the only
    # authoritative information available for those pre-P2-E generations.
    _sql("""
    INSERT INTO vector_indexes (
        vector_index_id, tenant_id, name, vector_target_id, embedding_profile_id,
        collection, selector, index_version, distance_metric, ownership_mode,
        query_strategy, status, identity_key, created_at, updated_at
    )
    SELECT
        'vidx_' || substr(md5(
            COALESCE(vt.tenant_id, 'vision-default') || '|' ||
            runtime.vector_target_id || '|' || runtime.embedding_profile_id || '|' ||
            ig.collection_name || '|' ||
            jsonb_build_object('generation_id', ig.generation_id, 'project_id', ig.project_id)::text || '|' ||
            ig.index_version || '|cosine'
        ), 1, 24),
        COALESCE(vt.tenant_id, 'vision-default'),
        ig.project_id || ' · ' || ig.generation_id,
        runtime.vector_target_id,
        runtime.embedding_profile_id,
        ig.collection_name,
        jsonb_build_object('project_id', ig.project_id, 'generation_id', ig.generation_id),
        ig.index_version,
        'cosine',
        'vision_managed',
        'qdrant-query-api',
        'unavailable',
        md5(
            COALESCE(vt.tenant_id, 'vision-default') || '|' ||
            runtime.vector_target_id || '|' || runtime.embedding_profile_id || '|' ||
            ig.collection_name || '|' ||
            jsonb_build_object('generation_id', ig.generation_id, 'project_id', ig.project_id)::text || '|' ||
            ig.index_version || '|cosine'
        ),
        ig.created_at,
        COALESCE(ig.activated_at, ig.created_at)
    FROM index_generations AS ig
    CROSS JOIN runtime_service_settings AS runtime
    LEFT JOIN vector_targets AS vt
      ON vt.vector_target_id = runtime.vector_target_id
    JOIN embedding_profiles AS ep
      ON ep.embedding_profile_id = runtime.embedding_profile_id
    WHERE runtime.singleton = TRUE
      AND COALESCE(runtime.vector_target_id, '') <> ''
      AND COALESCE(runtime.embedding_profile_id, '') <> ''
      AND COALESCE(ig.collection_name, '') <> ''
      AND COALESCE(ig.index_version, '') <> ''
      AND ig.embedding_model = ep.model
      AND ig.embedding_model_id = ep.model_id
    ON CONFLICT (identity_key) DO NOTHING
    """)


def downgrade() -> None:
    _sql("DROP TABLE IF EXISTS vector_indexes")
    _sql("ALTER TABLE ai_provider_configs DROP CONSTRAINT IF EXISTS ck_ai_provider_chat_processing_mode")
    _sql("ALTER TABLE ai_provider_configs DROP COLUMN IF EXISTS chat_processing_mode")
