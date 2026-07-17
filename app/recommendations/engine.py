"""Read-only AI DBA recommendation engine.

Phase 1 uses deterministic rules over live PostgreSQL evidence. It persists the
result in metadata tables so future ML ranking, feedback learning, and vector
assistant retrieval can build on the same contract.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from sqlalchemy import delete

from .. import pg_perf, sources as S
from ..db.models import (
    AiDbaModelRun,
    AiDbaRecommendation,
    AiDbaRecommendationEvidence,
    AiDbaRecommendationFeedback,
    AiSqlFingerprint,
    Base,
)
from ..db.session import SessionLocal, engine
from . import parameter_advisor

TERMINAL_STATUSES = {"accepted", "applied", "closed", "rejected", "resolved"}
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "low": 3}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fingerprint(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return sha256(raw.encode("utf-8")).hexdigest()[:40]


def _quote_ident(value: str | None) -> str:
    return '"' + str(value or "").replace('"', '""') + '"'


def _ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {"total": len(rows), "critical": 0, "warning": 0, "info": 0, "low": 0, "open": 0}
    for row in rows:
        sev = str(row.get("severity") or "info")
        out[sev] = out.get(sev, 0) + 1
        if row.get("status", "open") == "open":
            out["open"] += 1
    return out


def _candidate_api(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate.get("id"),
        "recommendation_id": candidate.get("id"),
        "cluster_id": candidate.get("cluster_id"),
        "cluster_name": candidate.get("cluster_name"),
        "database_name": candidate.get("database_name"),
        "schema_name": candidate.get("schema_name"),
        "object_name": candidate.get("object_name"),
        "object_type": candidate.get("object_type"),
        "category": candidate.get("category"),
        "recommendation_type": candidate.get("recommendation_type"),
        "title": candidate.get("title"),
        "summary": candidate.get("summary"),
        "rationale": candidate.get("rationale"),
        "severity": candidate.get("severity", "info"),
        "confidence": candidate.get("confidence"),
        "impact": candidate.get("impact"),
        "effort": candidate.get("effort"),
        "risk_level": candidate.get("risk_level", "dba_approval"),
        "approval_required": candidate.get("approval_required", True),
        "status": candidate.get("status", "open"),
        "fingerprint": candidate.get("fingerprint"),
        "action_sql": candidate.get("action_sql"),
        "action_payload": candidate.get("action_payload"),
        "action_preview": candidate.get("action_sql") or (candidate.get("action_payload") or {}).get("preview"),
        "evidence": candidate.get("evidence") or [],
        "source": candidate.get("source"),
        "generated_by": candidate.get("generated_by", "ai-dba-rule-engine"),
        "model_version": candidate.get("model_version", "v1"),
        "created_at": candidate.get("created_at"),
        "updated_at": candidate.get("updated_at"),
    }


def _model_api(rec: AiDbaRecommendation) -> dict[str, Any]:
    created = rec.created_at.isoformat() if rec.created_at else None
    updated = rec.updated_at.isoformat() if rec.updated_at else None
    return _candidate_api({
        "id": rec.id,
        "cluster_id": rec.cluster_id,
        "cluster_name": rec.cluster_name,
        "database_name": rec.database_name,
        "schema_name": rec.schema_name,
        "object_name": rec.object_name,
        "object_type": rec.object_type,
        "category": rec.category,
        "recommendation_type": rec.recommendation_type,
        "title": rec.title,
        "summary": rec.summary,
        "rationale": rec.rationale,
        "severity": rec.severity,
        "confidence": rec.confidence,
        "impact": rec.impact,
        "effort": rec.effort,
        "risk_level": rec.risk_level,
        "approval_required": rec.approval_required,
        "status": rec.status,
        "fingerprint": rec.fingerprint,
        "action_sql": rec.action_sql,
        "action_payload": rec.action_payload,
        "evidence": rec.evidence,
        "source": rec.source,
        "generated_by": rec.generated_by,
        "model_version": rec.model_version,
        "created_at": created,
        "updated_at": updated,
    })


def _add_parameter_candidates(out: list[dict[str, Any]], cluster_id: str, ram_gib: float | None,
                              cpu_cores: float | None, source_status: dict[str, Any]) -> None:
    payload = parameter_advisor.build_response(ram_gib=ram_gib, cpu_cores=cpu_cores)
    source_status["parameters"] = {"available": payload.get("available", False), "count": len(payload.get("recommendations") or [])}
    for rec in payload.get("recommendations") or []:
        if rec.get("status") != "advice":
            continue
        name = rec.get("name") or rec.get("parameter")
        out.append({
            "cluster_id": cluster_id,
            "cluster_name": S.CLUSTER_NAME,
            "category": "configuration",
            "recommendation_type": "parameter_tuning",
            "object_type": "pg_setting",
            "object_name": name,
            "title": f"Tune PostgreSQL parameter {name}",
            "summary": f"Current value {rec.get('current')} differs from recommended value {rec.get('recommended')}.",
            "rationale": rec.get("rationale"),
            "severity": "warning" if rec.get("apply") == "restart" else "info",
            "confidence": rec.get("confidence", 0.70),
            "impact": "medium",
            "effort": "medium" if rec.get("apply") == "restart" else "low",
            "risk_level": "dba_approval",
            "approval_required": True,
            "status": "open",
            "fingerprint": _fingerprint(cluster_id, "parameter", name, rec.get("recommended")),
            "action_payload": {
                "parameter": name,
                "recommended": rec.get("recommended"),
                "current": rec.get("current"),
                "apply": rec.get("apply"),
                "preview": f"Validate {name}={rec.get('recommended')} ({rec.get('apply')})",
            },
            "evidence": [{
                "source_type": "postgres_catalog",
                "source_name": "pg_settings",
                "metric_name": name,
                "metric_value": str(rec.get("current")),
                "evidence_text": rec.get("rationale"),
                "evidence_json": rec,
            }],
            "source": payload.get("source"),
        })


def _add_index_candidates(out: list[dict[str, Any]], cluster_id: str, limit: int,
                          source_status: dict[str, Any]) -> None:
    try:
        payload = pg_perf.index_advisor(limit=min(limit, 200))
    except S.SourceError as exc:
        source_status["indexes"] = {"available": False, "error": str(exc)}
        return
    rows = payload.get("recommendations") or []
    source_status["indexes"] = {"available": payload.get("available", False), "count": len(rows)}
    for row in rows:
        size = _safe_int(row.get("size_bytes"))
        schema = row.get("schema_name") or row.get("schemaname")
        table = row.get("table_name") or row.get("relname")
        index = row.get("index_name") or row.get("indexrelname")
        rec_code = row.get("recommendation") or ("review_unused_large" if size >= 256 * 1024 * 1024 else "review_unused")
        severity = "warning" if rec_code == "review_unused_large" else "info"
        action_sql = f"DROP INDEX CONCURRENTLY IF EXISTS {_quote_ident(schema)}.{_quote_ident(index)};"
        out.append({
            "cluster_id": cluster_id,
            "cluster_name": S.CLUSTER_NAME,
            "schema_name": schema,
            "object_name": index,
            "object_type": "index",
            "category": "index",
            "recommendation_type": rec_code,
            "title": f"Review unused index {index}",
            "summary": f"Index {schema}.{index} has zero recorded scans and uses {size} bytes.",
            "rationale": "Zero-scan indexes add write overhead and consume storage. Validate with workload history before dropping.",
            "severity": severity,
            "confidence": 0.62,
            "impact": "medium" if severity == "warning" else "low",
            "effort": "medium",
            "risk_level": "dba_approval",
            "approval_required": True,
            "status": "open",
            "fingerprint": _fingerprint(cluster_id, "index", schema, table, index, rec_code),
            "action_sql": action_sql,
            "action_payload": {"schema": schema, "table": table, "index": index, "dry_run_only": True},
            "evidence": [{
                "source_type": "postgres_catalog",
                "source_name": "pg_stat_user_indexes",
                "metric_name": "idx_scan",
                "metric_value": str(row.get("idx_scan", 0)),
                "evidence_text": f"size_bytes={size}; unique={row.get('is_unique')}; primary={row.get('is_primary')}",
                "evidence_json": row,
            }],
            "source": payload.get("source"),
        })


def _add_bloat_candidates(out: list[dict[str, Any]], cluster_id: str, limit: int,
                          source_status: dict[str, Any]) -> None:
    try:
        payload = pg_perf.bloat(limit=min(limit, 100))
    except S.SourceError as exc:
        source_status["bloat"] = {"available": False, "error": str(exc)}
        return
    rows = payload.get("bloat") or []
    source_status["bloat"] = {"available": payload.get("available", False), "count": len(rows)}
    for row in rows:
        pct = _safe_float(row.get("dead_tuple_percent"), 0.0) or 0.0
        size = _safe_int(row.get("size_bytes") or row.get("total_size_bytes") or row.get("table_size_bytes"))
        if pct < 20 or size < 100 * 1024 * 1024:
            continue
        schema = row.get("schema_name") or row.get("schemaname") or "public"
        table = row.get("table_name") or row.get("relname")
        out.append({
            "cluster_id": cluster_id,
            "cluster_name": S.CLUSTER_NAME,
            "schema_name": schema,
            "object_name": table,
            "object_type": "table",
            "category": "maintenance",
            "recommendation_type": "vacuum_analyze_review",
            "title": f"Review table bloat on {schema}.{table}",
            "summary": f"Dead tuple ratio is {pct:.1f}% on a table with {size} bytes allocated.",
            "rationale": "High dead tuple ratio can increase scan cost and storage usage. Validate autovacuum settings before manual maintenance.",
            "severity": "warning" if pct < 40 else "critical",
            "confidence": 0.67,
            "impact": "medium",
            "effort": "medium",
            "risk_level": "dba_approval",
            "approval_required": True,
            "status": "open",
            "fingerprint": _fingerprint(cluster_id, "bloat", schema, table),
            "action_sql": f"VACUUM (ANALYZE) {_quote_ident(schema)}.{_quote_ident(table)};",
            "action_payload": {"schema": schema, "table": table, "dead_tuple_percent": pct, "dry_run_only": True},
            "evidence": [{
                "source_type": "postgres_catalog",
                "source_name": "pg_stat_user_tables",
                "metric_name": "dead_tuple_percent",
                "metric_value": f"{pct:.2f}",
                "evidence_text": f"dead_tuples={row.get('dead_tuples')}; size_bytes={size}",
                "evidence_json": row,
            }],
            "source": payload.get("source"),
        })


def _add_sql_candidates(out: list[dict[str, Any]], cluster_id: str, limit: int,
                        source_status: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        payload = pg_perf.topsql(sort="total", limit=min(limit, 75))
    except S.SourceError as exc:
        source_status["topsql"] = {"available": False, "error": str(exc)}
        return []
    rows = payload.get("top_sql") or []
    source_status["topsql"] = {"available": payload.get("available", False), "count": len(rows), "reason": payload.get("reason")}
    fingerprints: list[dict[str, Any]] = []
    for row in rows:
        fingerprints.append(row)
        mean_ms = _safe_float(row.get("mean_exec_ms"), 0.0) or 0.0
        total_ms = _safe_float(row.get("total_exec_ms"), 0.0) or 0.0
        calls = _safe_int(row.get("calls"))
        cache_hit = _safe_float(row.get("cache_hit_pct"))
        if mean_ms < 500 and total_ms < 60000 and not (cache_hit is not None and cache_hit < 90 and total_ms > 10000):
            continue
        queryid = row.get("queryid")
        severity = "warning" if mean_ms >= 1000 or total_ms >= 300000 else "info"
        out.append({
            "cluster_id": cluster_id,
            "cluster_name": S.CLUSTER_NAME,
            "object_name": str(queryid),
            "object_type": "sql_fingerprint",
            "category": "query",
            "recommendation_type": "sql_plan_review",
            "title": f"Review high-impact SQL fingerprint {queryid}",
            "summary": f"Mean execution is {mean_ms:.1f} ms across {calls} calls; total time is {total_ms:.1f} ms.",
            "rationale": "High-impact SQL should be checked for plan stability, missing indexes, stale statistics, and excessive IO.",
            "severity": severity,
            "confidence": 0.60,
            "impact": "high" if severity == "warning" else "medium",
            "effort": "medium",
            "risk_level": "safe_read_only",
            "approval_required": False,
            "status": "open",
            "fingerprint": _fingerprint(cluster_id, "topsql", queryid),
            "action_payload": {"queryid": queryid, "preview": "Capture plan and compare index/statistics options"},
            "evidence": [{
                "source_type": "postgres_extension",
                "source_name": "pg_stat_statements",
                "metric_name": "mean_exec_ms",
                "metric_value": f"{mean_ms:.3f}",
                "evidence_text": str(row.get("query") or "")[:500],
                "evidence_json": row,
            }],
            "source": payload.get("source"),
        })
    return fingerprints


def _add_capacity_candidates(out: list[dict[str, Any]], cluster_id: str, source_status: dict[str, Any]) -> None:
    try:
        row = S.sql_one(
            "select count(*), count(*) filter (where state='active'), "
            "count(*) filter (where state='idle in transaction'), "
            "(select setting::int from pg_settings where name='max_connections') from pg_stat_activity"
        ) or ["0", "0", "0", "0"]
    except S.SourceError as exc:
        source_status["capacity"] = {"available": False, "error": str(exc)}
        return
    total = _safe_int(row[0])
    active = _safe_int(row[1])
    idle_xact = _safe_int(row[2])
    max_conn = max(1, _safe_int(row[3], 1))
    pct = round(100.0 * total / max_conn, 1)
    source_status["capacity"] = {"available": True, "connections": total, "max_connections": max_conn, "usage_percent": pct}
    if pct >= 70:
        out.append({
            "cluster_id": cluster_id,
            "cluster_name": S.CLUSTER_NAME,
            "category": "capacity",
            "recommendation_type": "connection_capacity_review",
            "object_type": "cluster",
            "object_name": S.CLUSTER_NAME,
            "title": "Review connection capacity and pooling",
            "summary": f"Connections are at {pct:.1f}% of max_connections ({total}/{max_conn}).",
            "rationale": "High connection saturation can cause rejected sessions and memory pressure. Prefer pooling and workload cleanup before increasing max_connections.",
            "severity": "critical" if pct >= 90 else "warning",
            "confidence": 0.82,
            "impact": "high",
            "effort": "medium",
            "risk_level": "dba_approval",
            "approval_required": True,
            "status": "open",
            "fingerprint": _fingerprint(cluster_id, "capacity", "connections"),
            "action_payload": {"connections": total, "active": active, "idle_in_transaction": idle_xact, "max_connections": max_conn},
            "evidence": [{
                "source_type": "postgres_catalog",
                "source_name": "pg_stat_activity + pg_settings",
                "metric_name": "connection_usage_percent",
                "metric_value": f"{pct:.1f}",
                "evidence_json": {"connections": total, "active": active, "idle_in_transaction": idle_xact, "max_connections": max_conn},
            }],
            "source": "pg_stat_activity",
        })


def generate_candidates(cluster_id: str, ram_gib: float | None = None, cpu_cores: float | None = None,
                        limit: int = 200) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    source_status: dict[str, Any] = {}
    candidates: list[dict[str, Any]] = []
    _add_parameter_candidates(candidates, cluster_id, ram_gib, cpu_cores, source_status)
    _add_index_candidates(candidates, cluster_id, limit, source_status)
    _add_bloat_candidates(candidates, cluster_id, limit, source_status)
    fingerprints = _add_sql_candidates(candidates, cluster_id, limit, source_status)
    _add_capacity_candidates(candidates, cluster_id, source_status)
    candidates.sort(key=lambda r: (SEVERITY_ORDER.get(str(r.get("severity") or "info"), 9), str(r.get("category") or ""), str(r.get("title") or "")))
    return candidates[:limit], source_status, fingerprints


def _upsert_sql_fingerprints(db: Any, cluster_id: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        queryid = str(row.get("queryid") or "")
        if not queryid:
            continue
        existing = db.query(AiSqlFingerprint).filter(
            AiSqlFingerprint.cluster_id == cluster_id,
            AiSqlFingerprint.queryid == queryid,
        ).one_or_none()
        if existing is None:
            existing = AiSqlFingerprint(cluster_id=cluster_id, cluster_name=S.CLUSTER_NAME, queryid=queryid, first_seen_at=_now())
            db.add(existing)
        existing.normalized_query = row.get("query")
        existing.calls = _safe_int(row.get("calls"))
        existing.mean_exec_ms = _safe_float(row.get("mean_exec_ms"))
        existing.total_exec_ms = _safe_float(row.get("total_exec_ms"))
        existing.rows_returned = _safe_int(row.get("rows"))
        existing.cache_hit_pct = _safe_float(row.get("cache_hit_pct"))
        existing.last_seen_at = _now()
        existing.extra = row


def _persist_candidate(db: Any, candidate: dict[str, Any], run_id: int) -> AiDbaRecommendation:
    existing = db.query(AiDbaRecommendation).filter(
        AiDbaRecommendation.cluster_id == candidate.get("cluster_id"),
        AiDbaRecommendation.fingerprint == candidate["fingerprint"],
    ).one_or_none()
    if existing is None:
        existing = AiDbaRecommendation(fingerprint=candidate["fingerprint"], created_at=_now())
        db.add(existing)
    for field in [
        "cluster_id", "cluster_name", "database_name", "schema_name", "object_name", "object_type",
        "category", "recommendation_type", "title", "summary", "rationale", "severity", "confidence",
        "impact", "effort", "risk_level", "approval_required", "action_sql", "action_payload", "evidence", "source",
    ]:
        if field in candidate:
            setattr(existing, field, candidate.get(field))
    if existing.status not in TERMINAL_STATUSES:
        existing.status = "open"
    existing.generated_by = candidate.get("generated_by") or "ai-dba-rule-engine"
    existing.model_version = candidate.get("model_version") or "v1"
    existing.model_run_id = run_id
    existing.updated_at = _now()
    db.flush()
    db.execute(delete(AiDbaRecommendationEvidence).where(AiDbaRecommendationEvidence.recommendation_id == existing.id))
    for ev in candidate.get("evidence") or []:
        db.add(AiDbaRecommendationEvidence(
            recommendation_id=existing.id,
            source_type=ev.get("source_type"),
            source_name=ev.get("source_name"),
            metric_name=ev.get("metric_name"),
            metric_value=ev.get("metric_value"),
            evidence_text=ev.get("evidence_text"),
            evidence_json=ev.get("evidence_json"),
        ))
    return existing


def run_recommendations(cluster_id: str, ram_gib: float | None = None, cpu_cores: float | None = None,
                        limit: int = 200) -> dict[str, Any]:
    started = _now()
    candidates, source_status, fingerprints = generate_candidates(cluster_id, ram_gib=ram_gib, cpu_cores=cpu_cores, limit=limit)
    try:
        _ensure_schema()
        with SessionLocal() as db:
            run = AiDbaModelRun(
                cluster_id=cluster_id,
                cluster_name=S.CLUSTER_NAME,
                run_type="recommendation",
                model_name="ai-dba-rule-engine",
                model_version="v1",
                status="running",
                started_at=started,
                rows_analyzed=sum((v.get("count") or 0) for v in source_status.values() if isinstance(v, dict)),
                run_metadata={"sources": source_status},
            )
            db.add(run)
            db.flush()
            _upsert_sql_fingerprints(db, cluster_id, fingerprints)
            rows = [_persist_candidate(db, cand, run.id) for cand in candidates]
            run.status = "succeeded"
            run.finished_at = _now()
            run.recommendations_created = len(rows)
            db.commit()
            api_rows = [_model_api(row) for row in rows]
    except Exception as exc:
        return {
            "available": False,
            "metadata_available": False,
            "error": str(exc),
            "source": "live PostgreSQL + ai-dba-rule-engine",
            "source_status": source_status,
            "generated": len(candidates),
            "summary": _summary(candidates),
            "recommendations": [_candidate_api(c) for c in candidates],
        }

    return {
        "available": True,
        "metadata_available": True,
        "source": "live PostgreSQL + ai_dba_recommendations",
        "source_status": source_status,
        "generated": len(api_rows),
        "summary": _summary(api_rows),
        "recommendations": api_rows,
    }


def list_recommendations(cluster_id: str, status: str | None = "open", category: str | None = None,
                         limit: int = 100) -> dict[str, Any]:
    try:
        _ensure_schema()
        with SessionLocal() as db:
            query = db.query(AiDbaRecommendation).filter(AiDbaRecommendation.cluster_id == cluster_id)
            if status and status != "all":
                query = query.filter(AiDbaRecommendation.status == status)
            if category:
                query = query.filter(AiDbaRecommendation.category == category)
            rows = query.order_by(AiDbaRecommendation.updated_at.desc(), AiDbaRecommendation.id.desc()).limit(max(1, min(limit, 500))).all()
            api_rows = [_model_api(row) for row in rows]
    except Exception as exc:
        return {"available": False, "metadata_available": False, "error": str(exc), "recommendations": [], "summary": {"total": 0}}
    api_rows.sort(key=lambda r: (SEVERITY_ORDER.get(str(r.get("severity") or "info"), 9), str(r.get("category") or ""), str(r.get("title") or "")))
    return {"available": True, "metadata_available": True, "source": "ai_dba_recommendations", "summary": _summary(api_rows), "recommendations": api_rows}


def get_recommendation(cluster_id: str, recommendation_id: int) -> dict[str, Any]:
    _ensure_schema()
    with SessionLocal() as db:
        rec = db.query(AiDbaRecommendation).filter(
            AiDbaRecommendation.cluster_id == cluster_id,
            AiDbaRecommendation.id == int(recommendation_id),
        ).one_or_none()
        if rec is None:
            return {"available": False, "error": "recommendation not found", "id": recommendation_id}
        evidence = db.query(AiDbaRecommendationEvidence).filter(
            AiDbaRecommendationEvidence.recommendation_id == rec.id
        ).order_by(AiDbaRecommendationEvidence.id.asc()).all()
        feedback = db.query(AiDbaRecommendationFeedback).filter(
            AiDbaRecommendationFeedback.recommendation_id == rec.id
        ).order_by(AiDbaRecommendationFeedback.id.desc()).limit(50).all()
        payload = _model_api(rec)
        payload["evidence_rows"] = [{
            "id": ev.id,
            "source_type": ev.source_type,
            "source_name": ev.source_name,
            "metric_name": ev.metric_name,
            "metric_value": ev.metric_value,
            "evidence_text": ev.evidence_text,
            "evidence_json": ev.evidence_json,
            "collected_at": ev.collected_at.isoformat() if ev.collected_at else None,
        } for ev in evidence]
        payload["feedback"] = [{
            "id": fb.id,
            "user_email": fb.user_email,
            "vote": fb.vote,
            "status": fb.status,
            "comment": fb.comment,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
        } for fb in feedback]
        return {"available": True, "recommendation": payload}


def add_feedback(cluster_id: str, recommendation_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_schema()
    with SessionLocal() as db:
        rec = db.query(AiDbaRecommendation).filter(
            AiDbaRecommendation.cluster_id == cluster_id,
            AiDbaRecommendation.id == int(recommendation_id),
        ).one_or_none()
        if rec is None:
            return {"ok": False, "error": "recommendation not found", "id": recommendation_id}
        status = payload.get("status")
        if status:
            rec.status = str(status)
        fb = AiDbaRecommendationFeedback(
            recommendation_id=rec.id,
            user_email=payload.get("user_email") or payload.get("user") or payload.get("email"),
            vote=payload.get("vote"),
            status=status,
            comment=payload.get("comment"),
        )
        db.add(fb)
        rec.updated_at = _now()
        db.commit()
        return {"ok": True, "id": rec.id, "status": rec.status}
