"""Promote embedding execution/vector-space settings into EmbeddingProfile registry.

Revision ID: p2d_0003_embedding_profiles
Revises: p2c_0002_vector_targets
"""
from __future__ import annotations

from alembic import op

revision = "p2d_0003_embedding_profiles"
down_revision = "p2c_0002_vector_targets"
branch_labels = None
depends_on = None


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    _sql("""
    CREATE TABLE IF NOT EXISTS embedding_profiles (
        embedding_profile_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL DEFAULT 'vision-default',
        name TEXT NOT NULL,
        deployment TEXT NOT NULL CHECK (deployment IN ('api', 'local')),
        provider TEXT NOT NULL CHECK (provider IN ('ollama', 'openai', 'nvidia')),
        base_url TEXT NOT NULL,
        model TEXT NOT NULL,
        model_id TEXT NOT NULL,
        dimension INTEGER NOT NULL CHECK (dimension > 0),
        batch_size INTEGER NOT NULL CHECK (batch_size BETWEEN 1 AND 256),
        credential_ref TEXT,
        status TEXT NOT NULL DEFAULT 'configured'
            CHECK (status IN ('configured', 'healthy', 'unavailable', 'disabled')),
        error TEXT,
        latency_ms INTEGER,
        last_checked_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (tenant_id, deployment, provider, base_url, model, model_id, dimension)
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_embedding_profiles_tenant_status ON embedding_profiles (tenant_id, status)")
    _sql("""
    ALTER TABLE runtime_service_settings
        ADD COLUMN IF NOT EXISTS embedding_profile_id TEXT
    """)
    _sql("ALTER TABLE runtime_service_settings ALTER COLUMN embedding_model DROP NOT NULL")

    # Backfill the complete pre-P2-D singleton embedding contract. Identity intentionally
    # excludes batch_size because tuning batch execution does not change the vector space.
    _sql("""
    INSERT INTO embedding_profiles (
        embedding_profile_id, tenant_id, name, deployment, provider, base_url,
        model, model_id, dimension, batch_size, status, created_at, updated_at
    )
    SELECT
        'eprof_' || substr(md5(
            'vision-default' || '|' || lower(embedding_deployment) || '|' ||
            lower(embedding_provider) || '|' || rtrim(embedding_base_url, '/') || '|' ||
            embedding_model || '|' || embedding_model_id || '|' || embedding_dimension::text
        ), 1, 24),
        'vision-default',
        COALESCE(NULLIF(embedding_model_id, ''), embedding_model),
        lower(embedding_deployment),
        lower(embedding_provider),
        rtrim(embedding_base_url, '/'),
        embedding_model,
        embedding_model_id,
        embedding_dimension,
        embedding_batch_size,
        'configured', NOW(), NOW()
    FROM runtime_service_settings
    WHERE singleton = TRUE
      AND lower(COALESCE(embedding_deployment, '')) IN ('api', 'local')
      AND lower(COALESCE(embedding_provider, '')) IN ('ollama', 'openai', 'nvidia')
      AND COALESCE(embedding_base_url, '') <> ''
      AND COALESCE(embedding_model, '') <> ''
      AND COALESCE(embedding_model_id, '') <> ''
      AND embedding_dimension > 0
      AND embedding_batch_size BETWEEN 1 AND 256
    ON CONFLICT (tenant_id, deployment, provider, base_url, model, model_id, dimension)
    DO UPDATE SET batch_size = EXCLUDED.batch_size, updated_at = NOW()
    """)
    _sql("""
    UPDATE runtime_service_settings AS runtime
    SET embedding_profile_id = profile.embedding_profile_id
    FROM embedding_profiles AS profile
    WHERE runtime.singleton = TRUE
      AND profile.tenant_id = 'vision-default'
      AND profile.deployment = lower(runtime.embedding_deployment)
      AND profile.provider = lower(runtime.embedding_provider)
      AND profile.base_url = rtrim(runtime.embedding_base_url, '/')
      AND profile.model = runtime.embedding_model
      AND profile.model_id = runtime.embedding_model_id
      AND profile.dimension = runtime.embedding_dimension
      AND runtime.embedding_profile_id IS NULL
    """)
    _sql("""
    ALTER TABLE runtime_service_settings
        DROP CONSTRAINT IF EXISTS fk_runtime_service_embedding_profile
    """)
    _sql("""
    ALTER TABLE runtime_service_settings
        ADD CONSTRAINT fk_runtime_service_embedding_profile
        FOREIGN KEY (embedding_profile_id)
        REFERENCES embedding_profiles(embedding_profile_id)
        ON DELETE RESTRICT
    """)


def downgrade() -> None:
    _sql("ALTER TABLE runtime_service_settings DROP CONSTRAINT IF EXISTS fk_runtime_service_embedding_profile")
    _sql("ALTER TABLE runtime_service_settings DROP COLUMN IF EXISTS embedding_profile_id")
    _sql("DROP TABLE IF EXISTS embedding_profiles")
