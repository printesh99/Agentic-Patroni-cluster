"""Prometheus exposition for the AI/ML DBA pipeline.

Two kinds of series:
- process counters/histograms updated inline as the pipeline runs (RCA calls,
  RAG retrievals, model scoring) via the helpers below;
- gauges refreshed on each scrape from the metadata DB (open incidents, anomaly
  scores, forecast risk, model count) by ``refresh_from_db()``.

Everything is defensive: a metrics failure must never break an API response.
"""
from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import text

from .db.session import SessionLocal

REGISTRY = CollectorRegistry()

# --- inline process metrics ------------------------------------------------
RCA_CALLS = Counter(
    "ai_rca_requests_total", "RCA explain requests", ["mode"], registry=REGISTRY,
)
RCA_LATENCY = Histogram(
    "ai_rca_latency_seconds", "RCA generation latency", ["mode"], registry=REGISTRY,
)
RAG_RETRIEVALS = Counter(
    "ai_rag_retrievals_total", "RAG retrievals", ["method"], registry=REGISTRY,
)
RAG_HITS = Histogram(
    "ai_rag_hits", "Snippets returned per retrieval",
    buckets=[0, 1, 2, 3, 5, 10], registry=REGISTRY,
)
ML_SCORINGS = Counter(
    "ai_ml_scorings_total", "ML scoring runs", ["result"], registry=REGISTRY,
)
PGPROFILE_SAMPLE_RUNS = Counter(
    "pgprofile_sample_runs_total", "pg_profile sample runs", ["server", "status", "trigger"], registry=REGISTRY,
)
PGPROFILE_SAMPLE_DURATION = Histogram(
    "pgprofile_sample_duration_seconds", "pg_profile sample duration", ["server"], registry=REGISTRY,
)
PGPROFILE_LAST_SUCCESS = Gauge(
    "pgprofile_last_success_timestamp", "Last successful pg_profile sample epoch", ["server"], registry=REGISTRY,
)
PGPROFILE_COLLECTION_FAILURES = Counter(
    "pgprofile_collection_failures_total", "pg_profile collection failures", ["server"], registry=REGISTRY,
)
PGPROFILE_REPORT_RUNS = Counter(
    "pgprofile_report_runs_total", "pg_profile report runs", ["status", "type"], registry=REGISTRY,
)
PGPROFILE_REPORT_DURATION = Histogram(
    "pgprofile_report_duration_seconds", "pg_profile report generation duration", ["type"], registry=REGISTRY,
)
PGPROFILE_REPORT_SIZE = Histogram(
    "pgprofile_report_size_bytes", "Sanitized pg_profile report size", registry=REGISTRY,
)
PGPROFILE_FEATURE_EXTRACTION = Counter(
    "pgprofile_feature_extraction_total", "pg_profile feature extraction", ["status", "type"], registry=REGISTRY,
)
PGPROFILE_FEATURE_ROWS = Gauge(
    "pgprofile_feature_rows", "Stored pg_profile feature rows", registry=REGISTRY,
)
PGPROFILE_REPOSITORY_SIZE = Gauge(
    "pgprofile_repository_size_bytes", "pg_profile extension repository size", registry=REGISTRY,
)
PGPROFILE_LOCK_CONTENTION = Counter(
    "pgprofile_lock_contention_total", "pg_profile advisory lock contention", registry=REGISTRY,
)
PGPROFILE_INCIDENT_LINKS = Counter(
    "pgprofile_incident_links_total", "pg_profile incident report links", registry=REGISTRY,
)

# --- DB-backed gauges ------------------------------------------------------
OPEN_INCIDENTS = Gauge(
    "ai_open_incidents", "Open AI incidents by severity", ["severity"], registry=REGISTRY,
)
KB_DOCS = Gauge("ai_kb_documents", "Knowledge-base documents", registry=REGISTRY)
KB_EMBEDDED = Gauge("ai_kb_documents_embedded", "KB docs with an embedding", registry=REGISTRY)
MODELS_TOTAL = Gauge("ai_ml_models", "Registered ML models", registry=REGISTRY)
ANOMALIES_24H = Gauge("ai_ml_anomalies_24h", "Anomalous scores in last 24h", registry=REGISTRY)
LAST_ANOMALY_SCORE = Gauge("ai_ml_last_anomaly_score", "Most recent anomaly score", registry=REGISTRY)
FORECAST_RISKS = Gauge("ai_forecast_risks", "Forecasts at warning/critical", registry=REGISTRY)
ACTIONS_BLOCKED = Gauge("ai_actions_blocked_total", "Action audit rows blocked from execution", registry=REGISTRY)
SEMANTIC_RAG = Gauge("ai_rag_semantic_enabled", "1 if pgvector semantic RAG is active", registry=REGISTRY)


def _scalar(db, sql: str, default=0):
    try:
        v = db.execute(text(sql)).scalar()
        return v if v is not None else default
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return default


def _has_column(db, table_name: str, column_name: str) -> bool:
    try:
        bind = db.get_bind()
        if bind.dialect.name == "sqlite":
            rows = db.execute(text(f"PRAGMA table_info({table_name})")).all()
            return any(str(row[1]) == column_name for row in rows)
        return bool(db.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table_name AND column_name = :column_name"
        ), {"table_name": table_name, "column_name": column_name}).scalar())
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False


def refresh_from_db() -> None:
    """Repopulate DB-backed gauges. Called on each scrape."""
    try:
        from .ai import rag_retriever
        SEMANTIC_RAG.set(1 if rag_retriever.semantic_enabled() else 0)
    except Exception:
        SEMANTIC_RAG.set(0)

    try:
        with SessionLocal() as db:
            OPEN_INCIDENTS.clear()
            try:
                rows = db.execute(text(
                    "SELECT severity, COUNT(*) FROM ai_incident "
                    "WHERE status = 'open' GROUP BY severity"
                )).all()
                for sev, n in rows:
                    OPEN_INCIDENTS.labels(severity=str(sev or "unknown")).set(n)
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
                pass

            KB_DOCS.set(_scalar(db, "SELECT COUNT(*) FROM ai_knowledge_base"))
            # embedding column only exists when the optional pgvector migration ran.
            if _has_column(db, "ai_knowledge_base", "embedding"):
                KB_EMBEDDED.set(_scalar(
                    db,
                    "SELECT COUNT(*) FROM ai_knowledge_base WHERE embedding IS NOT NULL",
                ))
            else:
                KB_EMBEDDED.set(0)

            MODELS_TOTAL.set(_scalar(db, "SELECT COUNT(*) FROM ml_model_registry"))
            ANOMALIES_24H.set(_scalar(
                db,
                "SELECT COUNT(*) FROM ml_anomaly_score WHERE is_anomaly = true "
                "AND scored_at > now() - interval '24 hours'",
            ))
            LAST_ANOMALY_SCORE.set(_scalar(
                db, "SELECT anomaly_score FROM ml_anomaly_score ORDER BY id DESC LIMIT 1",
            ))
            FORECAST_RISKS.set(_scalar(
                db,
                "SELECT COUNT(*) FROM ml_forecast_result "
                "WHERE severity IN ('warning', 'critical')",
            ))
            ACTIONS_BLOCKED.set(_scalar(
                db, "SELECT COUNT(*) FROM ai_action_audit WHERE execution_status = 'blocked'",
            ))
            PGPROFILE_FEATURE_ROWS.set(_scalar(db, "SELECT COUNT(*) FROM pgprofile_feature"))
            try:
                from .pg_profile.client import repository_size
                PGPROFILE_REPOSITORY_SIZE.set(repository_size() or 0)
            except Exception:
                PGPROFILE_REPOSITORY_SIZE.set(0)
    except Exception:
        # Never let a scrape failure surface as an error.
        pass


def render() -> tuple[bytes, str]:
    refresh_from_db()
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
