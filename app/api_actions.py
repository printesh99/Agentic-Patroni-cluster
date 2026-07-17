"""Mutating actions + live SQL: lifecycle, cutover, jobs, live-connections.

All cluster-changing operations go through ``jobs.submit`` which enforces
dry-run-by-default, the global mutation guard, RBAC, and audit logging.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from fastapi import APIRouter, Body, Depends

from . import jobs, sources as S
from .pg_overview import _members
from .threads import to_thread
from .security import Principal, require_principal

router = APIRouter(prefix="/api/v1", dependencies=[Depends(S.cluster_path_dependency)])


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor(principal: Principal):
    return principal.subject_id, list(principal.roles)


def _confirmed(payload: dict) -> bool:
    return bool(payload.get("confirm") or payload.get("execute"))


# --- lifecycle -------------------------------------------------------------
def _current_lifecycle() -> dict:
    try:
        members, leader, _ = _members(S.patroni_cluster())
    except S.SourceError:
        members, leader = [], None
    pg = [p for p in S.pods() if p["role"] in ("master", "replica")]
    version = S.sql_one("select current_setting('server_version')")
    return {"replicas": len(pg), "leader": leader, "members": members,
            "postgres_version": version[0] if version else None}


@router.get("/lifecycle/provision")
async def lifecycle_provision_get(cluster_id: str | None = None):
    cur = await to_thread(_current_lifecycle)
    return {"source": "live cluster", "current": cur, "members": cur["members"],
            "jobs": [j for j in jobs.JOBS if j["kind"].startswith("lifecycle")][:25],
            "generated_at": _generated_at(),
            "preflight": [
                {"name": "Cluster members healthy", "ok": bool(cur["members"]) and all(m.get("state") in ("running", "streaming") for m in cur["members"])},
                {"name": "Leader present", "ok": bool(cur["leader"])},
            ]}


@router.get("/lifecycle/provision/defaults")
async def lifecycle_provision_defaults(cluster_id: str | None = None):
    return {"defaults": {"instances": 2, "cpu": "1", "memory": "1Gi",
                         "storage": "6Gi", "pgbouncer_replicas": 1,
                         "postgres_version": 18}}


def _scale_executor(replicas: int):
    def run():
        S._run(S.KUBECTL + [
            "-n", S.NS, "patch", "postgrescluster", S.CLUSTER_NAME, "--type=json",
            "-p", f'[{{"op":"replace","path":"/spec/instances/0/replicas","value":{replicas}}}]',
        ])
        return {"patched_replicas": replicas}
    return run


@router.post("/lifecycle/scale/{cluster_id}")
@router.post("/lifecycle/scale")
async def lifecycle_scale(cluster_id: str | None = None, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    actor, roles = _actor(principal)
    replicas = int(payload.get("replicas", payload.get("instances", 2)))
    plan = [f"Patch PostgresCluster/{S.CLUSTER_NAME} spec.instances[0].replicas -> {replicas}",
            "PGO reconciles StatefulSet; Patroni rebalances roles."]
    return jobs.submit("lifecycle-scale", payload, actor=actor, actor_roles=roles,
                       dry_run=not _confirmed(payload), plan=plan,
                       executor=_scale_executor(replicas))


@router.post("/lifecycle/replicas/{cluster_id}")
@router.post("/lifecycle/replicas")
async def lifecycle_replicas(cluster_id: str | None = None, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    actor, roles = _actor(principal)
    action = payload.get("action", "add")
    plan = [f"Replica {action} via PostgresCluster spec change", "Lag/sync gate before promotion"]
    return jobs.submit("lifecycle-replicas", payload, actor=actor, actor_roles=roles,
                       dry_run=not _confirmed(payload), plan=plan)


@router.post("/lifecycle/upgrade/{cluster_id}")
@router.post("/lifecycle/upgrade")
async def lifecycle_upgrade(cluster_id: str | None = None, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    actor, roles = _actor(principal)
    plan = ["Preflight version/extension checks", "Rolling minor upgrade via PGO image bump",
            "Requires maintenance window + approval"]
    return jobs.submit("lifecycle-upgrade", payload, actor=actor, actor_roles=roles,
                       dry_run=not _confirmed(payload), plan=plan)


@router.post("/lifecycle/decommission/{cluster_id}")
@router.post("/lifecycle/decommission")
async def lifecycle_decommission(cluster_id: str | None = None, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    actor, roles = _actor(principal)
    plan = ["Take final backup", "Archive evidence", "Delete PostgresCluster (irreversible)"]
    return jobs.submit("lifecycle-decommission", payload, actor=actor, actor_roles=roles,
                       dry_run=not _confirmed(payload), plan=plan)


# --- cutover ---------------------------------------------------------------
@router.get("/cutover/config")
async def cutover_config():
    return {"source": "console", "config": {"approval": "4-eyes", "orchestrator": "vendored",
            "modes": ["switchover", "switchback"]}}


@router.get("/cutover/modes")
async def cutover_modes():
    return {"modes": [{"id": "switchover", "label": "Planned switchover"},
                      {"id": "switchback", "label": "Switchback"}]}


@router.get("/cutover/runs")
async def cutover_runs(limit: int = 100):
    return {"source": "console", "cutover": [j for j in jobs.JOBS if j["kind"] == "cutover"][:limit],
            "runs": [j for j in jobs.JOBS if j["kind"] == "cutover"][:limit]}


@router.post("/cutover/runs")
async def cutover_run(payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    actor, roles = _actor(principal)
    mode = payload.get("mode", "switchover")
    plan = [f"Patroni {mode}: promote target standby", "Re-point primary endpoint",
            "Verify pg_is_in_recovery + lag gate", "4-eyes approval required"]
    return jobs.submit("cutover", payload, actor=actor, actor_roles=roles,
                       dry_run=not _confirmed(payload), plan=plan)


# --- generic jobs ----------------------------------------------------------
@router.post("/jobs/dry-run")
async def jobs_dry_run(payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    actor, roles = _actor(principal)
    return jobs.submit(payload.get("kind", "generic"), payload, actor=actor,
                       actor_roles=roles, dry_run=True,
                       plan=payload.get("plan", ["validate only"]))


@router.post("/jobs")
async def jobs_create(payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    actor, roles = _actor(principal)
    return jobs.submit(payload.get("kind", "generic"), payload, actor=actor,
                       actor_roles=roles, dry_run=not _confirmed(payload),
                       plan=payload.get("plan", []))


# --- live read-only SQL console --------------------------------------------
_WRITE_RE = re.compile(r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|"
                       r"create|comment|reindex|cluster|vacuum|copy|call|do|"
                       r"refresh|reset|set\s+role)\b", re.IGNORECASE)


@router.get("/live-connections/defaults")
async def live_defaults(cluster_id: str | None = None):
    defaults = {"database": "postgres", "read_only": True, "endpoint": "primary",
                "row_limit": 200, "max_rows": 200, "statement_timeout_ms": 15000}
    return {**defaults, "defaults": defaults, "available": True,
            "generated_at": _generated_at()}


@router.get("/live-connections")
async def live_connections(cluster_id: str | None = None):
    return {"source": "DBA read-only query", "available": True,
            "generated_at": _generated_at(), "read_only": True,
            "databases": [r[0] for r in S.sql(
                "select datname from pg_database where not datistemplate order by 1")],
            "history": []}


@router.post("/live-connections")
@router.post("/live-connections/{cluster_id}")
async def live_query(cluster_id: str | None = None, payload: dict = Body(default={})):
    q = (payload.get("query") or payload.get("sql") or "").strip().rstrip(";")
    if not q:
        return {"ok": False, "read_only": True, "generated_at": _generated_at(), "error": "empty query"}
    if ";" in q or _WRITE_RE.search(q) or not re.match(r"^\s*(select|with|show|explain|table)\b", q, re.I):
        return {"ok": False, "read_only": True, "generated_at": _generated_at(), "error": "Only read-only SELECT/SHOW/EXPLAIN/WITH queries are allowed."}
    db = payload.get("database", "postgres")
    limit = max(1, min(int(payload.get("row_limit", 200)), 1000))
    # Enforce read-only at the session level as a second guard.
    guarded = f"set default_transaction_read_only=on; {q} limit {limit}" \
        if re.match(r"^\s*select\b", q, re.I) and " limit " not in q.lower() \
        else f"set default_transaction_read_only=on; {q}"
    try:
        rows = await to_thread(S.sql, guarded, db)
    except S.SourceError as exc:
        return {"ok": False, "read_only": True, "generated_at": _generated_at(), "error": str(exc)}
    # Drop the leading "SET" echo produced by the read-only guard statement.
    if rows and rows[0] == ["SET"]:
        rows = rows[1:]
    ncols = len(rows[0]) if rows else 0
    return {"ok": True, "available": True, "generated_at": _generated_at(),
            "read_only": True, "rowcount": len(rows),
            "rows": rows[:limit],
            "columns": [f"col{i}" for i in range(ncols)]}
