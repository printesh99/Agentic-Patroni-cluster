"""ai ml metadata baseline

Revision ID: 20260628_0001
Revises:
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = "20260628_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cluster_inventory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("dc", sa.String(length=64), nullable=False),
        sa.Column("env", sa.String(length=32), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("cluster_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("patroni_api_url", sa.String(length=512)),
        sa.Column("prometheus_url", sa.String(length=512)),
        sa.Column("loki_url", sa.String(length=512)),
        sa.Column("pg_service_rw", sa.String(length=255)),
        sa.Column("pg_service_ro", sa.String(length=255)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "cluster_health_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inventory_id", sa.Integer(), sa.ForeignKey("cluster_inventory.id")),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("role", sa.String(length=64)),
        sa.Column("timeline", sa.Integer()),
        sa.Column("replication_lag_seconds", sa.Float()),
        sa.Column("wal_rate_mb_min", sa.Float()),
        sa.Column("wal_pvc_used_percent", sa.Float()),
        sa.Column("pgdata_pvc_used_percent", sa.Float()),
        sa.Column("active_connections", sa.Integer()),
        sa.Column("max_connections", sa.Integer()),
        sa.Column("active_connections_percent", sa.Float()),
        sa.Column("cpu_percent", sa.Float()),
        sa.Column("memory_percent", sa.Float()),
        sa.Column("pgbouncer_pool_used_percent", sa.Float()),
        sa.Column("locks_waiting_count", sa.Integer()),
        sa.Column("long_txn_count", sa.Integer()),
        sa.Column("idle_in_transaction_count", sa.Integer()),
        sa.Column("deadlocks_per_min", sa.Float()),
        sa.Column("archive_failed_count", sa.Integer()),
        sa.Column("backup_status", sa.String(length=64)),
        sa.Column("pod_restart_count", sa.Integer()),
        sa.Column("logical_slot_inactive_count", sa.Integer()),
        sa.Column("replication_slot_retained_wal_mb", sa.Float()),
        sa.Column("patroni_status", sa.JSON()),
        sa.Column("raw_metrics", sa.JSON()),
    )
    op.create_table(
        "ml_model_registry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_type", sa.String(length=128), nullable=False),
        sa.Column("cluster_name", sa.String(length=255)),
        sa.Column("region", sa.String(length=64)),
        sa.Column("env", sa.String(length=32)),
        sa.Column("model_path", sa.String(length=1024), nullable=False),
        sa.Column("feature_list", sa.JSON(), nullable=False),
        sa.Column("training_start", sa.DateTime(timezone=True)),
        sa.Column("training_end", sa.DateTime(timezone=True)),
        sa.Column("training_rows", sa.Integer()),
        sa.Column("contamination", sa.Float()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "ml_anomaly_score",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("cluster_health_snapshot.id")),
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("ml_model_registry.id")),
        sa.Column("scored_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_anomaly", sa.Boolean()),
        sa.Column("anomaly_score", sa.Float()),
        sa.Column("severity", sa.String(length=32)),
        sa.Column("top_features", sa.JSON()),
        sa.Column("evidence", sa.JSON()),
        sa.Column("raw_output", sa.JSON()),
    )
    op.create_table(
        "ml_forecast_result",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inventory_id", sa.Integer(), sa.ForeignKey("cluster_inventory.id")),
        sa.Column("metric_name", sa.String(length=128), nullable=False),
        sa.Column("forecast_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("current_value", sa.Float()),
        sa.Column("growth_per_hour", sa.Float()),
        sa.Column("predicted_warning_time", sa.DateTime(timezone=True)),
        sa.Column("predicted_critical_time", sa.DateTime(timezone=True)),
        sa.Column("severity", sa.String(length=32)),
        sa.Column("raw_output", sa.JSON()),
    )
    op.create_table(
        "ai_incident",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("region", sa.String(length=64)),
        sa.Column("dc", sa.String(length=64)),
        sa.Column("cluster_name", sa.String(length=255)),
        sa.Column("severity", sa.String(length=32)),
        sa.Column("incident_type", sa.String(length=128)),
        sa.Column("title", sa.String(length=512)),
        sa.Column("evidence", sa.JSON()),
        sa.Column("rule_findings", sa.JSON()),
        sa.Column("ml_findings", sa.JSON()),
        sa.Column("forecast_findings", sa.JSON()),
        sa.Column("rag_context", sa.JSON()),
        sa.Column("ai_summary", sa.Text()),
        sa.Column("recommended_action", sa.Text()),
        sa.Column("confidence", sa.Float()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("approved_by", sa.String(length=255)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "ai_action_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("ai_incident.id")),
        sa.Column("action_level", sa.String(length=16)),
        sa.Column("action_type", sa.String(length=128)),
        sa.Column("command_preview", sa.Text()),
        sa.Column("requested_by", sa.String(length=255)),
        sa.Column("approved_by", sa.String(length=255)),
        sa.Column("executed_by", sa.String(length=255)),
        sa.Column("execution_status", sa.String(length=64)),
        sa.Column("output", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ai_action_audit")
    op.drop_table("ai_incident")
    op.drop_table("ml_forecast_result")
    op.drop_table("ml_anomaly_score")
    op.drop_table("ml_model_registry")
    op.drop_table("cluster_health_snapshot")
    op.drop_table("cluster_inventory")
