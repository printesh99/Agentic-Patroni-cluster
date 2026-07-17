"""pg_profile historical performance metadata.

Revision ID: 20260712_0008
Revises: 20260705_0007
Create Date: 2026-07-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260712_0008"
down_revision = "20260705_0007"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("pgprofile_server"):
        op.create_table(
            "pgprofile_server",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("inventory_id", sa.Integer(), sa.ForeignKey("cluster_inventory.id")),
            sa.Column("server_name", sa.String(128), nullable=False, unique=True),
            sa.Column("region", sa.String(64)), sa.Column("dc", sa.String(64)),
            sa.Column("environment", sa.String(32)),
            sa.Column("namespace", sa.String(255), nullable=False),
            sa.Column("cluster_name", sa.String(255), nullable=False),
            sa.Column("database_name", sa.String(255), nullable=False, server_default="postgres"),
            sa.Column("credential_reference", sa.String(512), nullable=False),
            sa.Column("endpoint_host", sa.String(512), nullable=False),
            sa.Column("endpoint_port", sa.Integer(), nullable=False, server_default="5555"),
            sa.Column("sslmode", sa.String(32), nullable=False, server_default="verify-full"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("registration_status", sa.String(32), nullable=False, server_default="PENDING"),
            sa.Column("last_verified_at", sa.DateTime(timezone=True)),
            sa.Column("last_sample_at", sa.DateTime(timezone=True)),
            sa.Column("last_successful_sample_id", sa.Integer()),
            sa.Column("last_error", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.CheckConstraint("sslmode IN ('verify-full','verify-ca')", name="ck_pgprofile_server_sslmode"),
        )
        op.create_index("ix_pgprofile_server_inventory_id", "pgprofile_server", ["inventory_id"])
        op.create_index("ix_pgprofile_server_environment", "pgprofile_server", ["environment"])
        op.create_index("ix_pgprofile_server_cluster_name", "pgprofile_server", ["cluster_name"])

    if not _has_table("pgprofile_sample_run"):
        op.create_table(
            "pgprofile_sample_run",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("pgprofile_server_id", sa.Integer(), sa.ForeignKey("pgprofile_server.id"), nullable=False),
            sa.Column("trigger_type", sa.String(32), nullable=False),
            sa.Column("triggered_by", sa.String(255)),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("status", sa.String(32), nullable=False, server_default="RUNNING"),
            sa.Column("sample_id", sa.Integer()), sa.Column("sample_time", sa.DateTime(timezone=True)),
            sa.Column("duration_ms", sa.Integer()),
            sa.Column("incident_id", sa.Integer(), sa.ForeignKey("ai_incident.id")),
            sa.Column("error_code", sa.String(64)),
            sa.Column("sanitized_error_message", sa.Text()), sa.Column("evidence", sa.JSON()),
            sa.Column("idempotency_key", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("idempotency_key", name="uq_pgprofile_sample_run_idempotency"),
            sa.CheckConstraint("trigger_type IN ('SCHEDULED','MANUAL','INCIDENT_START','INCIDENT_RECOVERY')",
                               name="ck_pgprofile_sample_trigger"),
            sa.CheckConstraint("status IN ('RUNNING','SUCCEEDED','FAILED','SKIPPED','PARTIAL')",
                               name="ck_pgprofile_sample_status"),
        )
        op.create_index("ix_pgprofile_sample_server_started", "pgprofile_sample_run", ["pgprofile_server_id", "started_at"])
        op.create_index("ix_pgprofile_sample_status_started", "pgprofile_sample_run", ["status", "started_at"])
        op.create_index("ix_pgprofile_sample_incident", "pgprofile_sample_run", ["incident_id"])

    if not _has_table("pgprofile_report"):
        op.create_table(
            "pgprofile_report",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("pgprofile_server_id", sa.Integer(), sa.ForeignKey("pgprofile_server.id"), nullable=False),
            sa.Column("start_sample_id", sa.Integer(), nullable=False),
            sa.Column("end_sample_id", sa.Integer(), nullable=False),
            sa.Column("period_start", sa.DateTime(timezone=True)), sa.Column("period_end", sa.DateTime(timezone=True)),
            sa.Column("report_type", sa.String(16), nullable=False, server_default="REGULAR"),
            sa.Column("generation_status", sa.String(32), nullable=False, server_default="PENDING"),
            sa.Column("generated_at", sa.DateTime(timezone=True)),
            sa.Column("incident_id", sa.Integer(), sa.ForeignKey("ai_incident.id")),
            sa.Column("report_hash", sa.String(64)), sa.Column("html_content", sa.LargeBinary()),
            sa.Column("html_compressed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("original_size_bytes", sa.Integer()), sa.Column("stored_size_bytes", sa.Integer()),
            sa.Column("storage_reference", sa.String(1024)),
            sa.Column("sanitized", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("error_message", sa.Text()), sa.Column("created_by", sa.String(255)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("pgprofile_server_id", "start_sample_id", "end_sample_id", "report_type",
                                name="uq_pgprofile_report_range"),
            sa.CheckConstraint("report_type IN ('REGULAR','DIFF')", name="ck_pgprofile_report_type"),
            sa.CheckConstraint("generation_status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','SKIPPED','PARTIAL','TOO_LARGE')",
                               name="ck_pgprofile_report_status"),
        )
        op.create_index("ix_pgprofile_report_server_period", "pgprofile_report", ["pgprofile_server_id", "period_start", "period_end"])
        op.create_index("ix_pgprofile_report_incident", "pgprofile_report", ["incident_id"])
        op.create_index("ix_pgprofile_report_status", "pgprofile_report", ["generation_status"])
        op.create_index("ix_pgprofile_report_report_hash", "pgprofile_report", ["report_hash"])

    if not _has_table("pgprofile_feature"):
        op.create_table(
            "pgprofile_feature",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("pgprofile_server_id", sa.Integer(), sa.ForeignKey("pgprofile_server.id"), nullable=False),
            sa.Column("start_sample_id", sa.Integer(), nullable=False), sa.Column("end_sample_id", sa.Integer(), nullable=False),
            sa.Column("period_start", sa.DateTime(timezone=True)), sa.Column("period_end", sa.DateTime(timezone=True)),
            sa.Column("database_name", sa.String(255)), sa.Column("query_id", sa.String(128)),
            sa.Column("query_fingerprint", sa.String(64)), sa.Column("feature_type", sa.String(64), nullable=False),
            sa.Column("feature_values", sa.JSON(), nullable=False), sa.Column("workload_label", sa.String(64)),
            sa.Column("incident_id", sa.Integer(), sa.ForeignKey("ai_incident.id")),
            sa.Column("extraction_version", sa.String(32), nullable=False, server_default="v1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_pgprofile_feature_server_period", "pgprofile_feature", ["pgprofile_server_id", "period_start", "period_end"])
        op.create_index("ix_pgprofile_feature_query_period", "pgprofile_feature", ["database_name", "query_id", "period_start"])
        op.create_index("ix_pgprofile_feature_incident", "pgprofile_feature", ["incident_id"])
        op.create_index("ix_pgprofile_feature_type", "pgprofile_feature", ["feature_type"])

    if not _has_table("incident_pgprofile_report"):
        op.create_table(
            "incident_pgprofile_report",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("incident_id", sa.Integer(), sa.ForeignKey("ai_incident.id"), nullable=False),
            sa.Column("report_id", sa.Integer(), sa.ForeignKey("pgprofile_report.id"), nullable=False),
            sa.Column("link_type", sa.String(32), nullable=False, server_default="PERFORMANCE_EVIDENCE"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("incident_id", "report_id", name="uq_incident_pgprofile_report"),
        )
        op.create_index("ix_incident_pgprofile_report_incident", "incident_pgprofile_report", ["incident_id"])

    if not _has_table("query_performance_baseline"):
        op.create_table(
            "query_performance_baseline",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("pgprofile_server_id", sa.Integer(), sa.ForeignKey("pgprofile_server.id"), nullable=False),
            sa.Column("cluster_name", sa.String(255)), sa.Column("database_name", sa.String(255), nullable=False),
            sa.Column("query_id", sa.String(128), nullable=False), sa.Column("query_fingerprint", sa.String(64)),
            sa.Column("weekday", sa.Integer()), sa.Column("hour", sa.Integer()), sa.Column("workload_window", sa.String(64)),
            sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("median_execution_ms", sa.Float()), sa.Column("mad_execution_ms", sa.Float()),
            sa.Column("p95_execution_ms", sa.Float()), sa.Column("median_calls", sa.Float()),
            sa.Column("median_rows", sa.Float()), sa.Column("median_buffer_reads", sa.Float()),
            sa.Column("median_temp_io_bytes", sa.Float()), sa.Column("median_wal_bytes", sa.Float()),
            sa.Column("first_seen", sa.DateTime(timezone=True)), sa.Column("last_seen", sa.DateTime(timezone=True)),
            sa.Column("history_status", sa.String(32), nullable=False, server_default="COLD_START"),
            sa.Column("feedback_state", sa.String(32)),
            sa.Column("model_version", sa.String(32), nullable=False, server_default="robust-v1"),
            sa.Column("model_metadata", sa.JSON()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("pgprofile_server_id", "database_name", "query_id", "weekday", "hour", "model_version",
                                name="uq_query_perf_baseline_window"),
        )
        op.create_index("ix_query_perf_baseline_query", "query_performance_baseline", ["pgprofile_server_id", "database_name", "query_id"])
        op.create_index("ix_query_perf_baseline_last_seen", "query_performance_baseline", ["last_seen"])


def downgrade() -> None:
    for table in ("query_performance_baseline", "incident_pgprofile_report", "pgprofile_feature",
                  "pgprofile_report", "pgprofile_sample_run", "pgprofile_server"):
        if _has_table(table):
            op.drop_table(table)
