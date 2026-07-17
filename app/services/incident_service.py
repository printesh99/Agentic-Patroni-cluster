"""Phase 5 incident lifecycle service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from .. import log_ai, loki, pg_log_analytics, pg_ops, sources as S
from ..db.models import AiIncident, MlAnomalyScore
from ..db.session import SessionLocal
from ..ml import forecasting_job, risk_scoring, scoring_job
from ..rules import engine as rule_engine
from ..ai import dba_agent
from . import snapshot_service
from . import inventory_service


def evaluate_and_upsert() -> dict[str, Any]:
    snapshot = snapshot_service.latest()
    if not snapshot.get("available"):
        snapshot = snapshot_service.collect_and_persist()
    rules = rule_engine.evaluate(snapshot)
    ml_score = scoring_job.score_latest()
    forecasts = forecasting_job.run().get("forecasts", [])
    readiness = pg_ops.readiness()
    log_findings = _log_findings()
    risk = risk_scoring.score({
        "rule_findings": rules.get("findings", []),
        "ml_findings": ml_score,
        "forecast_findings": forecasts,
        "log_findings": log_findings,
        "readiness": readiness,
    })
    incident = upsert_incident(risk, snapshot, rules, ml_score, forecasts, log_findings)
    return {"available": True, "risk": risk, "incident": serialize(incident)}


def upsert_incident(risk: dict[str, Any], snapshot: dict[str, Any], rules: dict[str, Any],
                    ml_score: dict[str, Any], forecasts: list[dict[str, Any]],
                    log_findings: list[dict[str, Any]]) -> AiIncident:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        inv = inventory_service.resolve(db)
        existing = db.execute(
            select(AiIncident)
            .where(AiIncident.inventory_id == inv.id)
            .where(AiIncident.incident_type == risk["primary_category"])
            .where(AiIncident.status == "open")
            .order_by(AiIncident.updated_at.desc(), AiIncident.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        evidence = {
            "snapshot_id": snapshot.get("snapshot_id"),
            "risk": risk,
            "timeline": [],
            "log_findings": log_findings[:10],
        }
        event = {"ts": now.isoformat(), "risk_score": risk["risk_score"], "severity": risk["final_severity"], "reasons": risk["reasons"]}
        if existing is None:
            evidence["timeline"] = [event]
            existing = AiIncident(
                inventory_id=inv.id,
                region="uae",
                dc="dc1",
                cluster_name=S.CLUSTER_NAME,
                severity=risk["final_severity"],
                incident_type=risk["primary_category"],
                title=_title(risk),
                evidence=evidence,
                rule_findings=rules.get("findings", []),
                ml_findings=ml_score,
                forecast_findings=forecasts,
                rag_context={"recommended_runbook_id": risk.get("recommended_runbook_id")},
                recommended_action=f"Review {risk.get('recommended_runbook_id')} and validate evidence before action.",
                confidence=_confidence(risk, rules, ml_score),
                status="open",
            )
            db.add(existing)
        else:
            merged = dict(existing.evidence or {})
            timeline = list(merged.get("timeline") or [])
            timeline.append(event)
            merged.update(evidence)
            merged["timeline"] = timeline[-50:]
            existing.evidence = merged
            existing.severity = risk["final_severity"]
            existing.title = _title(risk)
            existing.rule_findings = rules.get("findings", [])
            existing.ml_findings = ml_score
            existing.forecast_findings = forecasts
            existing.rag_context = {"recommended_runbook_id": risk.get("recommended_runbook_id")}
            existing.recommended_action = f"Review {risk.get('recommended_runbook_id')} and validate evidence before action."
            existing.confidence = _confidence(risk, rules, ml_score)
            existing.updated_at = now
            flag_modified(existing, "evidence")
        db.commit()
        db.refresh(existing)
        return existing


def list_incidents(limit: int = 50) -> dict[str, Any]:
    with SessionLocal() as db:
        inv = inventory_service.resolve(db)
        rows = db.execute(select(AiIncident).where(AiIncident.inventory_id == inv.id)
                          .order_by(AiIncident.updated_at.desc(), AiIncident.id.desc()).limit(limit)).scalars().all()
        return {"available": True, "incidents": [serialize(r) for r in rows]}


def get_incident(incident_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        inv = inventory_service.resolve(db)
        row = db.execute(select(AiIncident).where(AiIncident.id == incident_id,
                                                  AiIncident.inventory_id == inv.id)).scalar_one_or_none()
        if row is None:
            return {"available": False, "error": "incident not found", "incident_id": incident_id}
        return {"available": True, "incident": serialize(row)}


def explain(incident_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        inv = inventory_service.resolve(db)
        row = db.execute(select(AiIncident).where(AiIncident.id == incident_id,
                                                  AiIncident.inventory_id == inv.id)).scalar_one_or_none()
        if row is None:
            return {"available": False, "error": "incident not found", "incident_id": incident_id}
        current = serialize(row)
        result = dba_agent.explain_incident(current)
        row.ai_summary = result["summary"]
        row.rag_context = {
            "recommended_runbook_id": result["runbook_id"],
            "snippets": result["runbook_snippets"],
        }
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        return {"available": True, "incident": serialize(row), "explanation": result}


def set_status(incident_id: int, status: str) -> dict[str, Any]:
    with SessionLocal() as db:
        inv = inventory_service.resolve(db)
        row = db.execute(select(AiIncident).where(AiIncident.id == incident_id,
                                                  AiIncident.inventory_id == inv.id)).scalar_one_or_none()
        if row is None:
            return {"available": False, "error": "incident not found", "incident_id": incident_id}
        row.status = status
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        return {"available": True, "incident": serialize(row)}


def serialize(row: AiIncident) -> dict[str, Any]:
    return {
        "id": row.id,
        "inventory_id": row.inventory_id,
        "region": row.region,
        "dc": row.dc,
        "cluster_name": row.cluster_name,
        "severity": row.severity,
        "incident_type": row.incident_type,
        "title": row.title,
        "evidence": row.evidence,
        "rule_findings": row.rule_findings,
        "ml_findings": row.ml_findings,
        "forecast_findings": row.forecast_findings,
        "rag_context": row.rag_context,
        "ai_summary": row.ai_summary,
        "recommended_action": row.recommended_action,
        "confidence": row.confidence,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _log_findings() -> list[dict[str, Any]]:
    try:
        end = loki.now_ns()
        start = end - 6 * 3600 * loki.NS_PER_S
        return pg_log_analytics.findings(start, end).get("findings", [])
    except Exception:
        return []


def _title(risk: dict[str, Any]) -> str:
    return f"{risk['final_severity'].title()} {risk['primary_category'].replace('_', ' ')} risk on {S.CLUSTER_NAME}"


def _confidence(risk: dict[str, Any], rules: dict[str, Any], ml_score: dict[str, Any]) -> float:
    score = 0.45
    if rules.get("findings"):
        score += 0.25
    if ml_score.get("available"):
        score += 0.15
    if risk.get("reasons"):
        score += 0.1
    return min(0.95, round(score, 2))
