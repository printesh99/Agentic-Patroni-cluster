"""Normalized action control and operational readiness evidence."""
from alembic import op
import sqlalchemy as sa
revision = "20260717_0011"
down_revision = "20260717_0010"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("ai_action_plan",
        sa.Column("plan_id", sa.String(36), primary_key=True),
        sa.Column("action_audit_id", sa.Integer(), sa.ForeignKey("ai_action_audit.id"), nullable=False, unique=True),
        sa.Column("canonical_sha256", sa.String(64), nullable=False), sa.Column("canonical_payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_ai_action_plan_canonical_sha256", "ai_action_plan", ["canonical_sha256"])
    op.create_table("ai_action_approval",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("action_audit_id", sa.Integer(), sa.ForeignKey("ai_action_audit.id"), nullable=False),
        sa.Column("plan_sha256", sa.String(64), nullable=False), sa.Column("subject_id", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False), sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text()), sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("action_audit_id", "subject_id", name="uq_action_approval_subject"))
    op.create_index("ix_ai_action_approval_action_audit_id", "ai_action_approval", ["action_audit_id"])
    op.create_table("operational_readiness_evidence",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("inventory_id", sa.Integer(), sa.ForeignKey("cluster_inventory.id"), nullable=False),
        sa.Column("gate_name", sa.String(64), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False), sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_operational_readiness_evidence_inventory_id", "operational_readiness_evidence", ["inventory_id"])
    op.create_index("ix_operational_readiness_evidence_gate_name", "operational_readiness_evidence", ["gate_name"])

def downgrade() -> None:
    op.drop_table("operational_readiness_evidence")
    op.drop_table("ai_action_approval")
    op.drop_table("ai_action_plan")
