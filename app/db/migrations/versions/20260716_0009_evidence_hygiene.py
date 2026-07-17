"""Evidence-store deduplication and retrieval indexes.

Revision ID: 20260716_0009
Revises: 20260712_0008
"""
from __future__ import annotations

from alembic import op

revision = "20260716_0009"
down_revision = "20260712_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $migration$ BEGIN IF to_regclass('log_embeddings') IS NOT NULL THEN "
        "CREATE UNIQUE INDEX IF NOT EXISTS log_embeddings_cluster_content_hash_uidx "
        "ON log_embeddings (cluster_id, (metadata->>'content_hash')) "
        "WHERE metadata->>'content_hash' IS NOT NULL; END IF; END $migration$"
    )
    op.execute(
        "DO $migration$ BEGIN IF to_regclass('log_index_state') IS NOT NULL THEN "
        "CREATE INDEX IF NOT EXISTS log_index_state_freshness_idx "
        "ON log_index_state (last_indexed_at DESC); END IF; END $migration$"
    )
    op.execute(
        "DO $migration$ BEGIN IF to_regclass('ai_assistant_sessions') IS NOT NULL THEN "
        "CREATE INDEX IF NOT EXISTS ai_assistant_sessions_created_idx "
        "ON ai_assistant_sessions (created_at DESC); END IF; END $migration$"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ai_assistant_sessions_created_idx")
    op.execute("DROP INDEX IF EXISTS log_index_state_freshness_idx")
    op.execute("DROP INDEX IF EXISTS log_embeddings_cluster_content_hash_uidx")
