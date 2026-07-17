"""Agentic isolation and immutable evidence foundation.

Revision ID: 20260717_0010
Revises: 20260716_0009
"""
from alembic import op
import sqlalchemy as sa

revision = "20260717_0010"
down_revision = "20260716_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_incident", sa.Column("inventory_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_ai_incident_inventory", "ai_incident", "cluster_inventory", ["inventory_id"], ["id"])
    op.create_index("ix_ai_incident_inventory_id", "ai_incident", ["inventory_id"])
    op.create_table("ai_evidence_bundle",
        sa.Column("bundle_id", sa.String(36), primary_key=True),
        sa.Column("inventory_id", sa.Integer(), sa.ForeignKey("cluster_inventory.id"), nullable=False),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("ai_incident.id")),
        sa.Column("cluster_id", sa.String(64), nullable=False), sa.Column("cluster_name", sa.String(255), nullable=False),
        sa.Column("namespace", sa.String(255), nullable=False), sa.Column("window_start", sa.DateTime(timezone=True)),
        sa.Column("window_end", sa.DateTime(timezone=True)), sa.Column("trust_tier", sa.String(32), nullable=False),
        sa.Column("freshness_status", sa.String(32), nullable=False), sa.Column("quality_status", sa.String(32), nullable=False),
        sa.Column("partial", sa.Boolean(), nullable=False), sa.Column("warnings", sa.JSON()),
        sa.Column("action_ready", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_ai_evidence_bundle_inventory_id", "ai_evidence_bundle", ["inventory_id"])
    op.create_index("ix_ai_evidence_bundle_incident_id", "ai_evidence_bundle", ["incident_id"])
    op.create_table("ai_evidence_item",
        sa.Column("evidence_id", sa.String(36), primary_key=True), sa.Column("bundle_id", sa.String(36), sa.ForeignKey("ai_evidence_bundle.bundle_id"), nullable=False),
        sa.Column("inventory_id", sa.Integer(), sa.ForeignKey("cluster_inventory.id"), nullable=False), sa.Column("incident_id", sa.Integer(), sa.ForeignKey("ai_incident.id")),
        sa.Column("source_type", sa.String(64), nullable=False), sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("collector_name", sa.String(128), nullable=False), sa.Column("collector_version", sa.String(64), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True)), sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("freshness_seconds", sa.Integer()), sa.Column("trust_tier", sa.String(32), nullable=False),
        sa.Column("freshness_status", sa.String(32), nullable=False), sa.Column("quality_status", sa.String(32), nullable=False),
        sa.Column("partial", sa.Boolean(), nullable=False), sa.Column("warnings", sa.JSON()), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False))
    for name, cols in (("ix_ai_evidence_item_bundle_id", ["bundle_id"]), ("ix_ai_evidence_item_inventory_id", ["inventory_id"]),
                       ("ix_ai_evidence_item_incident_id", ["incident_id"]), ("ix_ai_evidence_item_payload_sha256", ["payload_sha256"])):
        op.create_index(name, "ai_evidence_item", cols)
    op.create_table("ai_tool_invocation_audit",
        sa.Column("invocation_id", sa.String(36), primary_key=True), sa.Column("bundle_id", sa.String(36), sa.ForeignKey("ai_evidence_bundle.bundle_id")),
        sa.Column("inventory_id", sa.Integer(), sa.ForeignKey("cluster_inventory.id"), nullable=False), sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(64)), sa.Column("mode", sa.String(32), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_sha256", sa.String(64)), sa.Column("output_sha256", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_index("ix_ai_tool_invocation_audit_bundle_id", "ai_tool_invocation_audit", ["bundle_id"])
    op.create_index("ix_ai_tool_invocation_audit_inventory_id", "ai_tool_invocation_audit", ["inventory_id"])
    op.create_table("ai_workflow_run",
        sa.Column("workflow_run_id", sa.String(36), primary_key=True), sa.Column("bundle_id", sa.String(36), sa.ForeignKey("ai_evidence_bundle.bundle_id")),
        sa.Column("inventory_id", sa.Integer(), sa.ForeignKey("cluster_inventory.id"), nullable=False), sa.Column("incident_id", sa.Integer(), sa.ForeignKey("ai_incident.id")),
        sa.Column("workflow_name", sa.String(128), nullable=False), sa.Column("mode", sa.String(32), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("summary", sa.JSON()))
    op.create_index("ix_ai_workflow_run_bundle_id", "ai_workflow_run", ["bundle_id"])
    op.create_index("ix_ai_workflow_run_inventory_id", "ai_workflow_run", ["inventory_id"])
    op.create_index("ix_ai_workflow_run_incident_id", "ai_workflow_run", ["incident_id"])


def downgrade() -> None:
    op.drop_table("ai_workflow_run"); op.drop_table("ai_tool_invocation_audit")
    op.drop_table("ai_evidence_item"); op.drop_table("ai_evidence_bundle")
    op.drop_index("ix_ai_incident_inventory_id", table_name="ai_incident")
    op.drop_constraint("fk_ai_incident_inventory", "ai_incident", type_="foreignkey")
    op.drop_column("ai_incident", "inventory_id")
