"""phase1 backup duration column backfill

Revision ID: 20260628_0003
Revises: 20260628_0002
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = "20260628_0003"
down_revision = "20260628_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("cluster_health_snapshot")}
    if "backup_duration_minutes" not in columns:
        op.add_column("cluster_health_snapshot", sa.Column("backup_duration_minutes", sa.Float()))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("cluster_health_snapshot")}
    if "backup_duration_minutes" in columns:
        op.drop_column("cluster_health_snapshot", "backup_duration_minutes")
