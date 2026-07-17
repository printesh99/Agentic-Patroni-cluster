"""ai knowledge base

Revision ID: 20260628_0004
Revises: 20260628_0003
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = "20260628_0004"
down_revision = "20260628_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_knowledge_base",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doc_type", sa.String(length=64)),
        sa.Column("region", sa.String(length=64)),
        sa.Column("cluster_name", sa.String(length=255)),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON()),
        sa.Column("source_file", sa.String(length=512)),
        sa.Column("runbook_id", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ai_knowledge_base_runbook_idx", "ai_knowledge_base", ["runbook_id"])


def downgrade() -> None:
    op.drop_index("ai_knowledge_base_runbook_idx", table_name="ai_knowledge_base")
    op.drop_table("ai_knowledge_base")
