"""Agentic AI recommendation orchestrator."""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

from fastapi import HTTPException
from sqlalchemy import inspect, select, text

from .. import sources as S
from ..ai import rag_retriever
from ..db.models import AiActionAudit, AiAgentRecommendation, AiAgentRun, Base
from ..db.session import SessionLocal, engine
from ..security import Principal
from . import ai_agent_collectors, ai_agent_email, ai_agent_executor, ai_provider


_RUN_LOCK = threading.Lock()
_TRUE_VALUES = {"1", "true", "yes", "on"}
_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
_VALID_CATEGORIES = {
    "ALL",
    "PERFORMANCE",
    "INDEX",
    "ERROR",
    "BACKUP",
    "REPLICATION",
    "PATRONI",
    "CONNECTION",
    "STORAGE",
    "VACUUM",
    "SECURITY",
    "OTHER",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _quote_ident(value: str | None) -> str:
    return '"' + str(value or "").replace('"', '""') + '"'


def _fingerprint(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def _schema_columns(table: str) -> set[str]:
    try:
        insp = inspect(engine)
        if not insp.has_table(table):
            return set()
        return {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return set()


def _ddl_type(column: str) -> str:
    if column == "recommendation_id":
        return "INTEGER"
    if column in {"execution_started_at", "execution_finished_at"}:
        return "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "TIMESTAMP"
    return "TEXT"


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)
    existing = _schema_columns("ai_action_audit")
    if not existing:
        return
    missing = [
        c
        for c in ("recommendation_id", "execution_started_at", "execution_finished_at", "execution_output", "error_message")
        if c not in existing
    ]
    if not missing:
        return
    with engine.begin() as conn:
        for column in missing:
            if engine.dialect.name == "postgresql":
                conn.execute(text(f"ALTER TABLE ai_action_audit ADD COLUMN IF NOT EXISTS {column} {_ddl_type(column)}"))
            else:
                conn.execute(text(f"ALTER TABLE ai_action_audit ADD COLUMN {column} {_ddl_type(column)}"))


def is_running() -> bool:
    return _RUN_LOCK.locked()


def _run_api(row: AiAgentRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.id,
        "agent_name": row.agent_name,
        "trigger_type": row.trigger_type,
        "triggered_by": row.triggered_by,
        "cluster_name": row.cluster_name,
        "database_name": row.database_name,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "status": row.status,
        "summary": row.summary,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _audit_api(row: AiActionAudit) -> dict[str, Any]:
    return {
        "id": row.id,
        "action_id": row.id,
        "recommendation_id": getattr(row, "recommendation_id", None),
        "incident_id": row.incident_id,
        "action_type": row.action_type,
        "requested_by": row.requested_by,
        "approved_by": row.approved_by,
        "executed_by": row.executed_by,
        "execution_started_at": row.execution_started_at.isoformat() if getattr(row, "execution_started_at", None) else None,
        "execution_finished_at": row.execution_finished_at.isoformat() if getattr(row, "execution_finished_at", None) else None,
        "execution_status": row.execution_status,
        "execution_output": getattr(row, "execution_output", None) or row.output,
        "error_message": getattr(row, "error_message", None),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _rec_api(row: AiAgentRecommendation, audit: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    confidence = row.confidence_score
    if isinstance(confidence, Decimal):
        confidence = float(confidence)
    return {
        "id": row.id,
        "recommendation_id": row.id,
        "run_id": row.run_id,
        "severity": row.severity,
        "category": row.category,
        "cluster_name": row.cluster_name,
        "region_name": row.region_name,
        "dc_name": row.dc_name,
        "database_name": row.database_name,
        "object_name": row.object_name,
        "finding": row.finding,
        "evidence": row.evidence,
        "root_cause": row.root_cause,
        "recommendation": row.recommendation,
        "recommended_sql": row.recommended_sql,
        "rollback_sql": row.rollback_sql,
        "risk_level": row.risk_level,
        "confidence_score": confidence,
        "approval_status": row.approval_status,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "execution_status": row.execution_status,
        "execution_output": row.execution_output,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "audit_history": audit or [],
    }


def _inventory_context() -> dict[str, Any]:
    return {
        "cluster_name": S.CLUSTER_NAME,
        "namespace": S.NS,
        "region_name": os.environ.get("REGION_NAME") or os.environ.get("PGC_REGION") or "uae",
        "dc_name": os.environ.get("DC_NAME") or os.environ.get("PGC_DC") or "dc1",
    }


def _base_rec(category: str, severity: str, finding: str, evidence: dict[str, Any],
              recommendation: str, root_cause: str, risk_level: str = "LOW",
              confidence: float = 0.55, object_name: str | None = None,
              database_name: str | None = None, recommended_sql: str | None = None,
              rollback_sql: str | None = None) -> dict[str, Any]:
    ctx = _inventory_context()
    return {
        "severity": severity,
        "category": category,
        "cluster_name": ctx["cluster_name"],
        "region_name": ctx["region_name"],
        "dc_name": ctx["dc_name"],
        "database_name": database_name,
        "object_name": object_name,
        "finding": finding,
        "evidence": evidence,
        "root_cause": root_cause,
        "recommendation": recommendation,
        "recommended_sql": recommended_sql,
        "rollback_sql": rollback_sql,
        "risk_level": risk_level,
        "confidence_score": round(max(0.0, min(1.0, confidence)), 2),
        "approval_status": "PENDING",
        "execution_status": None,
    }


def _rag_hits(query: str) -> list[dict[str, Any]]:
    try:
        hits = rag_retriever.retrieve(query=query, limit=3)
        return [
            {
                "runbook_id": h.get("runbook_id"),
                "title": h.get("title"),
                "method": h.get("method"),
                "score": h.get("score"),
                "source_file": h.get("source_file"),
            }
            for h in hits
        ]
    except Exception as exc:
        detail = str(exc).splitlines()[0][:240]
        return [{"error": f"knowledge base retrieval unavailable: {detail}", "method": "unavailable"}]


def _metric_value(evidence: dict[str, Any], name: str) -> float | None:
    metrics = ((evidence.get("metrics") or {}).get("values") or {})
    pg_values = (((evidence.get("postgres") or {}).get("postgres_snapshot") or {}).get("values") or {})
    if metrics.get(name) is not None:
        return _to_float(metrics.get(name))
    return _to_float(pg_values.get(name))


def _append_sop(rec: dict[str, Any]) -> dict[str, Any]:
    query = " ".join(str(rec.get(k) or "") for k in ("category", "finding", "root_cause", "recommendation"))
    ev = dict(rec.get("evidence") or {})
    ev["matched_sops"] = _rag_hits(query[:1000])
    provider = ai_provider.provider_status()
    ev["ai_provider"] = provider
    if not provider.get("configured"):
        ev["ai_provider_fallback"] = "AI provider disabled; recommendation generated by deterministic rules."
    rec["evidence"] = ev
    return rec


def _metric_recommendations(evidence: dict[str, Any], database_name: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    conn_warn = _env_float("AI_AGENT_CONNECTION_USAGE_WARN", 80)
    disk_warn = _env_float("AI_AGENT_DISK_USAGE_WARN", 85)
    disk_crit = _env_float("AI_AGENT_DISK_USAGE_CRITICAL", 95)
    repl_warn = _env_float("AI_AGENT_REPLICATION_LAG_WARN_SECONDS", 300)

    conn = _metric_value(evidence, "active_connections_percent")
    if conn is not None and conn >= conn_warn:
        severity = "CRITICAL" if conn >= 95 else "HIGH"
        out.append(_base_rec(
            "CONNECTION",
            severity,
            f"PostgreSQL connection usage is {conn:.1f}% of max_connections.",
            {"metric": "active_connections_percent", "value": conn, "threshold": conn_warn, "source": "pg_stat_activity/prometheus"},
            "Review application pooling, idle sessions, and max_connections headroom. Prefer PgBouncer/workload cleanup before increasing max_connections.",
            "Connection saturation can reject new sessions and increase memory pressure.",
            risk_level="MEDIUM",
            confidence=0.82,
            object_name=S.CLUSTER_NAME,
            database_name=database_name,
        ))

    for metric_name, label in (("pgdata_pvc_used_percent", "PGDATA PVC"), ("wal_pvc_used_percent", "WAL PVC")):
        pct = _metric_value(evidence, metric_name)
        if pct is not None and pct >= disk_warn:
            severity = "CRITICAL" if pct >= disk_crit else "HIGH"
            out.append(_base_rec(
                "STORAGE",
                severity,
                f"{label} usage is {pct:.1f}%.",
                {"metric": metric_name, "value": pct, "warning_threshold": disk_warn, "critical_threshold": disk_crit},
                "Increase storage through the approved OpenShift/PVC workflow or reduce retained WAL/bloat after DBA review.",
                "Storage pressure is above the configured threshold and can lead to write stalls or outage if exhausted.",
                risk_level="HIGH" if severity == "CRITICAL" else "MEDIUM",
                confidence=0.80,
                object_name=label,
                database_name=database_name,
            ))

    lag = _metric_value(evidence, "replication_lag_seconds")
    if lag is not None and lag >= repl_warn:
        out.append(_base_rec(
            "REPLICATION",
            "HIGH",
            f"Replication lag is {lag:.0f} seconds.",
            {"metric": "replication_lag_seconds", "value": lag, "threshold": repl_warn},
            "Check replica replay, WAL sender health, network saturation, and long-running transactions before any switchover.",
            "Replica replay is behind the primary beyond the configured warning threshold.",
            risk_level="MEDIUM",
            confidence=0.78,
            object_name=S.CLUSTER_NAME,
            database_name=database_name,
        ))

    archive_failed = _metric_value(evidence, "archive_failed_count")
    if archive_failed is not None and archive_failed > 0:
        out.append(_base_rec(
            "BACKUP",
            "HIGH",
            f"WAL archive failures detected: {archive_failed:.0f}.",
            {"metric": "archive_failed_count", "value": archive_failed},
            "Review pgBackRest archive-push logs and repository connectivity. Do not proceed with restore/DR assumptions until archiving is healthy.",
            "PostgreSQL reports active archiver failures, which can break PITR and DR recovery objectives.",
            risk_level="HIGH",
            confidence=0.83,
            object_name="pg_stat_archiver",
            database_name=database_name,
        ))

    deadlocks = _metric_value(evidence, "deadlocks_per_min")
    if deadlocks is not None and deadlocks >= 1:
        out.append(_base_rec(
            "PERFORMANCE",
            "MEDIUM" if deadlocks < 5 else "HIGH",
            f"Deadlock rate is {deadlocks:.1f} per minute.",
            {"metric": "deadlocks_per_min", "value": deadlocks},
            "Identify conflicting transaction paths, enforce consistent locking order, and review retry behavior.",
            "Repeated deadlocks indicate application transaction contention.",
            risk_level="LOW",
            confidence=0.74,
            database_name=database_name,
        ))

    return out


def _loki_recommendations(evidence: dict[str, Any], database_name: str | None) -> list[dict[str, Any]]:
    loki_ev = evidence.get("loki") or {}
    patterns = loki_ev.get("patterns") or {}
    samples = loki_ev.get("samples") or []
    out: list[dict[str, Any]] = []

    def count(*names: str) -> int:
        return sum(_to_int(patterns.get(name)) for name in names)

    backup_count = count("archive command failed", "pgBackRest error")
    if backup_count > 0:
        out.append(_base_rec(
            "BACKUP",
            "HIGH",
            f"Backup/archive error patterns appeared {backup_count} times in the last {loki_ev.get('lookback_minutes', 30)} minutes.",
            {"patterns": patterns, "samples": samples[:10], "source": "loki"},
            "Review pgBackRest repository health, archive-push errors, object-store credentials, and network reachability.",
            "Recent logs contain backup or archive failure signatures.",
            risk_level="HIGH",
            confidence=0.76,
            database_name=database_name,
        ))

    oom_count = count("out of memory", "OOMKilled")
    if oom_count > 0:
        out.append(_base_rec(
            "PERFORMANCE",
            "HIGH",
            f"OOM or memory pressure patterns appeared {oom_count} times.",
            {"patterns": patterns, "samples": samples[:10], "source": "loki"},
            "Check pod memory limits, PostgreSQL memory settings, temp file growth, and recent query memory usage.",
            "Container or PostgreSQL logs indicate memory pressure.",
            risk_level="MEDIUM",
            confidence=0.72,
            database_name=database_name,
        ))

    conn_count = count("remaining connection slots are reserved")
    if conn_count > 0:
        out.append(_base_rec(
            "CONNECTION",
            "HIGH",
            f"Connection slot exhaustion appeared {conn_count} times in logs.",
            {"patterns": patterns, "samples": samples[:10], "source": "loki"},
            "Reduce idle sessions, verify PgBouncer pooling, and review connection storms before increasing server limits.",
            "PostgreSQL rejected sessions because reserved connection slots were reached.",
            risk_level="MEDIUM",
            confidence=0.78,
            database_name=database_name,
        ))

    timeout_count = count("canceling statement due to statement timeout", "temporary file")
    if timeout_count > 0:
        out.append(_base_rec(
            "PERFORMANCE",
            "MEDIUM",
            f"Query timeout/temp-file signatures appeared {timeout_count} times.",
            {"patterns": patterns, "samples": samples[:10], "source": "loki"},
            "Correlate with pg_stat_statements top SQL, inspect plans, and check work_mem/temp file behavior.",
            "Logs show statements timing out or spilling to temporary files.",
            risk_level="LOW",
            confidence=0.67,
            database_name=database_name,
        ))

    patroni_count = count("Patroni failover", "Patroni switchover", "replica lag")
    if patroni_count > 0:
        out.append(_base_rec(
            "PATRONI",
            "HIGH",
            f"Patroni HA/failover signatures appeared {patroni_count} times.",
            {"patterns": patterns, "samples": samples[:10], "source": "loki"},
            "Review Patroni member state, DCS health, timeline history, and replication lag before any HA operation.",
            "Recent logs include HA transition or replica lag patterns.",
            risk_level="HIGH",
            confidence=0.72,
            database_name=database_name,
        ))

    return out


def _extract_index_candidate(query: str) -> tuple[str, str, list[str]] | None:
    q = " ".join(str(query or "").replace('"', "").split())
    table_match = re.search(r"\bfrom\s+([a-zA-Z_][\w.]*)(?:\s+[a-zA-Z_][\w]*)?", q, flags=re.I)
    if not table_match:
        return None
    table_ref = table_match.group(1)
    if "." in table_ref:
        schema, table = table_ref.split(".", 1)
    else:
        schema, table = "public", table_ref
    where_match = re.search(r"\bwhere\s+(.+?)(\border\s+by\b|\bgroup\s+by\b|\blimit\b|$)", q, flags=re.I)
    if not where_match:
        return None
    where = where_match.group(1)
    cols = []
    for col in re.findall(r"(?:[a-zA-Z_][\w]*\.)?([a-zA-Z_][\w]*)\s*(?:=|>|<|>=|<=|\bin\b|\blike\b|\bbetween\b)", where, flags=re.I):
        if col.lower() not in {"and", "or", "not", "null", "true", "false"} and col not in cols:
            cols.append(col)
    if not cols:
        return None
    return schema, table, cols[:2]


def _has_existing_index(schema: str, table: str, columns: list[str]) -> bool:
    try:
        schema_lit = schema.replace("'", "''")
        table_lit = table.replace("'", "''")
        rows = S.sql(
            "select indexdef from pg_indexes "
            f"where schemaname = '{schema_lit}' and tablename = '{table_lit}'"
        )
    except Exception:
        return False
    wanted = [c.lower() for c in columns]
    for row in rows:
        indexdef = (row[0] if row else "").lower()
        if all(re.search(r"\b" + re.escape(col.lower()) + r"\b", indexdef) for col in wanted):
            return True
    return False


def _index_sql(schema: str, table: str, columns: list[str]) -> tuple[str, str, str]:
    suffix = "_".join(columns)[:40]
    index_name = f"idx_ai_{table}_{suffix}_{_fingerprint(schema, table, columns)[:6]}"
    qualified_table = f"{_quote_ident(schema)}.{_quote_ident(table)}"
    qualified_index = f"{_quote_ident(schema)}.{_quote_ident(index_name)}"
    cols = ", ".join(_quote_ident(c) for c in columns)
    create_sql = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_quote_ident(index_name)} ON {qualified_table} ({cols});"
    rollback_sql = f"DROP INDEX CONCURRENTLY IF EXISTS {qualified_index};"
    return index_name, create_sql, rollback_sql


def _sql_recommendations(evidence: dict[str, Any], database_name: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pg = evidence.get("postgres") or {}
    mean_warn = _env_float("AI_AGENT_QUERY_MEAN_TIME_WARN_MS", 1000)

    top_total = (((pg.get("top_sql_total") or {}).get("top_sql")) or [])[:20]
    top_mean = (((pg.get("top_sql_mean") or {}).get("top_sql")) or [])[:20]
    seen_query_ids: set[str] = set()
    for row in top_total + top_mean:
        queryid = str(row.get("queryid") or "")
        if queryid in seen_query_ids:
            continue
        seen_query_ids.add(queryid)
        mean_ms = _to_float(row.get("mean_exec_ms"), 0.0) or 0.0
        total_ms = _to_float(row.get("total_exec_ms"), 0.0) or 0.0
        calls = _to_int(row.get("calls"))
        if mean_ms >= mean_warn or total_ms >= mean_warn * max(50, calls * 0.1):
            out.append(_base_rec(
                "PERFORMANCE",
                "HIGH" if mean_ms >= mean_warn * 3 else "MEDIUM",
                f"SQL fingerprint {queryid} has mean execution {mean_ms:.1f} ms across {calls} calls.",
                {"source": "pg_stat_statements", "query": row.get("query"), "calls": calls, "mean_exec_ms": mean_ms, "total_exec_ms": total_ms},
                "Capture a plan during an approved diagnostic window, check statistics, joins, sort/hash spills, and candidate indexes.",
                "The statement is a top contributor by mean or total execution time.",
                risk_level="LOW",
                confidence=0.68,
                object_name=queryid,
                database_name=database_name,
                recommended_sql=f"EXPLAIN (FORMAT JSON) {row.get('query')}" if row.get("query") else None,
            ))

            candidate = _extract_index_candidate(row.get("query") or "")
            if candidate:
                schema, table, columns = candidate
                has_index = _has_existing_index(schema, table, columns)
                if not has_index:
                    index_name, create_sql, rollback_sql = _index_sql(schema, table, columns)
                    out.append(_base_rec(
                        "INDEX",
                        "MEDIUM",
                        f"Potential missing index for high-impact SQL fingerprint {queryid} on {schema}.{table} ({', '.join(columns)}).",
                        {
                            "source": "pg_stat_statements",
                            "queryid": queryid,
                            "query": row.get("query"),
                            "candidate_columns": columns,
                            "existing_index_match": False,
                            "write_heavy_warning": "Validate write overhead and table churn before creating the index.",
                        },
                        "Review the plan and create the concurrent index only after DBA approval if the predicate is stable and selective.",
                        "The query filters on columns that do not appear to be covered by an existing index.",
                        risk_level="MEDIUM",
                        confidence=0.52,
                        object_name=f"{schema}.{table}",
                        database_name=database_name,
                        recommended_sql=create_sql,
                        rollback_sql=rollback_sql,
                    ))

    blocking_rows = ((pg.get("blocking") or {}).get("rows") or [])
    if blocking_rows:
        out.append(_base_rec(
            "PERFORMANCE",
            "HIGH",
            f"{len(blocking_rows)} blocked session relationships were detected.",
            {"source": "pg_locks/pg_stat_activity", "rows": blocking_rows[:20]},
            "Identify the blocking backend and application transaction. Cancel only after DBA approval and application impact review.",
            "PostgreSQL lock waits show sessions blocked by other sessions.",
            risk_level="MEDIUM",
            confidence=0.81,
            database_name=database_name,
        ))

    for row in (((pg.get("bloat") or {}).get("bloat")) or [])[:20]:
        pct = _to_float(row.get("dead_tuple_percent"), 0.0) or 0.0
        size = _to_int(row.get("size_bytes") or row.get("total_size_bytes") or row.get("table_size_bytes"))
        if pct >= 20 and size >= 100 * 1024 * 1024:
            schema = row.get("schema_name") or row.get("schemaname") or "public"
            table = row.get("table_name") or row.get("relname")
            out.append(_base_rec(
                "VACUUM",
                "MEDIUM" if pct < 40 else "HIGH",
                f"Table {schema}.{table} has {pct:.1f}% dead tuples.",
                {"source": "pg_stat_user_tables", "row": row},
                "Review autovacuum settings and run ANALYZE after approval if statistics are stale. Avoid VACUUM FULL in this agent.",
                "Dead tuple ratio is high enough to affect scans and planner estimates.",
                risk_level="LOW",
                confidence=0.67,
                object_name=f"{schema}.{table}",
                database_name=database_name,
                recommended_sql=f"ANALYZE {_quote_ident(schema)}.{_quote_ident(table)};",
            ))

    for row in ((pg.get("sequential_scan_candidates") or {}).get("rows") or [])[:20]:
        schema, table, seq_scan, idx_scan, live_tup = row[:5]
        out.append(_base_rec(
            "INDEX",
            "LOW",
            f"Large table {schema}.{table} has high sequential scan count ({seq_scan}) compared with index scans ({idx_scan}).",
            {"source": "pg_stat_user_tables", "schema": schema, "table": table, "seq_scan": seq_scan, "idx_scan": idx_scan, "n_live_tup": live_tup},
            "Correlate with top SQL predicates before creating any index. This is a candidate signal, not direct DDL advice.",
            "The table is large and is scanned sequentially more often than indexed.",
            risk_level="LOW",
            confidence=0.43,
            object_name=f"{schema}.{table}",
            database_name=database_name,
        ))

    return out


def _dedupe(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for rec in recs:
        key = _fingerprint(rec.get("category"), rec.get("severity"), rec.get("object_name"), rec.get("finding"), rec.get("recommended_sql"))
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    out.sort(key=lambda r: (_SEVERITY_RANK.get(str(r.get("severity") or "INFO"), 9), str(r.get("category") or ""), str(r.get("finding") or "")))
    return out


def generate_recommendations(evidence: dict[str, Any], category: str = "ALL",
                             database_name: str | None = None) -> list[dict[str, Any]]:
    requested = str(category or "ALL").upper()
    recs: list[dict[str, Any]] = []
    recs.extend(_metric_recommendations(evidence, database_name))
    recs.extend(_loki_recommendations(evidence, database_name))
    recs.extend(_sql_recommendations(evidence, database_name))
    if requested != "ALL":
        recs = [r for r in recs if r.get("category") == requested]
    return [_append_sop(r) for r in _dedupe(recs)]


def _create_run(trigger_type: str, triggered_by: str | None, cluster_name: str | None,
                database_name: str | None) -> int:
    ensure_schema()
    with SessionLocal() as db:
        row = AiAgentRun(
            agent_name=os.environ.get("AI_AGENT_NAME") or "ai-dba-agent",
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            cluster_name=cluster_name or S.CLUSTER_NAME,
            database_name=database_name,
            started_at=_now(),
            status="RUNNING",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _persist_recommendations(run_id: int, recs: list[dict[str, Any]]) -> list[AiAgentRecommendation]:
    saved: list[AiAgentRecommendation] = []
    with SessionLocal() as db:
        for rec in recs:
            row = AiAgentRecommendation(
                run_id=run_id,
                severity=rec.get("severity") or "INFO",
                category=rec.get("category") or "OTHER",
                cluster_name=rec.get("cluster_name"),
                region_name=rec.get("region_name"),
                dc_name=rec.get("dc_name"),
                database_name=rec.get("database_name"),
                object_name=rec.get("object_name"),
                finding=rec.get("finding"),
                evidence=rec.get("evidence"),
                root_cause=rec.get("root_cause"),
                recommendation=rec.get("recommendation"),
                recommended_sql=rec.get("recommended_sql"),
                rollback_sql=rec.get("rollback_sql"),
                risk_level=rec.get("risk_level") or "LOW",
                confidence_score=rec.get("confidence_score"),
                approval_status=rec.get("approval_status") or "PENDING",
                execution_status=rec.get("execution_status"),
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(row)
            saved.append(row)
        db.commit()
        for row in saved:
            db.refresh(row)
        return saved


def _finish_run(run_id: int, status: str, summary: str | None = None, error: str | None = None) -> None:
    with SessionLocal() as db:
        row = db.get(AiAgentRun, run_id)
        if row:
            row.status = status
            row.summary = summary
            row.error_message = error
            row.finished_at = _now()
            db.commit()


def run_agent(payload: dict[str, Any] | None = None, trigger_type: str = "MANUAL",
              triggered_by: str | None = None, raise_on_overlap: bool = False) -> dict[str, Any]:
    payload = payload or {}
    if not _RUN_LOCK.acquire(blocking=False):
        if raise_on_overlap:
            raise HTTPException(status_code=409, detail="AI agent run already in progress")
        return {"available": True, "skipped": True, "status": "SKIPPED", "reason": "AI agent run already in progress"}
    run_id: int | None = None
    try:
        category = str(payload.get("category") or "ALL").upper()
        if category not in _VALID_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"unsupported category {category}")
        lookback = int(payload.get("lookback_minutes") or os.environ.get("AI_AGENT_LOOKBACK_MINUTES") or "30")
        lookback = max(5, min(lookback, 24 * 60))
        cluster_name = payload.get("cluster_name") or S.CLUSTER_NAME
        database_name = payload.get("database_name") or None
        default_actor = "scheduler" if trigger_type.upper() == "SCHEDULED" else "dba"
        # Caller identity is supplied by the trusted API boundary or scheduler,
        # never by free-form request JSON.
        actor = triggered_by or default_actor
        run_id = _create_run(trigger_type.upper(), str(actor), str(cluster_name), database_name)
        evidence = ai_agent_collectors.collect_all(str(cluster_name), database_name, lookback)
        recs = generate_recommendations(evidence, category=category, database_name=database_name)
        saved_rows = _persist_recommendations(run_id, recs)
        saved_api = [_rec_api(row) for row in saved_rows]
        email_result = ai_agent_email.notify_recommendations(saved_rows, approval_url=os.environ.get("AI_AGENT_APPROVAL_URL"))
        summary_obj = {
            "recommendations_created": len(saved_rows),
            "category": category,
            "lookback_minutes": lookback,
            "email": email_result,
            "provider": ai_provider.provider_status(),
            "sources": {
                "metrics": (evidence.get("metrics") or {}).get("available"),
                "loki": (evidence.get("loki") or {}).get("available"),
                "postgres": (evidence.get("postgres") or {}).get("available"),
                "patroni": (evidence.get("patroni") or {}).get("available"),
            },
        }
        _finish_run(run_id, "COMPLETED", summary=_json(summary_obj))
        return {
            "available": True,
            "run": get_run(run_id)["run"],
            "summary": summary_obj,
            "recommendations": saved_api,
        }
    except HTTPException:
        if run_id:
            _finish_run(run_id, "FAILED", error="invalid request")
        raise
    except Exception as exc:
        if run_id:
            _finish_run(run_id, "FAILED", error=str(exc))
            return {"available": False, "run": get_run(run_id).get("run"), "error": str(exc)}
        return {"available": False, "error": str(exc)}
    finally:
        _RUN_LOCK.release()


def list_runs(limit: int = 50) -> dict[str, Any]:
    ensure_schema()
    with SessionLocal() as db:
        rows = db.execute(
            select(AiAgentRun).where(AiAgentRun.cluster_name == S.CLUSTER_NAME)
            .order_by(AiAgentRun.started_at.desc(), AiAgentRun.id.desc()).limit(max(1, min(limit, 500)))
        ).scalars().all()
        return {"available": True, "runs": [_run_api(r) for r in rows], "count": len(rows), "running": is_running()}


def get_run(run_id: int) -> dict[str, Any]:
    ensure_schema()
    with SessionLocal() as db:
        row = db.execute(select(AiAgentRun).where(AiAgentRun.id == int(run_id),
                                                  AiAgentRun.cluster_name == S.CLUSTER_NAME)).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="AI agent run not found")
        recs = db.execute(
            select(AiAgentRecommendation).where(AiAgentRecommendation.run_id == row.id).order_by(AiAgentRecommendation.id.desc())
        ).scalars().all()
        return {"available": True, "run": _run_api(row), "recommendations": [_rec_api(r) for r in recs]}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def list_recommendations(filters: dict[str, Any] | None = None, limit: int = 100) -> dict[str, Any]:
    ensure_schema()
    filters = filters or {}
    # Callers filter by the short cluster_id (e.g. "uat"), but rows are stored
    # under the canonical cluster_name (e.g. "uat-pgcluster-uae"). Resolve so the
    # filter actually matches instead of silently returning nothing.
    requested = S.resolve_cluster_name(filters.get("cluster_name")) if filters.get("cluster_name") else S.CLUSTER_NAME
    if requested != S.CLUSTER_NAME:
        raise HTTPException(status_code=409, detail="recommendation cluster does not match active cluster")
    filters = {**filters, "cluster_name": S.CLUSTER_NAME}
    with SessionLocal() as db:
        query = select(AiAgentRecommendation)
        for name in ("severity", "category", "approval_status", "cluster_name", "database_name"):
            value = filters.get(name)
            if value and str(value).lower() not in {"all", "any"}:
                normalized = str(value).upper() if name in {"severity", "category", "approval_status"} else str(value)
                query = query.where(getattr(AiAgentRecommendation, name) == normalized)
        created_from = _parse_dt(filters.get("created_from"))
        created_to = _parse_dt(filters.get("created_to"))
        if created_from:
            query = query.where(AiAgentRecommendation.created_at >= created_from)
        if created_to:
            query = query.where(AiAgentRecommendation.created_at <= created_to)
        rows = db.execute(
            query.order_by(AiAgentRecommendation.created_at.desc(), AiAgentRecommendation.id.desc()).limit(max(1, min(limit, 500)))
        ).scalars().all()
        summary: dict[str, int] = {"total": len(rows)}
        for row in rows:
            summary[row.severity] = summary.get(row.severity, 0) + 1
            summary[row.approval_status] = summary.get(row.approval_status, 0) + 1
        return {"available": True, "recommendations": [_rec_api(r) for r in rows], "summary": summary, "count": len(rows)}


def get_recommendation(recommendation_id: int) -> dict[str, Any]:
    ensure_schema()
    with SessionLocal() as db:
        row = db.execute(select(AiAgentRecommendation).where(AiAgentRecommendation.id == int(recommendation_id),
            AiAgentRecommendation.cluster_name == S.CLUSTER_NAME)).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="recommendation not found")
        audit_rows = db.execute(
            select(AiActionAudit)
            .where(AiActionAudit.recommendation_id == row.id)
            .order_by(AiActionAudit.id.desc())
        ).scalars().all()
        return {"available": True, "recommendation": _rec_api(row, [_audit_api(a) for a in audit_rows])}


def _actor(principal: Principal) -> str:
    return principal.subject_id


def _audit(recommendation_id: int, action_type: str, status: str, actor: str,
           approved_by: str | None = None, sql: str | None = None,
           output: Any = None, error: str | None = None,
           started_at: datetime | None = None) -> AiActionAudit:
    row = AiActionAudit(
        recommendation_id=recommendation_id,
        action_level="L3",
        action_type=action_type,
        command_preview=sql,
        requested_by=actor,
        approved_by=approved_by,
        executed_by=actor if action_type == "execute" else None,
        execution_started_at=started_at,
        execution_finished_at=_now() if started_at else None,
        execution_status=status,
        execution_output=_json(output) if output is not None else None,
        error_message=error,
        output=_json({"event": action_type, "status": status, "actor": actor, "output": output, "error": error}),
        created_at=_now(),
    )
    return row


def approve_recommendation(recommendation_id: int, payload: dict[str, Any] | None, principal: Principal) -> dict[str, Any]:
    ensure_schema()
    actor = _actor(principal)
    with SessionLocal() as db:
        row = db.execute(select(AiAgentRecommendation).where(AiAgentRecommendation.id == int(recommendation_id),
            AiAgentRecommendation.cluster_name == S.CLUSTER_NAME)).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="recommendation not found")
        if row.approval_status in {"REJECTED", "EXECUTED"}:
            raise HTTPException(status_code=409, detail=f"cannot approve status {row.approval_status}")
        row.approval_status = "APPROVED"
        row.approved_by = actor
        row.approved_at = _now()
        row.updated_at = _now()
        db.add(_audit(row.id, "approve", "APPROVED", actor, approved_by=actor, output={"approved": True}))
        db.commit()
        db.refresh(row)
        return {"available": True, "recommendation": _rec_api(row)}


def reject_recommendation(recommendation_id: int, payload: dict[str, Any] | None, principal: Principal) -> dict[str, Any]:
    ensure_schema()
    actor = _actor(principal)
    reason = str((payload or {}).get("reason") or "Rejected by DBA")
    with SessionLocal() as db:
        row = db.execute(select(AiAgentRecommendation).where(AiAgentRecommendation.id == int(recommendation_id),
            AiAgentRecommendation.cluster_name == S.CLUSTER_NAME)).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="recommendation not found")
        if row.approval_status == "EXECUTED":
            raise HTTPException(status_code=409, detail="executed recommendation cannot be rejected")
        row.approval_status = "REJECTED"
        row.execution_status = "REJECTED"
        row.execution_output = reason
        row.updated_at = _now()
        db.add(_audit(row.id, "reject", "REJECTED", actor, output={"reason": reason}))
        db.commit()
        db.refresh(row)
        return {"available": True, "recommendation": _rec_api(row)}


def execute_recommendation(recommendation_id: int, payload: dict[str, Any] | None, principal: Principal) -> dict[str, Any]:
    ensure_schema()
    payload = payload or {}
    actor = _actor(principal)
    confirm = bool(payload.get("confirm") or payload.get("execute"))
    with SessionLocal() as db:
        row = db.execute(select(AiAgentRecommendation).where(AiAgentRecommendation.id == int(recommendation_id),
            AiAgentRecommendation.cluster_name == S.CLUSTER_NAME)).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="recommendation not found")
        if row.approval_status != "APPROVED":
            raise HTTPException(status_code=409, detail=f"recommendation must be APPROVED before execution; current status {row.approval_status}")
        sql = row.recommended_sql
        started = _now()
        result = ai_agent_executor.execute_sql(sql, confirm=confirm)
        status = str(result.get("status") or "FAILED")
        row.execution_status = status
        row.execution_output = _json(result)
        if status == "EXECUTED":
            row.approval_status = "EXECUTED"
        elif status == "FAILED":
            row.approval_status = "FAILED"
        row.updated_at = _now()
        db.add(_audit(
            row.id,
            "execute",
            status,
            actor,
            approved_by=row.approved_by,
            sql=sql,
            output=result,
            error=result.get("reason") if not result.get("executed") else None,
            started_at=started,
        ))
        db.commit()
        db.refresh(row)
        return {"available": True, "execution": result, "recommendation": _rec_api(row)}


def status() -> dict[str, Any]:
    return {
        "available": True,
        "running": is_running(),
        "scheduler_enabled": _env_bool("AI_AGENT_SCHEDULER_ENABLED", False),
        "interval_minutes": int(os.environ.get("AI_AGENT_INTERVAL_MINUTES") or "30"),
        "lookback_minutes": int(os.environ.get("AI_AGENT_LOOKBACK_MINUTES") or "30"),
        "email_enabled": ai_agent_email.enabled(),
        "execution_enabled": ai_agent_executor.execution_enabled(),
        "provider": ai_provider.provider_status(),
    }
