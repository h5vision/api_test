"""Add external RAG project catalog and identity bindings.

Revision ID: p3_0010_external_project_registry
Revises: p3_0009_chat_intake_normalization
"""
from __future__ import annotations

from alembic import op

revision = "p3_0010_external_project_registry"
down_revision = "p3_0009_chat_intake_normalization"
branch_labels = None
depends_on = None


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    _sql("""
    CREATE TABLE IF NOT EXISTS rag_targets (
        target_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        base_url TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        availability TEXT NOT NULL DEFAULT 'unknown'
            CHECK (availability IN ('online', 'stale', 'offline', 'unknown')),
        last_seen_at TIMESTAMPTZ,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    _sql("""
    CREATE TABLE IF NOT EXISTS external_project_catalog (
        target_id TEXT NOT NULL REFERENCES rag_targets(target_id) ON DELETE CASCADE,
        external_project_id TEXT NOT NULL,
        name TEXT,
        state TEXT,
        revision TEXT,
        dirty BOOLEAN,
        chunk_count BIGINT CHECK (chunk_count IS NULL OR chunk_count >= 0),
        actual_chunks BIGINT CHECK (actual_chunks IS NULL OR actual_chunks >= 0),
        indexed_at TIMESTAMPTZ,
        fingerprint JSONB NOT NULL DEFAULT '{}'::jsonb,
        availability TEXT NOT NULL DEFAULT 'unknown'
            CHECK (availability IN ('online', 'stale', 'offline', 'unknown')),
        last_seen_at TIMESTAMPTZ,
        raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (target_id, external_project_id)
    )
    """)
    _sql("""
    CREATE INDEX IF NOT EXISTS ix_external_project_catalog_revision
    ON external_project_catalog (target_id, revision)
    """)
    _sql("""
    CREATE TABLE IF NOT EXISTS project_external_bindings (
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        target_id TEXT NOT NULL,
        external_project_id TEXT NOT NULL,
        binding_method TEXT NOT NULL
            CHECK (binding_method IN (
                'manual', 'revision_exact', 'project_id_exact', 'leaf_candidate'
            )),
        binding_strength TEXT NOT NULL,
        verification_state TEXT NOT NULL
            CHECK (verification_state IN ('verified', 'candidate', 'unverified', 'conflict')),
        last_verified_at TIMESTAMPTZ,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (project_id, target_id),
        FOREIGN KEY (target_id, external_project_id)
            REFERENCES external_project_catalog(target_id, external_project_id)
            ON DELETE RESTRICT
    )
    """)
    _sql("""
    CREATE INDEX IF NOT EXISTS ix_project_external_bindings_external
    ON project_external_bindings (target_id, external_project_id)
    """)


def downgrade() -> None:
    _sql("DROP INDEX IF EXISTS ix_project_external_bindings_external")
    _sql("DROP TABLE IF EXISTS project_external_bindings")
    _sql("DROP INDEX IF EXISTS ix_external_project_catalog_revision")
    _sql("DROP TABLE IF EXISTS external_project_catalog")
    _sql("DROP TABLE IF EXISTS rag_targets")
