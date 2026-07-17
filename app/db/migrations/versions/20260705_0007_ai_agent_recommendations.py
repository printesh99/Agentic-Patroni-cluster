"""Agentic AI recommendation lifecycle tables.

Revision ID: 20260705_0007
Revises: 20260705_0006
Create Date: 2026-07-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260705_0007"
down_revision = "20260705_0006"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    if not _has_table("ai_agent_run"):
        op.create_table(
            "ai_agent_run",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("agent_name", sa.String(length=128), nullable=False, server_default="ai-dba-agent"),
            sa.Column("trigger_type", sa.String(length=32), nullable=False, server_default="MANUAL"),
            sa.Column("triggered_by", sa.String(length=255)),
            sa.Column("cluster_name", sa.String(length=255)),
            sa.Column("database_name", sa.String(length=255)),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="RUNNING"),
            sa.Column("summary", sa.Text()),
            sa.Column("error_message", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_ai_agent_run_started_at", "ai_agent_run", ["started_at"])
        op.create_index("ix_ai_agent_run_status", "ai_agent_run", ["status"])

    if not _has_table("ai_recommendation"):
        op.create_table(
            "ai_recommendation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("ai_agent_run.id")),
            sa.Column("severity", sa.String(length=32), nullable=False, server_default="INFO"),
            sa.Column("category", sa.String(length=64), nullable=False, server_default="OTHER"),
            sa.Column("cluster_name", sa.String(length=255)),
            sa.Column("region_name", sa.String(length=64)),
            sa.Column("dc_name", sa.String(length=64)),
            sa.Column("database_name", sa.String(length=255)),
            sa.Column("object_name", sa.String(length=512)),
            sa.Column("finding", sa.Text()),
            sa.Column("evidence", sa.JSON()),
            sa.Column("root_cause", sa.Text()),
            sa.Column("recommendation", sa.Text()),
            sa.Column("recommended_sql", sa.Text()),
            sa.Column("rollback_sql", sa.Text()),
            sa.Column("risk_level", sa.String(length=32), nullable=False, server_default="LOW"),
            sa.Column("confidence_score", sa.Numeric(5, 2)),
            sa.Column("approval_status", sa.String(length=32), nullable=False, server_default="PENDING"),
            sa.Column("approved_by", sa.String(length=255)),
            sa.Column("approved_at", sa.DateTime(timezone=True)),
            sa.Column("execution_status", sa.String(length=64)),
            sa.Column("execution_output", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_ai_recommendation_run_id", "ai_recommendation", ["run_id"])
        op.create_index("ix_ai_recommendation_status", "ai_recommendation", ["approval_status"])
        op.create_index("ix_ai_recommendation_cluster_category", "ai_recommendation", ["cluster_name", "category"])
        op.create_index("ix_ai_recommendation_created_at", "ai_recommendation", ["created_at"])

    if _has_table("ai_action_audit"):
        _add_column_if_missing("ai_action_audit", sa.Column("recommendation_id", sa.Integer(), sa.ForeignKey("ai_recommendation.id")))
        _add_column_if_missing("ai_action_audit", sa.Column("execution_started_at", sa.DateTime(timezone=True)))
        _add_column_if_missing("ai_action_audit", sa.Column("execution_finished_at", sa.DateTime(timezone=True)))
        _add_column_if_missing("ai_action_audit", sa.Column("execution_output", sa.Text()))
        _add_column_if_missing("ai_action_audit", sa.Column("error_message", sa.Text()))


def downgrade() -> None:
    if _has_table("ai_action_audit"):
        for column in ("error_message", "execution_output", "execution_finished_at", "execution_started_at", "recommendation_id"):
            if _has_column("ai_action_audit", column):
                op.drop_column("ai_action_audit", column)
    if _has_table("ai_recommendation"):
        op.drop_index("ix_ai_recommendation_created_at", table_name="ai_recommendation")
        op.drop_index("ix_ai_recommendation_cluster_category", table_name="ai_recommendation")
        op.drop_index("ix_ai_recommendation_status", table_name="ai_recommendation")
        op.drop_index("ix_ai_recommendation_run_id", table_name="ai_recommendation")
        op.drop_table("ai_recommendation")
    if _has_table("ai_agent_run"):
        op.drop_index("ix_ai_agent_run_status", table_name="ai_agent_run")
        op.drop_index("ix_ai_agent_run_started_at", table_name="ai_agent_run")
        op.drop_table("ai_agent_run")
