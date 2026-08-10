"""Promote physical vector endpoints into the canonical VectorTarget registry.

Revision ID: p2c_0002_vector_targets
Revises: p2a_0001_baseline
"""
from __future__ import annotations

from alembic import op

revision = "p2c_0002_vector_targets"
down_revision = "p2a_0001_baseline"
branch_labels = None
depends_on = None


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    _sql("""
    CREATE TABLE IF NOT EXISTS vector_targets (
        vector_target_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL DEFAULT 'vision-default',
        name TEXT NOT NULL,
        engine TEXT NOT NULL CHECK (engine IN ('qdrant')),
        endpoint TEXT NOT NULL,
        credential_ref TEXT,
        deployment_type TEXT NOT NULL,
        capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
        status TEXT NOT NULL DEFAULT 'configured'
            CHECK (status IN ('configured', 'healthy', 'unavailable', 'disabled')),
        error TEXT,
        latency_ms INTEGER,
        last_checked_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (tenant_id, engine, endpoint)
    )
    """)
    _sql("CREATE INDEX IF NOT EXISTS idx_vector_targets_tenant_status ON vector_targets (tenant_id, status)")
    _sql("""
    ALTER TABLE runtime_service_settings
        ADD COLUMN IF NOT EXISTS vector_target_id TEXT
    """)
    _sql("ALTER TABLE runtime_service_settings ALTER COLUMN vector_host DROP NOT NULL")
    _sql("ALTER TABLE runtime_service_settings ALTER COLUMN vector_port DROP NOT NULL")

    # Data-preserving backfill from the pre-P2-C singleton routing fields. These
    # columns remain in place for historical/rollback visibility but runtime code
    # stops treating them as authoritative after this revision.
    _sql("""
    INSERT INTO vector_targets (
        vector_target_id, tenant_id, name, engine, endpoint,
        deployment_type, capabilities, status, created_at, updated_at
    )
    SELECT
        'vtarget_' || substr(md5('vision-default|qdrant|http://' || lower(vector_host) || ':' || vector_port::text), 1, 24),
        'vision-default',
        'Migrated Qdrant',
        'qdrant',
        'http://' || vector_host || ':' || vector_port::text,
        CASE
            WHEN lower(vector_host) IN ('127.0.0.1', 'localhost', 'qdrant', 'host.docker.internal') THEN 'local'
            WHEN lower(vector_host) LIKE '%%.svc' OR lower(vector_host) LIKE '%%.svc.%%' THEN 'cluster'
            ELSE 'remote_server'
        END,
        '{"dense_vectors":true,"payload_filter":true,"exact_count":true,"provision_index":true,"named_vectors":true,"hybrid_query":true,"rrf":true}'::jsonb,
        'configured', NOW(), NOW()
    FROM runtime_service_settings
    WHERE singleton = TRUE
      AND COALESCE(vector_host, '') <> ''
      AND vector_port BETWEEN 1 AND 65535
    ON CONFLICT (tenant_id, engine, endpoint) DO NOTHING
    """)
    _sql("""
    UPDATE runtime_service_settings AS runtime
    SET vector_target_id = target.vector_target_id
    FROM vector_targets AS target
    WHERE runtime.singleton = TRUE
      AND target.tenant_id = 'vision-default'
      AND target.engine = 'qdrant'
      AND target.endpoint = 'http://' || runtime.vector_host || ':' || runtime.vector_port::text
      AND runtime.vector_target_id IS NULL
    """)
    _sql("""
    ALTER TABLE runtime_service_settings
        DROP CONSTRAINT IF EXISTS fk_runtime_service_vector_target
    """)
    _sql("""
    ALTER TABLE runtime_service_settings
        ADD CONSTRAINT fk_runtime_service_vector_target
        FOREIGN KEY (vector_target_id)
        REFERENCES vector_targets(vector_target_id)
        ON DELETE RESTRICT
    """)


def downgrade() -> None:
    _sql("ALTER TABLE runtime_service_settings DROP CONSTRAINT IF EXISTS fk_runtime_service_vector_target")
    _sql("ALTER TABLE runtime_service_settings DROP COLUMN IF EXISTS vector_target_id")
    _sql("DROP TABLE IF EXISTS vector_targets")
