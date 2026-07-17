"""phase1 snapshot feature columns

Revision ID: 20260628_0002
Revises: 20260628_0001
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = "20260628_0002"
down_revision = "20260628_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cluster_health_snapshot", sa.Column("backup_duration_minutes", sa.Float()))
    op.add_column("cluster_health_snapshot", sa.Column("pg_stat_statements_slow_query_count", sa.Integer()))
    op.add_column("cluster_health_snapshot", sa.Column("temp_files_mb", sa.Float()))


def downgrade() -> None:
    op.drop_column("cluster_health_snapshot", "temp_files_mb")
    op.drop_column("cluster_health_snapshot", "pg_stat_statements_slow_query_count")
    op.drop_column("cluster_health_snapshot", "backup_duration_minutes")
