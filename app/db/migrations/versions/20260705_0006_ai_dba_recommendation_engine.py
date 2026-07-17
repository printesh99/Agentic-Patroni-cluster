"""AI DBA recommendation engine metadata.

Revision ID: 20260705_0006
Revises: 20260628_0005
Create Date: 2026-07-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260705_0006"
down_revision = "20260628_0005"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("ai_dba_model_run"):
        op.create_table(
            "ai_dba_model_run",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("cluster_id", sa.String(length=64)),
            sa.Column("cluster_name", sa.String(length=255)),
            sa.Column("run_type", sa.String(length=64), nullable=False, server_default="recommendation"),
            sa.Column("model_name", sa.String(length=128), nullable=False, server_default="rule-engine"),
            sa.Column("model_version", sa.String(length=64), nullable=False, server_default="v1"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("rows_analyzed", sa.Integer()),
            sa.Column("recommendations_created", sa.Integer()),
            sa.Column("error", sa.Text()),
            sa.Column("metadata", sa.JSON()),
        )
    if not _has_table("ai_sql_fingerprint"):
        op.create_table(
            "ai_sql_fingerprint",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("cluster_id", sa.String(length=64)),
            sa.Column("cluster_name", sa.String(length=255)),
            sa.Column("database_name", sa.String(length=255)),
            sa.Column("queryid", sa.String(length=128)),
            sa.Column("normalized_query", sa.Text()),
            sa.Column("calls", sa.Integer()),
            sa.Column("mean_exec_ms", sa.Float()),
            sa.Column("total_exec_ms", sa.Float()),
            sa.Column("rows_returned", sa.Integer()),
            sa.Column("cache_hit_pct", sa.Float()),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("extra", sa.JSON()),
        )
    if not _has_table("ai_dba_recommendations"):
        op.create_table(
            "ai_dba_recommendations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("cluster_id", sa.String(length=64)),
            sa.Column("cluster_name", sa.String(length=255)),
            sa.Column("database_name", sa.String(length=255)),
            sa.Column("schema_name", sa.String(length=255)),
            sa.Column("object_name", sa.String(length=512)),
            sa.Column("object_type", sa.String(length=64)),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("recommendation_type", sa.String(length=128), nullable=False),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("summary", sa.Text()),
            sa.Column("rationale", sa.Text()),
            sa.Column("severity", sa.String(length=32), nullable=False, server_default="info"),
            sa.Column("confidence", sa.Float()),
            sa.Column("impact", sa.String(length=32)),
            sa.Column("effort", sa.String(length=32)),
            sa.Column("risk_level", sa.String(length=64), nullable=False, server_default="dba_approval"),
            sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
            sa.Column("fingerprint", sa.String(length=128), nullable=False),
            sa.Column("action_sql", sa.Text()),
            sa.Column("action_payload", sa.JSON()),
            sa.Column("evidence", sa.JSON()),
            sa.Column("source", sa.String(length=255)),
            sa.Column("generated_by", sa.String(length=128), nullable=False, server_default="ai-dba-rule-engine"),
            sa.Column("model_version", sa.String(length=64), nullable=False, server_default="v1"),
            sa.Column("model_run_id", sa.Integer(), sa.ForeignKey("ai_dba_model_run.id")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_ai_dba_recommendations_cluster_status", "ai_dba_recommendations", ["cluster_id", "status"])
        op.create_index("ix_ai_dba_recommendations_fingerprint", "ai_dba_recommendations", ["cluster_id", "fingerprint"])
    if not _has_table("ai_dba_recommendation_evidence"):
        op.create_table(
            "ai_dba_recommendation_evidence",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("recommendation_id", sa.Integer(), sa.ForeignKey("ai_dba_recommendations.id"), nullable=False),
            sa.Column("source_type", sa.String(length=64)),
            sa.Column("source_name", sa.String(length=255)),
            sa.Column("metric_name", sa.String(length=255)),
            sa.Column("metric_value", sa.String(length=255)),
            sa.Column("evidence_text", sa.Text()),
            sa.Column("evidence_json", sa.JSON()),
            sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not _has_table("ai_dba_recommendation_feedback"):
        op.create_table(
            "ai_dba_recommendation_feedback",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("recommendation_id", sa.Integer(), sa.ForeignKey("ai_dba_recommendations.id"), nullable=False),
            sa.Column("user_email", sa.String(length=255)),
            sa.Column("vote", sa.String(length=32)),
            sa.Column("status", sa.String(length=32)),
            sa.Column("comment", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("ai_dba_recommendation_feedback")
    op.drop_table("ai_dba_recommendation_evidence")
    op.drop_index("ix_ai_dba_recommendations_fingerprint", table_name="ai_dba_recommendations")
    op.drop_index("ix_ai_dba_recommendations_cluster_status", table_name="ai_dba_recommendations")
    op.drop_table("ai_dba_recommendations")
    op.drop_table("ai_sql_fingerprint")
    op.drop_table("ai_dba_model_run")
