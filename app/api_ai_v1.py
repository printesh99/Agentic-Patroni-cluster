"""Thin /api/v1/ai/* router for the v17 AI Ops frontend
(static/dist/ai_platform.js, ai_platform2.js, static/ai-ui.js).

This does NOT implement new AI business logic. tools/ai_module_gap_diag.py
confirmed the underlying capability already exists -- agent runs and
recommendations under /api/ai-agent/* (app/api_ai_agent.py,
app/services/ai_agent_service.py), model/provider status in
app/services/ai_provider.py, and the RAG knowledge base in
app/ai/rag_retriever.py + the ai_knowledge_base table -- just not reachable
at the path names this frontend calls. Every handler below is a direct call
into that existing service layer.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy import select

from . import sources as S
from .ai import rag_retriever
from .db.models import AiActionAudit, AiIncident
from .db.session import SessionLocal
from .services import ai_agent_service, ai_provider
from .services import inventory_service
from .threads import to_thread
from .security import Principal, require_principal

router = APIRouter(prefix="/api/v1/ai", tags=["ai-v1"])


@router.get("/health")
async def ai_health():
    from .ai import log_embeddings
    return await to_thread(log_embeddings.health)


def _overview() -> dict:
    agent = ai_agent_service.status()
    recs = ai_agent_service.list_recommendations(limit=5)
    runs = ai_agent_service.list_runs(limit=1)
    with SessionLocal() as db:
        inv = inventory_service.resolve(db)
        open_incidents = db.execute(
            select(AiIncident).where(AiIncident.inventory_id == inv.id, AiIncident.status != "closed")
        ).scalars().all()
    return {
        "available": True,
        "source": "ai_agent_service.status + ai_agent_service.list_recommendations + ai_incident",
        "agent": agent,
        "last_run": (runs.get("runs") or [None])[0],
        "recommendations_summary": recs.get("summary"),
        "recent_recommendations": (recs.get("recommendations") or [])[:5],
        "open_incidents": len(open_incidents),
    }


@router.get("/overview")
async def overview(cluster_id: str | None = None):
    return await to_thread(_overview)


def _model_gateway_status() -> dict:
    return {
        "available": True,
        "source": "ai_provider.provider_status + ai_agent_service.status",
        **ai_provider.provider_status(),
        "agent": ai_agent_service.status(),
    }


@router.get("/model-gateway/status")
async def model_gateway_status():
    return await to_thread(_model_gateway_status)


@router.get("/agents")
async def list_agents(limit: int = 50):
    return await to_thread(ai_agent_service.list_runs, limit)


@router.get("/agents/{run_id}")
async def agent_run_detail(run_id: int):
    return await to_thread(ai_agent_service.get_run, run_id)


@router.post("/agents/run-now")
async def run_agent_now(payload: dict = Body(default={})):
    return await to_thread(ai_agent_service.run_agent, payload, "MANUAL", payload.get("triggered_by"), True)


@router.get("/recommendations")
async def list_recommendations(
    severity: str | None = None,
    category: str | None = None,
    approval_status: str | None = None,
    cluster_name: str | None = None,
    database_name: str | None = None,
    cluster_id: str | None = None,
    limit: int = 100,
):
    filters = {
        "severity": severity,
        "category": category,
        "approval_status": approval_status,
        "cluster_name": cluster_name or cluster_id,
        "database_name": database_name,
    }
    return await to_thread(ai_agent_service.list_recommendations, filters, limit)


@router.get("/recommendations/{recommendation_id}")
async def recommendation_detail(recommendation_id: int):
    return await to_thread(ai_agent_service.get_recommendation, recommendation_id)


@router.post("/recommendations/{recommendation_id}/approve")
async def approve_recommendation(recommendation_id: int, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return await to_thread(ai_agent_service.approve_recommendation, recommendation_id, payload, principal)


@router.post("/recommendations/{recommendation_id}/reject")
async def reject_recommendation(recommendation_id: int, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return await to_thread(ai_agent_service.reject_recommendation, recommendation_id, payload, principal)


@router.post("/recommendations/{recommendation_id}/execute")
async def execute_recommendation(recommendation_id: int, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return await to_thread(ai_agent_service.execute_recommendation, recommendation_id, payload, principal)


def _evidence_pack(recommendation_id: int) -> dict:
    detail = ai_agent_service.get_recommendation(recommendation_id)
    rec = detail.get("recommendation") or {}
    return {
        "available": True,
        "source": "ai_recommendation.evidence + ai_action_audit",
        "recommendation_id": rec.get("id"),
        "finding": rec.get("finding"),
        "root_cause": rec.get("root_cause"),
        "evidence": rec.get("evidence"),
        "audit_history": rec.get("audit_history"),
    }


@router.get("/evidence-packs/{recommendation_id}")
async def evidence_pack(recommendation_id: int):
    return await to_thread(_evidence_pack, recommendation_id)


def _list_audit(recommendation_id: int | None, limit: int) -> dict:
    ai_agent_service.ensure_schema()
    with SessionLocal() as db:
        query = select(AiActionAudit).order_by(AiActionAudit.created_at.desc(), AiActionAudit.id.desc())
        if recommendation_id is not None:
            query = query.where(AiActionAudit.recommendation_id == recommendation_id)
        rows = db.execute(query.limit(max(1, min(limit, 500)))).scalars().all()
        return {
            "available": True,
            "source": "ai_action_audit",
            "audit": [ai_agent_service._audit_api(r) for r in rows],
            "count": len(rows),
        }


@router.get("/audit")
async def list_audit(recommendation_id: int | None = None, cluster_id: str | None = None, limit: int = 100):
    return await to_thread(_list_audit, recommendation_id, limit)


def _rag_kb(query: str | None, limit: int) -> dict:
    hits = rag_retriever.retrieve(query=query, limit=limit)
    return {
        "available": True,
        "source": "ai_knowledge_base (app/ai/rag_retriever.py)",
        "documents": hits,
        "count": len(hits),
        "semantic_enabled": rag_retriever.semantic_enabled(),
    }


@router.get("/rag/kb")
async def rag_kb(query: str | None = None, limit: int = 20):
    return await to_thread(_rag_kb, query, limit)


# ---------------------------------------------------------------------------
# Ask Your Database — natural-language -> guarded read-only SQL (ai_nlsql view)
# ---------------------------------------------------------------------------
import re as _re
import json as _json

# Anything that is not a pure read is rejected before execution.
_FORBIDDEN_SQL = _re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"copy|call|do|vacuum|analyze|reindex|cluster|refresh|comment|"
    r"security|lock|set|reset|begin|commit|rollback|savepoint|prepare|"
    r"execute|listen|notify|import|merge)\b",
    _re.IGNORECASE,
)


def _database_names() -> list[str]:
    """Searchable Patroni application databases; never the metadata DB."""
    rows = S.sql("select datname from pg_database where datallowconn "
                 "and not datistemplate and datname <> 'postgres' order by datname")
    return [r[0] for r in rows if r and r[0]]


def _schema_context(databases: list[str], max_tables: int = 120) -> tuple[str, dict[str, str]]:
    """Return database-qualified schema context and per-database failures."""
    lines, errors = [], {}
    per_database = max(10, int(max_tables / max(len(databases), 1)))
    query = ("select table_schema||'.'||table_name, "
             "string_agg(column_name, ', ' order by ordinal_position) "
             "from information_schema.columns "
             "where table_schema not in ('pg_catalog','information_schema') "
             "group by 1 order by 1 limit " + str(per_database))
    for database in databases:
        try:
            rows = S.sql(query, dbname=database, timeout=20)
            lines.extend(f"{database}:{r[0]}({r[1]})" for r in rows)
        except S.SourceError as exc:
            errors[database] = str(exc)
    return "\n".join(lines), errors


def _extract_sql(text: str) -> str:
    """Pull a single SQL statement out of an LLM answer (fenced or bare)."""
    if not text:
        return ""
    fence = _re.search(r"```(?:sql)?\s*(.+?)```", text, _re.IGNORECASE | _re.DOTALL)
    body = fence.group(1) if fence else text
    m = _re.search(r"\b(with|select)\b.+", body, _re.IGNORECASE | _re.DOTALL)
    sql = (m.group(0) if m else body).strip()
    return sql.rstrip(";").strip()


def _nlsql(question: str, limit: int, database: str | None = None) -> dict:
    question = (question or "").strip()
    if not question:
        return {"available": False, "error": "empty question"}

    limit = min(max(int(limit), 1), 500)
    try:
        available_databases = _database_names()
    except S.SourceError as exc:
        return {"available": False, "error": str(exc), "database_scope": "patroni-cluster"}
    requested_database = (database or "").strip()
    if requested_database and requested_database not in available_databases:
        return {"available": False, "error": "database is not a searchable Patroni application database",
                "database": requested_database, "databases": available_databases}
    databases = [requested_database] if requested_database else available_databases
    if not databases:
        return {"available": False, "error": "no connectable non-template application databases found",
                "databases": []}
    schema, schema_errors = _schema_context(databases)
    prompt = (
        "You are a PostgreSQL expert. Convert the user's question into ONE "
        "read-only SQL query (SELECT or WITH only). Return ONLY the SQL, no "
        "prose, no explanation, no trailing semicolon. Never modify data.\n\n"
        "Schema lines are prefixed database:table. Generate one query that can "
        "run independently in each selected database; never use cross-database names.\n\n"
        f"Selected databases: {', '.join(databases)}\n"
        f"Schema:\n{schema or '(schema unavailable)'}\n\n"
        f"Question: {question}\nSQL:"
    )
    # CPU-hosted local models (Ollama) can take 30s-2min for a first, cold
    # generation; allow generous headroom so we don't false-timeout.
    result = ai_provider.generate_rca(prompt, timeout_s=150)
    provider_meta = {"provider": result.provider, "model": result.model,
                     "llm_available": result.available}
    if not result.available:
        return {"available": True, "question": question, "sql": None,
                "executed": False, "error": result.error or "LLM provider unavailable",
                **provider_meta}

    sql = _extract_sql(result.content)
    low = sql.lower()
    if not (low.startswith("select") or low.startswith("with")):
        return {"available": True, "question": question, "sql": sql, "executed": False,
                "error": "model did not return a SELECT/WITH query", **provider_meta}
    if _FORBIDDEN_SQL.search(sql) or ";" in sql:
        return {"available": True, "question": question, "sql": sql, "executed": False,
                "error": "generated SQL rejected by read-only guard", **provider_meta}

    # Execute inside a bounded, read-only subquery so we never trust the raw text.
    wrapped = f"select row_to_json(_nlq)::text from ({sql}) _nlq limit {limit}"
    objects, database_errors, per_database_counts = [], dict(schema_errors), {}
    for dbname in databases:
        try:
            db_rows = S.sql(wrapped, dbname=dbname, timeout=20)
            parsed = [_json.loads(r[0]) for r in db_rows]
            per_database_counts[dbname] = len(parsed)
            objects.extend({"database": dbname, **row} for row in parsed)
        except (S.SourceError, ValueError, TypeError) as exc:
            database_errors[dbname] = str(exc)
    if not per_database_counts:
        return {"available": True, "question": question, "sql": sql, "executed": False,
                "error": "query could not be executed on any selected database",
                "database_errors": database_errors, "databases": databases, **provider_meta}
    columns = ["database"]
    for row in objects:
        for key in row:
            if key not in columns:
                columns.append(key)
    rows = [[row.get(column) for column in columns] for row in objects]

    return {
        "available": True,
        "source": "live Patroni cluster databases (guarded read-only SQL)",
        "cluster_id": S.CLUSTER_ID,
        "cluster_name": S.CLUSTER_NAME,
        "database_scope": requested_database or "all-application-databases",
        "databases": databases,
        "per_database_counts": per_database_counts,
        "database_errors": database_errors,
        "question": question,
        "sql": sql,
        "executed": True,
        "row_count": len(rows),
        "columns": columns,
        "rows": rows,
        **provider_meta,
    }


@router.post("/nlsql")
async def nlsql(payload: dict = Body(...)):
    question = payload.get("question") or payload.get("q") or ""
    limit = int(payload.get("limit") or 100)
    token = S.activate_cluster(str(payload["cluster_id"])) if payload.get("cluster_id") else None
    try:
        return await to_thread(_nlsql, question, limit, payload.get("database"))
    finally:
        if token is not None:
            S.reset_active_cluster(token)


@router.get("/nlsql")
async def nlsql_get(q: str, limit: int = 100, cluster_id: str | None = None,
                    database: str | None = None):
    token = S.activate_cluster(cluster_id) if cluster_id else None
    try:
        return await to_thread(_nlsql, q, limit, database)
    finally:
        if token is not None:
            S.reset_active_cluster(token)


@router.get("/nlsql/databases")
async def nlsql_databases(cluster_id: str | None = None):
    token = S.activate_cluster(cluster_id) if cluster_id else None
    try:
        databases = await to_thread(_database_names)
        return {"available": True, "source": "live Patroni pg_database",
                "cluster_id": S.CLUSTER_ID, "cluster_name": S.CLUSTER_NAME,
                "databases": databases}
    except S.SourceError as exc:
        return {"available": False, "error": str(exc), "databases": []}
    finally:
        if token is not None:
            S.reset_active_cluster(token)


# ---------------------------------------------------------------------------
# Branching & Forks — live logical/physical replication topology (ai_branching)
# ---------------------------------------------------------------------------
def _branching() -> dict:
    slots: list[dict] = []
    publications: list[dict] = []
    subscriptions: list[dict] = []
    standbys: list[dict] = []
    errors: dict[str, str] = {}

    try:
        rows = S.sql(
            "select slot_name, slot_type, coalesce(database,''), active::text, "
            "coalesce(wal_status,''), "
            "coalesce(pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), "
            "restart_lsn)),'') from pg_replication_slots order by slot_name"
        )
        slots = [{
            "slot_name": r[0], "slot_type": r[1], "database": r[2],
            "active": r[3] == "t", "wal_status": r[4], "retained_wal": r[5],
        } for r in rows]
    except S.SourceError as exc:
        errors["slots"] = str(exc)

    try:
        rows = S.sql(
            "select p.pubname, p.puballtables::text, "
            "(select count(*) from pg_publication_rel r where r.prpubid = p.oid) "
            "from pg_publication p order by p.pubname"
        )
        publications = [{
            "name": r[0], "all_tables": r[1] == "t", "table_count": _int(r[2]),
        } for r in rows]
    except S.SourceError as exc:
        errors["publications"] = str(exc)

    try:
        rows = S.sql(
            "select subname, subenabled::text from pg_subscription order by subname"
        )
        subscriptions = [{"name": r[0], "enabled": r[1] == "t"} for r in rows]
    except S.SourceError as exc:
        errors["subscriptions"] = str(exc)

    try:
        rows = S.sql(
            "select application_name, coalesce(client_addr::text,''), state, "
            "coalesce(sync_state,'') from pg_stat_replication order by application_name"
        )
        standbys = [{
            "application_name": r[0], "client_addr": r[1],
            "state": r[2], "sync_state": r[3],
        } for r in rows]
    except S.SourceError as exc:
        errors["standbys"] = str(exc)

    return {
        "available": True,
        "source": "pg_replication_slots + pg_publication + pg_subscription + "
                  "pg_stat_replication (live)",
        "logical_slots": [s for s in slots if s["slot_type"] == "logical"],
        "physical_slots": [s for s in slots if s["slot_type"] == "physical"],
        "slots": slots,
        "publications": publications,
        "subscriptions": subscriptions,
        "standbys": standbys,
        "errors": errors,
        "summary": {
            "logical_slots": sum(1 for s in slots if s["slot_type"] == "logical"),
            "physical_standbys": len(standbys),
            "publications": len(publications),
            "subscriptions": len(subscriptions),
        },
    }


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


@router.get("/branching")
async def branching(cluster_id: str | None = None):
    return await to_thread(_branching)
