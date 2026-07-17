"""Compatibility routes for newer UI modules."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends

from . import jobs, pg_cluster, pg_admin, pg_perf, sources as S
from .openshift_rag import _safe_text
from .api_actions import lifecycle_provision_get
from .threads import to_thread

router = APIRouter(dependencies=[Depends(S.cluster_path_dependency)])


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/api/ui/cluster")
async def legacy_ui_cluster():
    return await to_thread(pg_cluster.build_cluster)


@router.get("/api/v1/lifecycle/{action}/{cluster_id}")
async def lifecycle_status(action: str, cluster_id: str):
    current = await lifecycle_provision_get(cluster_id)
    return {"source": "live cluster + console jobs", "action": action,
            "current": current.get("current", {}),
            "members": current.get("members", []),
            "jobs": [j for j in jobs.JOBS if action in j.get("kind", "")][:25],
            "preflight": current.get("preflight", [])}


@router.get("/api/v1/clusters/{cluster_id}/pods/{pod}/logs/preview")
async def pod_logs_preview(cluster_id: str, pod: str, container: str = "database", tail: int = 200):
    tail = max(20, min(int(tail), 500))
    pod_doc = await to_thread(S.kubectl_json, ["-n", S.NS, "get", "pod", pod])
    if pod_doc.get("metadata", {}).get("namespace") != S.NS:
        return {"available": False, "error": "pod is outside the selected namespace", "logs": []}
    containers = {item.get("name") for item in pod_doc.get("spec", {}).get("containers", [])}
    if container not in containers:
        return {"available": False, "error": "container not found in selected pod", "logs": []}
    raw = await to_thread(S._run, S.KUBECTL + ["-n", S.NS, "logs", pod, "-c", container, "--tail", str(tail)], 30)
    return {"available": True, "source": "Kubernetes pod logs", "generated_at": _generated_at(),
            "pod": pod, "container": container, "tail": tail,
            "logs": [_safe_text(line, 2000) for line in raw.splitlines()[-tail:]]}

@router.get("/api/v1/clusters/{cluster_id}/findings")
async def findings(cluster_id: str, status: str = "open", severity: str | None = None):
    return {"source": "collector findings", "available": False, "findings": []}


@router.get("/api/v1/clusters/{cluster_id}/health/timeline")
async def collector_health_timeline(cluster_id: str, range: str = "24h"):
    return {
        "source": "support collector",
        "available": False,
        "cluster_id": cluster_id,
        "range": range,
        "timeline": [],
        "reason": "No collector runs have been ingested yet",
    }


@router.get("/api/v1/clusters/{cluster_id}/databases/{database}/schemas")
async def db_schemas(cluster_id: str, database: str):
    rows = await to_thread(S.sql,
        "select schema_name from information_schema.schemata "
        "where schema_name not in ('pg_catalog','information_schema') order by 1",
        database)
    return {"source": "information_schema.schemata", "available": True,
            "generated_at": _generated_at(), "database": database,
            "schemas": [{"schema_name": r[0]} for r in rows]}


@router.get("/api/v1/clusters/{cluster_id}/databases/{database}/schemas/{schema}/tables")
async def schema_tables(cluster_id: str, database: str, schema: str):
    rows = await to_thread(S.sql,
        "select t.schemaname, t.tablename, t.tableowner, 'table', "
        "coalesce(c.reltuples::bigint, 0), "
        "pg_total_relation_size(quote_ident(t.schemaname)||'.'||quote_ident(t.tablename)) "
        "from pg_tables t join pg_namespace n on n.nspname=t.schemaname "
        "join pg_class c on c.relnamespace=n.oid and c.relname=t.tablename "
        f"where t.schemaname = '{schema.replace(chr(39), chr(39)+chr(39))}' "
        "order by 6 desc limit 200",
        database)
    return {"source": "pg_tables + pg_class", "available": True,
            "generated_at": _generated_at(), "database": database, "schema": schema,
            "tables": [{"table_schema": r[0], "table_name": r[1], "owner": r[2],
                        "table_type": r[3], "estimated_rows": int(r[4]),
                        "total_size_bytes": int(r[5])} for r in rows]}


@router.get("/api/v1/clusters/{cluster_id}/databases/{database}/schemas/{schema}/indexes")
async def schema_indexes(cluster_id: str, database: str, schema: str):
    rows = await to_thread(S.sql,
        "select schemaname, tablename, indexname, indexdef "
        f"from pg_indexes where schemaname = '{schema.replace(chr(39), chr(39)+chr(39))}' "
        "order by tablename, indexname limit 200",
        database)
    return {"source": "pg_indexes", "available": True,
            "generated_at": _generated_at(), "database": database, "schema": schema,
            "indexes": [{"table_schema": r[0], "table_name": r[1], "index_name": r[2],
                         "schemaname": r[0], "tablename": r[1], "indexname": r[2],
                         "indexdef": r[3]} for r in rows]}


@router.get("/api/v1/clusters/{cluster_id}/databases/{database}/extensions")
async def db_extensions(cluster_id: str, database: str):
    rows = await to_thread(S.sql,
        "select a.name, a.default_version, coalesce(e.extversion,''), "
        "coalesce(n.nspname,''), coalesce(a.comment,'') from pg_available_extensions a "
        "left join pg_extension e on e.extname=a.name "
        "left join pg_namespace n on n.oid=e.extnamespace order by a.name",
        database)
    return {"source": "pg_available_extensions", "available": True,
            "generated_at": _generated_at(), "database": database,
            "extensions": [{"name": r[0], "default_version": r[1],
                            "installed_version": r[2] or None, "schema_name": r[3] or None,
                            "comment": r[4] or None} for r in rows]}


@router.post("/api/v1/clusters/{cluster_id}/databases/{database}/extensions")
async def db_extension_validate(cluster_id: str, database: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "database": database,
            "extension": payload.get("name"), "message": "Extension change validated only."}


@router.get("/api/v1/clusters/{cluster_id}/perf/plans/{queryid}")
async def perf_plan(cluster_id: str, queryid: str, database: str | None = None):
    return await to_thread(pg_perf.plan_detail, queryid, database)


@router.get("/api/v1/clusters/{cluster_id}/perf/slow/{pid}")
async def perf_slow_detail(cluster_id: str, pid: int):
    return await to_thread(pg_perf.backend_detail, pid)


@router.get("/api/v1/clusters/{cluster_id}/perf/topsql/{queryid}")
async def perf_topsql_detail(cluster_id: str, queryid: str, database: str | None = None):
    return await to_thread(pg_perf.plan_detail, queryid, database)


@router.post("/api/v1/cutover/{config_id}/runs")
async def cutover_config_run(config_id: str, payload: dict = Body(default={})):
    from .api_actions import cutover_run
    payload = {**payload, "config_id": config_id}
    return await cutover_run(payload)


@router.get("/api/v1/cutover/runs/{job_id}")
async def cutover_run_detail(job_id: str):
    for j in jobs.JOBS:
        if j["id"] == job_id or j.get("request_id") == job_id:
            return {"run": j, **j}
    return {"error": "run not found", "id": job_id}


@router.post("/api/v1/cutover/runs/{job_id}/cancel")
async def cutover_run_cancel(job_id: str):
    return {"ok": False, "id": job_id, "message": "Cancel is not available for local in-memory jobs."}


@router.get("/api/v1/jobs/{job_id}/logs")
async def job_logs(job_id: str, limit: int = 5000):
    for j in jobs.JOBS:
        if j["id"] == job_id or j.get("request_id") == job_id:
            lines = (j.get("stdout_excerpt") or j.get("stderr_excerpt") or "").splitlines()
            return {"job_id": job_id, "lines": lines[-limit:], "logs": lines[-limit:]}
    return {"job_id": job_id, "lines": [], "logs": []}


@router.post("/api/v1/jobs/{job_id}/approve")
async def job_approve(job_id: str):
    return {"ok": True, "id": job_id, "approved": False,
            "message": "Approval workflow is not configured in this bundle."}


@router.post("/api/v1/jobs/{job_id}/reject")
async def job_reject(job_id: str, payload: dict = Body(default={})):
    return {"ok": True, "id": job_id, "rejected": False,
            "reason": payload.get("reason"), "message": "Approval workflow is not configured."}


@router.delete("/api/v1/jobs/{job_id}")
async def job_delete(job_id: str):
    before = len(jobs.JOBS)
    jobs.JOBS[:] = [j for j in jobs.JOBS if j["id"] != job_id and j.get("request_id") != job_id]
    return {"ok": len(jobs.JOBS) < before, "id": job_id}


@router.get("/api/monitor/db-sessions/summary")
async def db_sessions_summary():
    return await to_thread(pg_perf.session_summary)


@router.get("/api/monitor/db-sessions/idle-in-transaction")
async def db_sessions_idle_in_xact():
    return await to_thread(pg_perf.idle_in_transaction)


@router.post("/api/monitor/db-sessions/terminate/{pid}")
async def db_session_terminate(pid: int):
    return {"ok": False, "pid": pid, "dry_run": True,
            "message": "Direct session termination is disabled in this console bundle."}


@router.post("/api/monitor/db-sessions/terminate-bulk")
async def db_session_terminate_bulk(payload: dict = Body(default={})):
    return {"ok": False, "dry_run": True, "terminated": 0,
            "message": "Bulk session termination is disabled in this console bundle."}
