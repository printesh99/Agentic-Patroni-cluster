"""SQLAlchemy models for AI/ML metadata.

The schema mirrors docs/ai-dba-phases/phase-00-foundations.md and is deliberately
portable between local SQLite and production Postgres.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class ClusterInventory(Base):
    __tablename__ = "cluster_inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    dc: Mapped[str] = mapped_column(String(64), nullable=False)
    env: Mapped[str] = mapped_column(String(32), nullable=False)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    cluster_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    patroni_api_url: Mapped[str | None] = mapped_column(String(512))
    prometheus_url: Mapped[str | None] = mapped_column(String(512))
    loki_url: Mapped[str | None] = mapped_column(String(512))
    pg_service_rw: Mapped[str | None] = mapped_column(String(255))
    pg_service_ro: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClusterHealthSnapshot(Base):
    __tablename__ = "cluster_health_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_id: Mapped[int | None] = mapped_column(ForeignKey("cluster_inventory.id"))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    role: Mapped[str | None] = mapped_column(String(64))
    timeline: Mapped[int | None] = mapped_column(Integer)
    replication_lag_seconds: Mapped[float | None] = mapped_column(Float)
    wal_rate_mb_min: Mapped[float | None] = mapped_column(Float)
    wal_pvc_used_percent: Mapped[float | None] = mapped_column(Float)
    pgdata_pvc_used_percent: Mapped[float | None] = mapped_column(Float)
    active_connections: Mapped[int | None] = mapped_column(Integer)
    max_connections: Mapped[int | None] = mapped_column(Integer)
    active_connections_percent: Mapped[float | None] = mapped_column(Float)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    memory_percent: Mapped[float | None] = mapped_column(Float)
    pgbouncer_pool_used_percent: Mapped[float | None] = mapped_column(Float)
    locks_waiting_count: Mapped[int | None] = mapped_column(Integer)
    long_txn_count: Mapped[int | None] = mapped_column(Integer)
    idle_in_transaction_count: Mapped[int | None] = mapped_column(Integer)
    deadlocks_per_min: Mapped[float | None] = mapped_column(Float)
    archive_failed_count: Mapped[int | None] = mapped_column(Integer)
    backup_status: Mapped[str | None] = mapped_column(String(64))
    backup_duration_minutes: Mapped[float | None] = mapped_column(Float)
    pod_restart_count: Mapped[int | None] = mapped_column(Integer)
    logical_slot_inactive_count: Mapped[int | None] = mapped_column(Integer)
    replication_slot_retained_wal_mb: Mapped[float | None] = mapped_column(Float)
    pg_stat_statements_slow_query_count: Mapped[int | None] = mapped_column(Integer)
    temp_files_mb: Mapped[float | None] = mapped_column(Float)
    patroni_status: Mapped[dict | None] = mapped_column(JSON)
    raw_metrics: Mapped[dict | None] = mapped_column(JSON)


class MlModelRegistry(Base):
    __tablename__ = "ml_model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_type: Mapped[str] = mapped_column(String(128), nullable=False)
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    region: Mapped[str | None] = mapped_column(String(64))
    env: Mapped[str | None] = mapped_column(String(32))
    model_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    feature_list: Mapped[list] = mapped_column(JSON, nullable=False)
    training_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_rows: Mapped[int | None] = mapped_column(Integer)
    contamination: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MlAnomalyScore(Base):
    __tablename__ = "ml_anomaly_score"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("cluster_health_snapshot.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("ml_model_registry.id"))
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_anomaly: Mapped[bool | None] = mapped_column(Boolean)
    anomaly_score: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[str | None] = mapped_column(String(32))
    top_features: Mapped[list | None] = mapped_column(JSON)
    evidence: Mapped[dict | None] = mapped_column(JSON)
    raw_output: Mapped[dict | None] = mapped_column(JSON)


class MlForecastResult(Base):
    __tablename__ = "ml_forecast_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_id: Mapped[int | None] = mapped_column(ForeignKey("cluster_inventory.id"))
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    forecast_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    current_value: Mapped[float | None] = mapped_column(Float)
    growth_per_hour: Mapped[float | None] = mapped_column(Float)
    predicted_warning_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    predicted_critical_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    severity: Mapped[str | None] = mapped_column(String(32))
    raw_output: Mapped[dict | None] = mapped_column(JSON)


class AiIncident(Base):
    __tablename__ = "ai_incident"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_id: Mapped[int | None] = mapped_column(ForeignKey("cluster_inventory.id"), index=True)
    region: Mapped[str | None] = mapped_column(String(64))
    dc: Mapped[str | None] = mapped_column(String(64))
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    severity: Mapped[str | None] = mapped_column(String(32))
    incident_type: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(512))
    evidence: Mapped[dict | None] = mapped_column(JSON)
    rule_findings: Mapped[list | None] = mapped_column(JSON)
    ml_findings: Mapped[dict | None] = mapped_column(JSON)
    forecast_findings: Mapped[list | None] = mapped_column(JSON)
    rag_context: Mapped[dict | None] = mapped_column(JSON)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiKnowledgeBase(Base):
    __tablename__ = "ai_knowledge_base"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_type: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(64))
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSON)
    source_file: Mapped[str | None] = mapped_column(String(512))
    runbook_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiAgentRun(Base):
    __tablename__ = "ai_agent_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128), default="ai-dba-agent", nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), default="MANUAL", nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(255))
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    database_name: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiAgentRecommendation(Base):
    __tablename__ = "ai_recommendation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_agent_run.id"))
    severity: Mapped[str] = mapped_column(String(32), default="INFO", nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="OTHER", nullable=False)
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    region_name: Mapped[str | None] = mapped_column(String(64))
    dc_name: Mapped[str | None] = mapped_column(String(64))
    database_name: Mapped[str | None] = mapped_column(String(255))
    object_name: Mapped[str | None] = mapped_column(String(512))
    finding: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict | list | None] = mapped_column(JSON)
    root_cause: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    recommended_sql: Mapped[str | None] = mapped_column(Text)
    rollback_sql: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(32), default="LOW", nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    approval_status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_status: Mapped[str | None] = mapped_column(String(64))
    execution_output: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiActionAudit(Base):
    __tablename__ = "ai_action_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("ai_incident.id"))
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("ai_recommendation.id"))
    action_level: Mapped[str | None] = mapped_column(String(16))
    action_type: Mapped[str | None] = mapped_column(String(128))
    command_preview: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str | None] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    executed_by: Mapped[str | None] = mapped_column(String(255))
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_status: Mapped[str | None] = mapped_column(String(64))
    execution_output: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiActionPlan(Base):
    __tablename__ = "ai_action_plan"
    plan_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action_audit_id: Mapped[int] = mapped_column(ForeignKey("ai_action_audit.id"), nullable=False, unique=True)
    canonical_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    canonical_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiActionApproval(Base):
    __tablename__ = "ai_action_approval"
    __table_args__ = (UniqueConstraint("action_audit_id", "subject_id", name="uq_action_approval_subject"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_audit_id: Mapped[int] = mapped_column(ForeignKey("ai_action_audit.id"), nullable=False, index=True)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    roles: Mapped[list] = mapped_column(JSON, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OperationalReadinessEvidence(Base):
    __tablename__ = "operational_readiness_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("cluster_inventory.id"), nullable=False, index=True)
    gate_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())



class AiDbaModelRun(Base):
    __tablename__ = "ai_dba_model_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[str | None] = mapped_column(String(64))
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    run_type: Mapped[str] = mapped_column(String(64), default="recommendation", nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), default="rule-engine", nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows_analyzed: Mapped[int | None] = mapped_column(Integer)
    recommendations_created: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    run_metadata: Mapped[dict | None] = mapped_column("metadata", JSON)


class AiSqlFingerprint(Base):
    __tablename__ = "ai_sql_fingerprint"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[str | None] = mapped_column(String(64))
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    database_name: Mapped[str | None] = mapped_column(String(255))
    queryid: Mapped[str | None] = mapped_column(String(128))
    normalized_query: Mapped[str | None] = mapped_column(Text)
    calls: Mapped[int | None] = mapped_column(Integer)
    mean_exec_ms: Mapped[float | None] = mapped_column(Float)
    total_exec_ms: Mapped[float | None] = mapped_column(Float)
    rows_returned: Mapped[int | None] = mapped_column(Integer)
    cache_hit_pct: Mapped[float | None] = mapped_column(Float)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    extra: Mapped[dict | None] = mapped_column(JSON)


class AiDbaRecommendation(Base):
    __tablename__ = "ai_dba_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[str | None] = mapped_column(String(64))
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    database_name: Mapped[str | None] = mapped_column(String(255))
    schema_name: Mapped[str | None] = mapped_column(String(255))
    object_name: Mapped[str | None] = mapped_column(String(512))
    object_type: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    recommendation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(32), default="info", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    impact: Mapped[str | None] = mapped_column(String(32))
    effort: Mapped[str | None] = mapped_column(String(32))
    risk_level: Mapped[str] = mapped_column(String(64), default="dba_approval", nullable=False)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    action_sql: Mapped[str | None] = mapped_column(Text)
    action_payload: Mapped[dict | None] = mapped_column(JSON)
    evidence: Mapped[list | None] = mapped_column(JSON)
    source: Mapped[str | None] = mapped_column(String(255))
    generated_by: Mapped[str] = mapped_column(String(128), default="ai-dba-rule-engine", nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    model_run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_dba_model_run.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiDbaRecommendationEvidence(Base):
    __tablename__ = "ai_dba_recommendation_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("ai_dba_recommendations.id"), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_name: Mapped[str | None] = mapped_column(String(255))
    metric_name: Mapped[str | None] = mapped_column(String(255))
    metric_value: Mapped[str | None] = mapped_column(String(255))
    evidence_text: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[dict | None] = mapped_column(JSON)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiDbaRecommendationFeedback(Base):
    __tablename__ = "ai_dba_recommendation_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("ai_dba_recommendations.id"), nullable=False)
    user_email: Mapped[str | None] = mapped_column(String(255))
    vote: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# pg_profile subsystem (v29) — central historical-performance repository models.
# Merged from the v28 pg_profile work. Tables created by migration 20260712_0008.
# ---------------------------------------------------------------------------
class PgProfileServer(Base):
    __tablename__ = "pgprofile_server"
    __table_args__ = (
        CheckConstraint("sslmode IN ('verify-full','verify-ca')", name="ck_pgprofile_server_sslmode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_id: Mapped[int | None] = mapped_column(ForeignKey("cluster_inventory.id"), index=True)
    server_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    region: Mapped[str | None] = mapped_column(String(64))
    dc: Mapped[str | None] = mapped_column(String(64))
    environment: Mapped[str | None] = mapped_column(String(32), index=True)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    cluster_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False, default="postgres")
    credential_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    endpoint_host: Mapped[str] = mapped_column(String(512), nullable=False)
    endpoint_port: Mapped[int] = mapped_column(Integer, nullable=False, default=5555)
    sslmode: Mapped[str] = mapped_column(String(32), nullable=False, default="verify-full")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    registration_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sample_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_sample_id: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PgProfileSampleRun(Base):
    __tablename__ = "pgprofile_sample_run"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_pgprofile_sample_run_idempotency"),
        CheckConstraint("trigger_type IN ('SCHEDULED','MANUAL','INCIDENT_START','INCIDENT_RECOVERY')",
                        name="ck_pgprofile_sample_trigger"),
        CheckConstraint("status IN ('RUNNING','SUCCEEDED','FAILED','SKIPPED','PARTIAL')",
                        name="ck_pgprofile_sample_status"),
        Index("ix_pgprofile_sample_server_started", "pgprofile_server_id", "started_at"),
        Index("ix_pgprofile_sample_status_started", "status", "started_at"),
        Index("ix_pgprofile_sample_incident", "incident_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pgprofile_server_id: Mapped[int] = mapped_column(ForeignKey("pgprofile_server.id"), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    sample_id: Mapped[int | None] = mapped_column(Integer)
    sample_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("ai_incident.id"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    sanitized_error_message: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PgProfileReport(Base):
    __tablename__ = "pgprofile_report"
    __table_args__ = (
        UniqueConstraint("pgprofile_server_id", "start_sample_id", "end_sample_id", "report_type",
                         name="uq_pgprofile_report_range"),
        CheckConstraint("report_type IN ('REGULAR','DIFF')", name="ck_pgprofile_report_type"),
        CheckConstraint("generation_status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','SKIPPED','PARTIAL','TOO_LARGE')",
                        name="ck_pgprofile_report_status"),
        Index("ix_pgprofile_report_server_period", "pgprofile_server_id", "period_start", "period_end"),
        Index("ix_pgprofile_report_incident", "incident_id"),
        Index("ix_pgprofile_report_status", "generation_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pgprofile_server_id: Mapped[int] = mapped_column(ForeignKey("pgprofile_server.id"), nullable=False)
    start_sample_id: Mapped[int] = mapped_column(Integer, nullable=False)
    end_sample_id: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report_type: Mapped[str] = mapped_column(String(16), nullable=False, default="REGULAR")
    generation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("ai_incident.id"))
    report_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    html_content: Mapped[bytes | None] = mapped_column(LargeBinary)
    html_compressed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    original_size_bytes: Mapped[int | None] = mapped_column(Integer)
    stored_size_bytes: Mapped[int | None] = mapped_column(Integer)
    storage_reference: Mapped[str | None] = mapped_column(String(1024))
    sanitized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PgProfileFeature(Base):
    __tablename__ = "pgprofile_feature"
    __table_args__ = (
        Index("ix_pgprofile_feature_server_period", "pgprofile_server_id", "period_start", "period_end"),
        Index("ix_pgprofile_feature_query_period", "database_name", "query_id", "period_start"),
        Index("ix_pgprofile_feature_incident", "incident_id"),
        Index("ix_pgprofile_feature_type", "feature_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pgprofile_server_id: Mapped[int] = mapped_column(ForeignKey("pgprofile_server.id"), nullable=False)
    start_sample_id: Mapped[int] = mapped_column(Integer, nullable=False)
    end_sample_id: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    database_name: Mapped[str | None] = mapped_column(String(255))
    query_id: Mapped[str | None] = mapped_column(String(128))
    query_fingerprint: Mapped[str | None] = mapped_column(String(64))
    feature_type: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    workload_label: Mapped[str | None] = mapped_column(String(64))
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("ai_incident.id"))
    extraction_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IncidentPgProfileReport(Base):
    __tablename__ = "incident_pgprofile_report"
    __table_args__ = (
        UniqueConstraint("incident_id", "report_id", name="uq_incident_pgprofile_report"),
        Index("ix_incident_pgprofile_report_incident", "incident_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("ai_incident.id"), nullable=False)
    report_id: Mapped[int] = mapped_column(ForeignKey("pgprofile_report.id"), nullable=False)
    link_type: Mapped[str] = mapped_column(String(32), nullable=False, default="PERFORMANCE_EVIDENCE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QueryPerformanceBaseline(Base):
    __tablename__ = "query_performance_baseline"
    __table_args__ = (
        UniqueConstraint("pgprofile_server_id", "database_name", "query_id", "weekday", "hour",
                         "model_version", name="uq_query_perf_baseline_window"),
        Index("ix_query_perf_baseline_query", "pgprofile_server_id", "database_name", "query_id"),
        Index("ix_query_perf_baseline_last_seen", "last_seen"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pgprofile_server_id: Mapped[int] = mapped_column(ForeignKey("pgprofile_server.id"), nullable=False)
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    query_id: Mapped[str] = mapped_column(String(128), nullable=False)
    query_fingerprint: Mapped[str | None] = mapped_column(String(64))
    weekday: Mapped[int | None] = mapped_column(Integer)
    hour: Mapped[int | None] = mapped_column(Integer)
    workload_window: Mapped[str | None] = mapped_column(String(64))
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    median_execution_ms: Mapped[float | None] = mapped_column(Float)
    mad_execution_ms: Mapped[float | None] = mapped_column(Float)
    p95_execution_ms: Mapped[float | None] = mapped_column(Float)
    median_calls: Mapped[float | None] = mapped_column(Float)
    median_rows: Mapped[float | None] = mapped_column(Float)
    median_buffer_reads: Mapped[float | None] = mapped_column(Float)
    median_temp_io_bytes: Mapped[float | None] = mapped_column(Float)
    median_wal_bytes: Mapped[float | None] = mapped_column(Float)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history_status: Mapped[str] = mapped_column(String(32), nullable=False, default="COLD_START")
    feedback_state: Mapped[str | None] = mapped_column(String(32))
    model_version: Mapped[str] = mapped_column(String(32), nullable=False, default="robust-v1")
    model_metadata: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiEvidenceBundle(Base):
    __tablename__ = "ai_evidence_bundle"
    bundle_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("cluster_inventory.id"), nullable=False, index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("ai_incident.id"), index=True)
    cluster_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cluster_name: Mapped[str] = mapped_column(String(255), nullable=False)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trust_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="VERIFIED")
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False, default="FRESH")
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False, default="COMPLETE")
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warnings: Mapped[list | None] = mapped_column(JSON)
    action_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiEvidenceItem(Base):
    __tablename__ = "ai_evidence_item"
    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("ai_evidence_bundle.bundle_id"), nullable=False, index=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("cluster_inventory.id"), nullable=False, index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("ai_incident.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    collector_name: Mapped[str] = mapped_column(String(128), nullable=False)
    collector_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    freshness_seconds: Mapped[int | None] = mapped_column(Integer)
    trust_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warnings: Mapped[list | None] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class AiToolInvocationAudit(Base):
    __tablename__ = "ai_tool_invocation_audit"
    invocation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bundle_id: Mapped[str | None] = mapped_column(ForeignKey("ai_evidence_bundle.bundle_id"), index=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("cluster_inventory.id"), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="READ_ONLY")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_sha256: Mapped[str | None] = mapped_column(String(64))
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiWorkflowRun(Base):
    __tablename__ = "ai_workflow_run"
    workflow_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bundle_id: Mapped[str | None] = mapped_column(ForeignKey("ai_evidence_bundle.bundle_id"), index=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("cluster_inventory.id"), nullable=False, index=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("ai_incident.id"), index=True)
    workflow_name: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="SHADOW")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict | None] = mapped_column(JSON)
