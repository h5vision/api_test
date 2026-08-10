"""Bind Vision-managed IndexGeneration rows to persistent VectorIndex provenance.

Revision ID: p2f_0005_generation_vector_index
Revises: p2e_0004_vector_indexes_chat_processing
"""
from __future__ import annotations

from alembic import op

revision = "p2f_0005_generation_vector_index"
down_revision = "p2e_0004_vector_indexes_chat_processing"
branch_labels = None
depends_on = None


def _sql(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    _sql("""
    ALTER TABLE index_generations
        ADD COLUMN IF NOT EXISTS vector_index_id TEXT
    """)
    _sql("""
    ALTER TABLE index_generations
        DROP CONSTRAINT IF EXISTS fk_index_generations_vector_index
    """)
    _sql("""
    ALTER TABLE index_generations
        ADD CONSTRAINT fk_index_generations_vector_index
        FOREIGN KEY (vector_index_id)
        REFERENCES vector_indexes(vector_index_id)
        ON DELETE RESTRICT
    """)
    _sql("""
    CREATE INDEX IF NOT EXISTS idx_index_generations_vector_index
        ON index_generations (vector_index_id)
    """)

    # P2-E legacy compatibility rows are status='unavailable' because their
    # historical target provenance cannot be proven. Do not bind them here.
    # Only rows produced by the P2-E lifecycle (building/ready/retired) are
    # eligible for automatic provenance backfill.
    _sql("""
    UPDATE index_generations AS ig
    SET vector_index_id = (
        SELECT vi.vector_index_id
        FROM vector_indexes AS vi
        WHERE vi.ownership_mode = 'vision_managed'
          AND vi.status IN ('building', 'ready', 'retired')
          AND vi.selector = jsonb_build_object(
              'project_id', ig.project_id,
              'generation_id', ig.generation_id
          )
        ORDER BY vi.updated_at DESC, vi.vector_index_id
        LIMIT 1
    )
    WHERE ig.vector_index_id IS NULL
      AND EXISTS (
          SELECT 1
          FROM vector_indexes AS vi
          WHERE vi.ownership_mode = 'vision_managed'
            AND vi.status IN ('building', 'ready', 'retired')
            AND vi.selector = jsonb_build_object(
                'project_id', ig.project_id,
                'generation_id', ig.generation_id
            )
      )
    """)


def downgrade() -> None:
    _sql("DROP INDEX IF EXISTS idx_index_generations_vector_index")
    _sql("""
    ALTER TABLE index_generations
        DROP CONSTRAINT IF EXISTS fk_index_generations_vector_index
    """)
    _sql("""
    ALTER TABLE index_generations
        DROP COLUMN IF EXISTS vector_index_id
    """)
