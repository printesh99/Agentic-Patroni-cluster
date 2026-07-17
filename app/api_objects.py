"""Object-inventory routes (the Objects page) served LIVE from the monitored
PostgreSQL via ``sources.sql`` (kubectl exec -> psql on the primary).

The v7 refactor dropped the snapshot store + ingest endpoint, leaving these
8 ``/api/*`` routes 404. We read the same catalogs directly on demand instead.

Performance guards (live HTTP over kubectl-exec is expensive):
  * Global concurrency cap (OBJECTS_DB_CONCURRENCY) bounds simultaneous psql
    execs across ALL object endpoints so the pod is never saturated into a
    NotReady cascade.
  * ALL-scope is capped to the top OBJECTS_MAX_DBS databases by size; a single
    region or a specific database is exact and cheap. Counts/lists then say how
    many of the cluster's databases were scanned.
  * Per (endpoint, scope) results are cached for OBJECTS_CACHE_TTL seconds, so
    the 7 panels the page loads in parallel (and refreshes) don't re-scan.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Query

from . import sources as S
from .threads import to_thread

router = APIRouter(prefix="/api", tags=["objects"])

_REGION_RE = re.compile(r"^(ae|ch|ke|sa|uk)_(common|service|tps|tps_warehouse)_uat$")
_EXCLUDE_DBS = {d.strip() for d in os.environ.get(
    "OBJECTS_EXCLUDE_DATABASES", "uat_object_metrics,object_monitor").split(",") if d.strip()}
_CONCURRENCY = max(1, int(os.environ.get("OBJECTS_DB_CONCURRENCY", "5")))
_MAX_DBS = max(1, int(os.environ.get("OBJECTS_MAX_DBS", "12")))
_CACHE_TTL = float(os.environ.get("OBJECTS_CACHE_TTL", "60"))

# One semaphore shared by every endpoint -> hard ceiling on concurrent execs.
_SEM = asyncio.Semaphore(_CONCURRENCY)
_CACHE: dict[str, tuple[float, Any]] = {}


def _classify(datname: str) -> tuple[str, str]:
    m = _REGION_RE.match(datname)
    if m:
        return m.group(1).upper(), m.group(2).upper()
    if datname == "landlord_uat":
        return "LANDLORD", "LANDLORD"
    if datname == "postgres":
        return "POSTGRES", "POSTGRES"
    return "OTHER", "OTHER"


def _i(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _truthy(v: Any) -> str:
    return "true" if str(v).strip().lower() in {"t", "true", "1", "yes", "on"} else "false"


async def _cached(key: str, producer: Callable[[], "asyncio.Future"]) -> Any:
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = await producer()
    _CACHE[key] = (now + _CACHE_TTL, value)
    return value


# --- catalog SQL -----------------------------------------------------------
_NONSYS = "n.nspname not in ('pg_catalog', 'information_schema')"

# Object COUNTS only (cheap). Sizes are summed separately so the count query
# stays fast on very large databases.
_COUNTS_SQL = f"""
select
  (select count(*) from pg_namespace n where {_NONSYS}) as schemas,
  (select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
     where c.relkind in ('r','p') and {_NONSYS}) as tables,
  (select count(*) from pg_index ix join pg_class t on t.oid=ix.indrelid
     join pg_namespace n on n.oid=t.relnamespace where {_NONSYS}) as indexes,
  (select count(*) from information_schema.views
     where table_schema not in ('pg_catalog','information_schema')) as views,
  (select count(*) from pg_matviews
     where schemaname not in ('pg_catalog','information_schema')) as materialized_views,
  (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
     where {_NONSYS} and p.prokind in ('f','p')) as functions,
  (select count(*) from information_schema.triggers
     where trigger_schema not in ('pg_catalog','information_schema')) as triggers,
  (select count(*) from information_schema.sequences
     where sequence_schema not in ('pg_catalog','information_schema')) as sequences,
  (select count(*) from pg_publication) as publications,
  (select count(*) from pg_subscription) as subscriptions,
  coalesce((select sum(pg_relation_size(c.oid)) from pg_class c
     join pg_namespace n on n.oid=c.relnamespace
     where c.relkind in ('r','p') and {_NONSYS}),0) as table_bytes,
  coalesce((select sum(pg_indexes_size(c.oid)) from pg_class c
     join pg_namespace n on n.oid=c.relnamespace
     where c.relkind in ('r','p') and {_NONSYS}),0) as index_bytes,
  coalesce((select sum(pg_total_relation_size(c.oid)) from pg_class c
     join pg_namespace n on n.oid=c.relnamespace
     where c.relkind in ('r','p') and {_NONSYS}),0) as total_relation_bytes,
  coalesce((select sum(n_dead_tup) from pg_stat_user_tables),0) as dead_tuples
"""

_TABLES_SQL = f"""
select n.nspname, c.relname,
  pg_total_relation_size(c.oid) as total_size_bytes,
  coalesce(s.n_dead_tup,0) as dead_tuples,
  case when coalesce(s.n_live_tup,0)+coalesce(s.n_dead_tup,0) > 0
       then round(coalesce(s.n_dead_tup,0)*100.0/(coalesce(s.n_live_tup,0)+coalesce(s.n_dead_tup,0)),2)
       else 0 end as dead_tuple_percent
from pg_class c join pg_namespace n on n.oid=c.relnamespace
left join pg_stat_user_tables s on s.relid=c.oid
where c.relkind in ('r','p') and {_NONSYS}
order by pg_total_relation_size(c.oid) desc
limit {{lim}}
"""

_INDEXES_SQL = f"""
select n.nspname, t.relname as tablename, i.relname as indexname,
  pg_relation_size(i.oid) as index_size_bytes,
  ix.indisprimary as is_primary, ix.indisvalid as is_valid
from pg_index ix
join pg_class i on i.oid=ix.indexrelid
join pg_class t on t.oid=ix.indrelid
join pg_namespace n on n.oid=t.relnamespace
where {_NONSYS}
order by pg_relation_size(i.oid) desc
limit {{lim}}
"""

_PUBS_SQL = "select pubname, pubinsert, pubupdate, pubdelete, pubtruncate from pg_publication order by pubname"
_SUBS_SQL = ("select subname, subenabled, coalesce(array_to_string(subpublications, ','), '') "
             "from pg_subscription order by subname")
_SLOTS_SQL = """
select slot_name, slot_type, coalesce(database,''),
  case when active then 1 else 0 end,
  coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn), 0)
from pg_replication_slots order by slot_name
"""
# datname + size, used both for the picker and to rank ALL-scope to top-K.
_DBSIZE_SQL = ("select datname, pg_database_size(datname) from pg_database "
               "where datistemplate=false and datallowconn order by pg_database_size(datname) desc")


# --- database scoping ------------------------------------------------------
def _ranked_databases() -> list[dict[str, Any]]:
    """All monitored databases, largest first, with region/category/size."""
    rows = S.sql(_DBSIZE_SQL)
    out = []
    for r in rows:
        name = r[0] if r else ""
        if not name or name in _EXCLUDE_DBS:
            continue
        region, category = _classify(name)
        out.append({"datname": name, "region": region, "category": category,
                    "size_bytes": _i(r[1]) if len(r) > 1 else 0})
    return out


def _scope(region: str | None, database: str | None,
           ranked: list[dict[str, Any]]) -> tuple[list[str], int, bool]:
    """Resolve (db_names, total_in_scope, truncated). ALL-scope is capped to the
    top _MAX_DBS by size so a live full scan can't blow the request timeout."""
    if database and database != "ALL":
        names = [d["datname"] for d in ranked if d["datname"] == database]
        return names, len(names), False
    pool = ranked if not region or region == "ALL" else [d for d in ranked if d["region"] == region]
    total = len(pool)
    names = [d["datname"] for d in pool[:_MAX_DBS]]   # ranked is size-desc
    return names, total, total > len(names)


async def _gather(dbs: list[str], fn) -> list[tuple[str, Any]]:
    async def one(db: str):
        async with _SEM:
            try:
                return db, await to_thread(fn, db)
            except S.SourceError:
                return db, None
    return await asyncio.gather(*[one(d) for d in dbs])


# --- routes ----------------------------------------------------------------
@router.get("/regions")
async def regions() -> dict[str, Any]:
    async def build():
        ranked = await to_thread(_ranked_databases)
        seen: list[str] = []
        for d in ranked:
            if d["region"] not in seen:
                seen.append(d["region"])
        return {"regions": sorted(seen)}
    return await _cached("regions", build)


@router.get("/databases")
async def databases(region: str | None = Query(default=None)) -> dict[str, Any]:
    async def build():
        ranked = await to_thread(_ranked_databases)
        dbs = ranked if not region or region == "ALL" else [d for d in ranked if d["region"] == region]
        # keep the picker's historical sort (name) but drop the size helper field
        out = sorted(({"datname": d["datname"], "region": d["region"], "category": d["category"]}
                      for d in dbs), key=lambda d: d["datname"])
        return {"databases": out}
    return await _cached(f"databases:{region}", build)


@router.get("/overview")
async def overview(region: str | None = Query(default=None),
                   database: str | None = Query(default=None)) -> dict[str, Any]:
    async def build():
        ranked = await to_thread(_ranked_databases)
        dbs, total, truncated = _scope(region, database, ranked)
        keys = ["schemas", "tables", "indexes", "views", "materialized_views",
                "functions", "triggers", "sequences", "publications", "subscriptions",
                "table_bytes", "index_bytes", "total_relation_bytes", "dead_tuples"]
        totals = {k: 0 for k in keys}
        for _db, rows in await _gather(dbs, lambda db: S.sql(_COUNTS_SQL, dbname=db)):
            if not rows:
                continue
            row = rows[0]
            for idx, k in enumerate(keys):
                if idx < len(row):
                    totals[k] += _i(row[idx])
        totals["databases"] = total
        try:
            totals["replication_slots"] = len(await _gather_slots())
        except S.SourceError:
            totals["replication_slots"] = 0
        return {
            "totals": totals,
            "snapshot": {"collected_at": datetime.now(timezone.utc).isoformat()},
            "source": "live PostgreSQL (pg_catalog)",
            "scope": {"region": region or "ALL", "database": database or "ALL",
                      "databases_scanned": len(dbs), "databases_total": total, "truncated": truncated},
        }
    return await _cached(f"overview:{region}:{database}", build)


async def _gather_slots() -> list[list[str]]:
    async with _SEM:
        return await to_thread(S.sql, _SLOTS_SQL)


@router.get("/tables")
async def tables(region: str | None = Query(default=None),
                 database: str | None = Query(default=None),
                 limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
    async def build():
        ranked = await to_thread(_ranked_databases)
        dbs, _total, truncated = _scope(region, database, ranked)
        rows: list[dict[str, Any]] = []
        for db, db_rows in await _gather(dbs, lambda db: S.sql(_TABLES_SQL.format(lim=limit), dbname=db)):
            for r in (db_rows or []):
                rows.append({
                    "datname": db,
                    "schemaname": r[0] if len(r) > 0 else "",
                    "relname": r[1] if len(r) > 1 else "",
                    "total_size_bytes": _i(r[2]) if len(r) > 2 else 0,
                    "dead_tuples": _i(r[3]) if len(r) > 3 else 0,
                    "dead_tuple_percent": _f(r[4]) if len(r) > 4 else 0.0,
                })
        rows.sort(key=lambda x: x["total_size_bytes"], reverse=True)
        return {"tables": rows[:limit], "truncated": truncated}
    return await _cached(f"tables:{region}:{database}:{limit}", build)


@router.get("/indexes")
async def indexes(region: str | None = Query(default=None),
                  database: str | None = Query(default=None),
                  limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    async def build():
        ranked = await to_thread(_ranked_databases)
        dbs, _total, truncated = _scope(region, database, ranked)
        rows: list[dict[str, Any]] = []
        for db, db_rows in await _gather(dbs, lambda db: S.sql(_INDEXES_SQL.format(lim=limit), dbname=db)):
            for r in (db_rows or []):
                rows.append({
                    "datname": db,
                    "schemaname": r[0] if len(r) > 0 else "",
                    "tablename": r[1] if len(r) > 1 else "",
                    "indexname": r[2] if len(r) > 2 else "",
                    "index_size_bytes": _i(r[3]) if len(r) > 3 else 0,
                    "is_primary": _truthy(r[4]) if len(r) > 4 else "false",
                    "is_valid": _truthy(r[5]) if len(r) > 5 else "false",
                })
        rows.sort(key=lambda x: x["index_size_bytes"], reverse=True)
        return {"indexes": rows[:limit], "truncated": truncated}
    return await _cached(f"indexes:{region}:{database}:{limit}", build)


@router.get("/pubsub")
async def pubsub(region: str | None = Query(default=None),
                 database: str | None = Query(default=None)) -> dict[str, Any]:
    async def build():
        ranked = await to_thread(_ranked_databases)
        dbs, _total, truncated = _scope(region, database, ranked)
        pubs = await _gather(dbs, lambda db: S.sql(_PUBS_SQL, dbname=db))
        subs = await _gather(dbs, lambda db: S.sql(_SUBS_SQL, dbname=db))
        publications: list[dict[str, Any]] = []
        for db, rows in pubs:
            for r in (rows or []):
                publications.append({
                    "datname": db,
                    "pubname": r[0] if len(r) > 0 else "",
                    "insert": _truthy(r[1]) if len(r) > 1 else "false",
                    "update": _truthy(r[2]) if len(r) > 2 else "false",
                    "delete": _truthy(r[3]) if len(r) > 3 else "false",
                    "truncate": _truthy(r[4]) if len(r) > 4 else "false",
                })
        subscriptions: list[dict[str, Any]] = []
        for db, rows in subs:
            for r in (rows or []):
                subscriptions.append({
                    "datname": db,
                    "subname": r[0] if len(r) > 0 else "",
                    "enabled": _truthy(r[1]) if len(r) > 1 else "false",
                    "publications": r[2] if len(r) > 2 else "",
                })
        return {"publications": publications, "subscriptions": subscriptions, "truncated": truncated}
    return await _cached(f"pubsub:{region}:{database}", build)


@router.get("/slots")
async def slots() -> dict[str, Any]:
    async def build():
        rows = await _gather_slots()
        out = []
        for r in rows:
            out.append({
                "slot_name": r[0] if len(r) > 0 else "",
                "slot_type": r[1] if len(r) > 1 else "",
                "database": r[2] if len(r) > 2 else "",
                "active": _i(r[3]) if len(r) > 3 else 0,
                "retained_wal_bytes": _i(r[4]) if len(r) > 4 else 0,
            })
        return {"slots": out}
    return await _cached("slots", build)


@router.get("/snapshots/latest")
async def snapshots_latest() -> dict[str, Any]:
    return {"available": True, "source": "live", "collected_at": datetime.now(timezone.utc).isoformat()}
