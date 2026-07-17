"""ai_knowledge_base embedding column (pgvector, Postgres only)

Adds a 384-dim ``embedding`` column for semantic RAG. Postgres-only: the column
and the ``vector`` extension are skipped on SQLite so the dev schema stays
portable and the keyword retriever remains the fallback there.

Revision ID: 20260628_0005
Revises: 20260628_0004
Create Date: 2026-06-28
"""
from alembic import op

revision = "20260628_0005"
down_revision = "20260628_0004"
branch_labels = None
depends_on = None

EMBED_DIM = 384


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(f"ALTER TABLE ai_knowledge_base ADD COLUMN IF NOT EXISTS embedding vector({EMBED_DIM})")
    # Cosine-distance ANN index; ivfflat is fine for this small corpus.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ai_knowledge_base_embedding_idx "
        "ON ai_knowledge_base USING ivfflat (embedding vector_cosine_ops) WITH (lists = 1)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ai_knowledge_base_embedding_idx")
    op.execute("ALTER TABLE ai_knowledge_base DROP COLUMN IF EXISTS embedding")
