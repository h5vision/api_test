"""Add P3 chat-intake normalization policy.

Revision ID: p3_0009_chat_intake_normalization
Revises: p2i_0008_project_vector_routes
"""
from __future__ import annotations

from alembic import op

revision = "p3_0009_chat_intake_normalization"
down_revision = "p2i_0008_project_vector_routes"
branch_labels = None
depends_on = None


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    _sql("""
    CREATE TABLE IF NOT EXISTS chat_intake_settings (
        singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
        deep_normalization_enabled BOOLEAN NOT NULL DEFAULT TRUE,
        fallback_mode TEXT NOT NULL DEFAULT 'raw_message'
            CHECK (fallback_mode IN ('raw_message')),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    _sql("""
    INSERT INTO chat_intake_settings (
        singleton, deep_normalization_enabled, fallback_mode
    ) VALUES (TRUE, TRUE, 'raw_message')
    ON CONFLICT (singleton) DO NOTHING
    """)
    _sql("""
    ALTER TABLE frontend_clients
        ADD COLUMN IF NOT EXISTS chat_deep_normalization_mode TEXT
        NOT NULL DEFAULT 'inherit'
    """)
    _sql("""
    ALTER TABLE frontend_clients
        DROP CONSTRAINT IF EXISTS ck_frontend_clients_chat_deep_normalization_mode
    """)
    _sql("""
    ALTER TABLE frontend_clients
        ADD CONSTRAINT ck_frontend_clients_chat_deep_normalization_mode
        CHECK (chat_deep_normalization_mode IN ('inherit', 'auto', 'off'))
    """)


def downgrade() -> None:
    _sql("""
    ALTER TABLE frontend_clients
        DROP CONSTRAINT IF EXISTS ck_frontend_clients_chat_deep_normalization_mode
    """)
    _sql("""
    ALTER TABLE frontend_clients
        DROP COLUMN IF EXISTS chat_deep_normalization_mode
    """)
    _sql("DROP TABLE IF EXISTS chat_intake_settings")
