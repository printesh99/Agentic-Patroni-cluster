"""Event-gated pg_profile evidence attachment for performance incidents."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from ..db.models import AiIncident, PgProfileFeature, PgProfileServer
from ..db.session import SessionLocal
from . import client, report_service, service
from .config import settings
from .schemas import ReportCreate
from .security import sanitize_error

PERFORMANCE_TYPES = {
    "query_latency", "performance_regression", "performance", "cpu_saturation", "cpu",
    "storage_latency", "storage", "temporary_file_spike", "temp_file", "lock_contention",
    "locks", "wal_spike", "wal", "tps_degradation", "connections", "connection_saturation",
}


def _eligible(value: str | None) -> bool:
    key = (value or "").strip().lower().replace(" ", "_")
    return key in PERFORMANCE_TYPES or any(token in key for token in ("performance", "latency", "lock", "wal", "connection", "cpu", "temp"))


def handle_incident(incident_id: int, event: str) -> dict[str, Any]:
    if not (settings.enabled and settings.incident_sampling_enabled):
        return {"available": False, "status": "UNAVAILABLE"}
    with SessionLocal() as db:
        incident = db.get(AiIncident, incident_id)
        if not incident or not _eligible(incident.incident_type):
            return {"available": False, "status": "NOT_APPLICABLE"}
        server_stmt = select(PgProfileServer).where(
            PgProfileServer.cluster_name == incident.cluster_name,
            PgProfileServer.enabled.is_(True))
        if settings.allowed_environments:
            server_stmt = server_stmt.where(func.lower(PgProfileServer.environment).in_(settings.allowed_environments))
        server = db.execute(server_stmt.order_by(PgProfileServer.id).limit(1)).scalar_one_or_none()
        if not server:
            evidence = dict(incident.evidence or {})
            evidence["pg_profile"] = {"status": "PARTIAL_DATA", "reason": "no registered pg_profile server"}
            incident.evidence = evidence; flag_modified(incident, "evidence"); db.commit()
            return evidence["pg_profile"]
        server_id, server_name = server.id, server.server_name
    trigger = "INCIDENT_RECOVERY" if event == "RECOVERY" else "INCIDENT_START"
    sample = service.collect_sample(server_id, trigger, "incident-service", incident_id,
                                    idempotency_key=f"incident:{incident_id}:{event}", retries=1)
    summary: dict[str, Any] = {"status": sample.get("status"), "sample_run_id": (sample.get("run") or {}).get("id"),
                              "sample_id": (sample.get("run") or {}).get("sample_id"), "report_ids": []}
    if sample.get("status") == "SUCCEEDED" and settings.auto_report_enabled:
        samples = client.list_samples(server_name, days=min(settings.retention_days, 14))
        samples = sorted(samples, key=lambda r: r["sample_time"])
        current_id = summary["sample_id"]
        prior = [r for r in samples if int(r["sample"]) < int(current_id or 0)]
        if prior and current_id:
            payload = ReportCreate(pgprofile_server_id=server_id, start_sample_id=int(prior[-1]["sample"]),
                                   end_sample_id=int(current_id), report_type="REGULAR", incident_id=incident_id)
            report = report_service.generate(payload, "incident-service")
            report_id = (report.get("report") or {}).get("id")
            if report_id:
                summary["report_ids"] = [report_id]
                summary["report_status"] = report.get("status")
    with SessionLocal() as db:
        incident = db.get(AiIncident, incident_id)
        top = db.execute(select(PgProfileFeature).where(
            PgProfileFeature.incident_id == incident_id,
            PgProfileFeature.feature_type == "QUERY_INTERVAL").order_by(PgProfileFeature.id.desc()).limit(5)).scalars().all()
        summary["top_query_evidence"] = [{"id": f"pgprofile-feature:{r.id}",
                                           "query_id": r.query_id, "query_fingerprint": r.query_fingerprint,
                                           "metrics": {k: r.feature_values.get(k) for k in (
                                               "calls", "mean_execution_ms", "total_execution_ms",
                                               "shared_blocks_read", "temp_blocks_written", "workload_contribution_pct")}}
                                         for r in top]
        evidence = dict(incident.evidence or {})
        evidence["pg_profile"] = summary
        incident.evidence = evidence; flag_modified(incident, "evidence")
        db.commit()
    return summary


def safe_handle(incident_id: int, event: str) -> dict[str, Any]:
    try:
        return handle_incident(incident_id, event)
    except Exception as exc:
        return {"available": False, "status": "PARTIAL_DATA", "reason": sanitize_error(exc)}
