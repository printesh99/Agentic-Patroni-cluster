"""Live backend endpoints for the restored DBA console screens:

  * SQL Insight (Active Session History)  ->  GET /api/v1/insight/ash
  * Cluster Health Grid                   ->  GET /api/v1/health-grid
  * Memory / SGA                          ->  GET /api/v1/memory-sga

These replace the previous static "representative sample" frontends. Every
handler reads straight from the live cluster via ``app.sources`` (pg_stat_*,
pg_settings, Patroni /cluster) and degrades to ``available: false`` with an
``error`` string instead of raising, matching the rest of /api/v1/*.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import sources as S
from .threads import to_thread

router = APIRouter(prefix="/api/v1", tags=["v1-screens"])


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# SQL Insight — Active Session History (live snapshot + statement aggregates)
# ---------------------------------------------------------------------------
def _ash(db: str | None = None, limit: int = 200) -> dict[str, Any]:
    """Live ASH-style view. Postgres has no built-in session-history table, so
    this is a *live* aggregation of the current pg_stat_activity snapshot plus
    top statements from pg_stat_statements (when the extension is present) — no
    seeded data."""
    dbfilter = ""
    if db:
        dbfilter = " and datname = '" + db.replace("'", "''") + "'"

    # Active (non-idle) sessions, the sampling unit for ASH.
    active = S.sql(
        "select coalesce(usename,''), coalesce(datname,''), "
        "coalesce(nullif(wait_event_type,''),'CPU'), coalesce(wait_event,'on-cpu'), "
        "coalesce(state,''), "
        "coalesce(extract(epoch from now()-query_start),0)::int, "
        "replace(left(query,400),chr(10),' ') "
        "from pg_stat_activity "
        "where pid <> pg_backend_pid() and state is not null and state <> 'idle'"
        + dbfilter +
        " order by query_start nulls last limit " + str(_i(limit, 200))
    )
    sessions = [{
        "user": r[0], "database": r[1],
        "wait_event_type": r[2], "wait_event": r[3], "state": r[4],
        "active_sec": _i(r[5]), "query": r[6],
    } for r in active]

    # Wait-event profile: what the DB is spending its active time on right now.
    by_wait: dict[str, int] = {}
    for s in sessions:
        by_wait[s["wait_event_type"]] = by_wait.get(s["wait_event_type"], 0) + 1
    wait_profile = sorted(
        ({"wait_event_type": k, "sessions": v} for k, v in by_wait.items()),
        key=lambda x: x["sessions"], reverse=True,
    )

    # Top SQL by total time (pg_stat_statements), guarded — extension optional.
    top_sql: list[dict[str, Any]] = []
    statements_available = False
    try:
        rows = S.sql(
            "select queryid::text, replace(replace(coalesce(left(query,400),''),chr(10),' '),chr(13),' '), calls, "
            "round(total_exec_time::numeric,1), round(mean_exec_time::numeric,2), rows "
            "from pg_stat_statements order by total_exec_time desc limit 25"
        )
        statements_available = True
        top_sql = [{
            "queryid": r[0], "query": r[1].replace("\n", " "),
            "calls": _i(r[2]), "total_ms": _f(r[3]),
            "mean_ms": _f(r[4]), "rows": _i(r[5]),
        } for r in rows if len(r) >= 6]
    except S.SourceError:
        statements_available = False

    return {
        "available": True,
        "source": "pg_stat_activity (live snapshot)"
                  + (" + pg_stat_statements" if statements_available else ""),
        "generated_at_active_sessions": len(sessions),
        "wait_profile": wait_profile,
        "active_sessions": sessions,
        "top_sql": top_sql,
        "statements_available": statements_available,
        "summary": {
            "active_sessions": len(sessions),
            "distinct_wait_types": len(wait_profile),
            "top_wait": (wait_profile[0]["wait_event_type"] if wait_profile else "idle"),
        },
    }


@router.get("/insight/ash")
async def sql_insight_ash(db: str | None = None, limit: int = 200, cluster_id: str | None = None):
    try:
        return await to_thread(_ash, db, limit)
    except S.SourceError as exc:
        return {"available": False, "error": str(exc), "active_sessions": [], "wait_profile": [], "top_sql": []}


# ---------------------------------------------------------------------------
# Cluster Health Grid — per-member + per-check live status
# ---------------------------------------------------------------------------
def _health_grid() -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    patroni_error: str | None = None
    try:
        doc = S.patroni_cluster()
        for m in doc.get("members", []):
            members.append({
                "name": m.get("name"),
                "role": m.get("role"),
                "state": m.get("state"),
                "lag_mb": round(_f(m.get("lag", 0)) / (1024 * 1024), 2) if m.get("lag") not in (None, "") else 0.0,
                "timeline": m.get("timeline"),
                "host": m.get("host"),
            })
    except S.SourceError as exc:
        patroni_error = str(exc)

    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    # Connection saturation.
    try:
        row = S.sql_one(
            "select (select count(*) from pg_stat_activity), "
            "current_setting('max_connections')::int"
        )
        if row:
            used, cap = _i(row[0]), _i(row[1], 1)
            pct = round(100.0 * used / max(cap, 1), 1)
            add("Connections", "ok" if pct < 80 else ("warn" if pct < 92 else "crit"),
                f"{used}/{cap} ({pct}%)")
    except S.SourceError as exc:
        add("Connections", "unknown", str(exc))

    # Streaming replication health.
    try:
        rows = S.sql(
            "select application_name, state, "
            "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn),0)::bigint "
            "from pg_stat_replication"
        )
        if rows:
            worst = max((_i(r[2]) for r in rows), default=0)
            lag_mb = round(worst / (1024 * 1024), 2)
            add("Replication", "ok" if lag_mb < 64 else ("warn" if lag_mb < 512 else "crit"),
                f"{len(rows)} standby(s), max lag {lag_mb} MB")
        else:
            add("Replication", "warn", "no streaming standbys connected")
    except S.SourceError as exc:
        add("Replication", "unknown", str(exc))

    # Longest transaction / xid wraparound proximity.
    try:
        row = S.sql_one(
            "select coalesce(max(extract(epoch from now()-xact_start)),0)::int "
            "from pg_stat_activity where xact_start is not null"
        )
        if row:
            longest = _i(row[0])
            add("Long transactions", "ok" if longest < 300 else ("warn" if longest < 1800 else "crit"),
                f"longest open xact {longest}s")
    except S.SourceError as exc:
        add("Long transactions", "unknown", str(exc))

    # Cache hit ratio.
    try:
        row = S.sql_one(
            "select round(100.0*sum(blks_hit)/nullif(sum(blks_hit)+sum(blks_read),0),2) "
            "from pg_stat_database"
        )
        if row and row[0]:
            hit = _f(row[0])
            add("Cache hit ratio", "ok" if hit >= 95 else ("warn" if hit >= 85 else "crit"), f"{hit}%")
    except S.SourceError as exc:
        add("Cache hit ratio", "unknown", str(exc))

    # Deadlocks / rollbacks trend (cumulative).
    try:
        row = S.sql_one("select coalesce(sum(deadlocks),0) from pg_stat_database")
        if row:
            add("Deadlocks", "ok" if _i(row[0]) == 0 else "warn", f"{_i(row[0])} total")
    except S.SourceError as exc:
        add("Deadlocks", "unknown", str(exc))

    order = {"crit": 0, "warn": 1, "unknown": 2, "ok": 3}
    worst = min((order.get(c["status"], 3) for c in checks), default=3)
    overall = ["crit", "warn", "unknown", "ok"][worst]

    return {
        "available": True,
        "source": "Patroni /cluster + pg_stat_* (live)",
        "overall": overall,
        "members": members,
        "checks": checks,
        "patroni_error": patroni_error,
        "summary": {
            "members": len(members),
            "checks": len(checks),
            "failing": sum(1 for c in checks if c["status"] in ("warn", "crit")),
        },
    }


@router.get("/health-grid")
async def health_grid(cluster_id: str | None = None):
    try:
        return await to_thread(_health_grid)
    except S.SourceError as exc:
        return {"available": False, "error": str(exc), "members": [], "checks": []}


# ---------------------------------------------------------------------------
# Memory / SGA — memory settings, bgwriter, cache efficiency (Postgres "SGA")
# ---------------------------------------------------------------------------
_MEM_SETTINGS = [
    "shared_buffers", "effective_cache_size", "work_mem", "maintenance_work_mem",
    "wal_buffers", "temp_buffers", "max_wal_size", "min_wal_size",
    "huge_pages", "max_connections", "autovacuum_work_mem",
]


def _memory_sga() -> dict[str, Any]:
    names = ",".join("'" + n + "'" for n in _MEM_SETTINGS)
    settings: list[dict[str, Any]] = []
    try:
        rows = S.sql(
            "select name, setting, unit, coalesce(short_desc,'') from pg_settings "
            f"where name in ({names}) order by name"
        )
        for r in rows:
            settings.append({
                "name": r[0], "setting": r[1], "unit": r[2], "description": r[3],
            })
    except S.SourceError as exc:
        return {"available": False, "error": str(exc), "settings": []}

    # Human-friendly shared_buffers / effective_cache_size in bytes.
    sized: list[dict[str, Any]] = []
    try:
        rows = S.sql(
            "select name, pg_size_pretty(setting::bigint * "
            "  case unit when '8kB' then 8192 when 'kB' then 1024 when 'MB' then 1048576 else 1 end) "
            "from pg_settings where name in ('shared_buffers','effective_cache_size',"
            "'wal_buffers','maintenance_work_mem','work_mem') and unit is not null"
        )
        sized = [{"name": r[0], "pretty": r[1]} for r in rows]
    except S.SourceError:
        sized = []

    # Cache hit ratio + bgwriter activity.
    cache_hit = None
    try:
        row = S.sql_one(
            "select round(100.0*sum(blks_hit)/nullif(sum(blks_hit)+sum(blks_read),0),2) "
            "from pg_stat_database"
        )
        if row and row[0]:
            cache_hit = _f(row[0])
    except S.SourceError:
        cache_hit = None

    bgwriter: dict[str, Any] = {}
    try:
        row = S.sql_one(
            "select checkpoints_timed, checkpoints_req, buffers_checkpoint, "
            "buffers_clean, buffers_backend, maxwritten_clean from pg_stat_bgwriter"
        )
        if row:
            bgwriter = {
                "checkpoints_timed": _i(row[0]), "checkpoints_req": _i(row[1]),
                "buffers_checkpoint": _i(row[2]), "buffers_clean": _i(row[3]),
                "buffers_backend": _i(row[4]), "maxwritten_clean": _i(row[5]),
            }
    except S.SourceError:
        bgwriter = {}

    # Top databases by cache footprint (blks_hit) as an SGA-usage proxy.
    top_db: list[dict[str, Any]] = []
    try:
        rows = S.sql(
            "select datname, blks_hit, blks_read, "
            "round(100.0*blks_hit/nullif(blks_hit+blks_read,0),2) "
            "from pg_stat_database where datname is not null and datname <> '' "
            "order by blks_hit desc limit 10"
        )
        top_db = [{
            "database": r[0], "blks_hit": _i(r[1]), "blks_read": _i(r[2]),
            "hit_ratio": _f(r[3]),
        } for r in rows]
    except S.SourceError:
        top_db = []

    return {
        "available": True,
        "source": "pg_settings + pg_stat_bgwriter + pg_stat_database (live)",
        "settings": settings,
        "sized": sized,
        "cache_hit_ratio": cache_hit,
        "bgwriter": bgwriter,
        "top_databases": top_db,
        "summary": {
            "settings": len(settings),
            "cache_hit_ratio": cache_hit,
            "checkpoints_req": bgwriter.get("checkpoints_req"),
        },
    }


@router.get("/memory-sga")
async def memory_sga(cluster_id: str | None = None):
    try:
        return await to_thread(_memory_sga)
    except S.SourceError as exc:
        return {"available": False, "error": str(exc), "settings": []}
