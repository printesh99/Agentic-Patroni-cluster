"""Application Monitoring and business dashboard helpers.

The production path is read-only against PostgreSQL catalogs/stat views. The
local Docker path can also read seeded schemas so the v13 UI screens have
representative rows when no real logical replication or business schemas exist.
"""
from __future__ import annotations

import os
from typing import Any

from . import sources as S


_DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "tps": ("tps", "posting", "transaction"),
    "tps_warehouse": ("tps_warehouse", "warehouse", "analytics", "etl"),
    "warehouse": ("warehouse", "analytics", "etl"),
    "service": ("service", "crm", "kafka", "chatbot", "profile", "dashboard", "admin"),
    "api_gateway": ("api_gateway", "gateway", "api"),
    "gateway": ("gateway", "api"),
    "charge": ("charge", "payment", "payments", "card", "cards"),
    "locker": ("locker",),
    "mobile": ("mobile", "channel", "channels"),
    "document": ("document", "documents", "doc"),
    "common": ("common", "object", "postgres", "core"),
}


def _local_seed_fallback_enabled() -> bool:
    return os.environ.get("PGC_LOCAL_SEED_FALLBACK", "").lower() in {"1", "true", "yes", "on"}


def _lit(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _safe_sql(query: str, dbname: str = "postgres", timeout: int = 25) -> list[list[str]]:
    try:
        return S.sql(query, dbname=dbname, timeout=timeout)
    except S.SourceError:
        return []


def _safe_one(query: str, dbname: str = "postgres") -> list[str] | None:
    try:
        return S.sql_one(query, dbname=dbname)
    except S.SourceError:
        return None


def _domain_key(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_")


def _domain_hints(domain: str | None) -> tuple[str, ...]:
    key = _domain_key(domain)
    return _DOMAIN_HINTS.get(key, (key,)) if key else ()


def _domain_of(appname: str, datname: str = "") -> str:
    text = f"{appname or ''} {datname or ''}".lower()
    ordered = (
        ("tps_warehouse", _DOMAIN_HINTS["tps_warehouse"]),
        ("api_gateway", _DOMAIN_HINTS["api_gateway"]),
        ("charge", _DOMAIN_HINTS["charge"]),
        ("locker", _DOMAIN_HINTS["locker"]),
        ("mobile", _DOMAIN_HINTS["mobile"]),
        ("document", _DOMAIN_HINTS["document"]),
        ("tps", _DOMAIN_HINTS["tps"]),
        ("service", _DOMAIN_HINTS["service"]),
    )
    for key, hints in ordered:
        if any(h in text for h in hints):
            return key
    return "common" if datname else "other"


def _database_names() -> list[str]:
    rows = _safe_sql("select datname from pg_database where not datistemplate order by 1")
    return [r[0] for r in rows if r and r[0]]


def _selected_databases(database: str | None = None, domain: str | None = None, max_dbs: int = 12) -> list[str]:
    if database:
        return [database]
    dbs = _database_names()
    hints = _domain_hints(domain)
    if hints:
        matched = [db for db in dbs if any(h in db.lower() for h in hints)]
        if matched:
            return matched[:max_dbs]
    return [db for db in dbs if db not in {"template0", "template1"}][:max_dbs]


def _evidence_databases(max_dbs: int = 8) -> list[str]:
    dbs = _database_names()
    return [db for db in dbs if db not in {"template0", "template1"}][:max_dbs]


def _row(label: str, value: int | float, **extra: Any) -> dict[str, Any]:
    out = {"label": label or "(none)", "value": value}
    out.update(extra)
    return out


def _session_where(database: str | None = None, region: str | None = None, domain: str | None = None) -> str:
    clauses = ["pid <> pg_backend_pid()"]
    if database:
        clauses.append(f"datname = {_lit(database)}")
    elif region:
        clauses.append(f"datname = {_lit(region)}")
    hints = _domain_hints(domain)
    if hints:
        like = [
            f"(lower(coalesce(application_name,'')) like {_lit('%' + h + '%')} "
            f"or lower(coalesce(datname,'')) like {_lit('%' + h + '%')})"
            for h in hints
        ]
        clauses.append("(" + " or ".join(like) + ")")
    return "where " + " and ".join(clauses)


def overview(database: str | None = None, region: str | None = None, domain: str | None = None) -> dict[str, Any]:
    where = _session_where(database, region, domain)
    rows = _safe_sql(
        "select coalesce(state,'unknown'), coalesce(application_name,''), "
        f"coalesce(datname,'') from pg_stat_activity {where}"
    )
    total = len(rows)
    by_state: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    by_region: dict[str, int] = {}
    active = idle = idle_tx = 0
    for state, app, db in rows:
        by_state[state] = by_state.get(state, 0) + 1
        dom = _domain_of(app, db)
        by_domain[dom] = by_domain.get(dom, 0) + 1
        region_name = db or "(none)"
        by_region[region_name] = by_region.get(region_name, 0) + 1
        if state == "active":
            active += 1
        elif state == "idle":
            idle += 1
        elif state == "idle in transaction":
            idle_tx += 1
    lock_where = _session_where(database, region, domain) + " and wait_event_type = 'Lock'"
    lock_waits = _int((_safe_one(f"select count(*) from pg_stat_activity {lock_where}") or ["0"])[0])
    return {
        "available": True,
        "source": "pg_stat_activity",
        "total": total,
        "active": active,
        "idle": idle,
        "idle_in_transaction": idle_tx,
        "lock_waits": lock_waits,
        "by_state": [_row(k, v, state=k, sessions=v) for k, v in sorted(by_state.items(), key=lambda x: -x[1])],
        "by_domain": [_row(k, v, domain=k, sessions=v) for k, v in sorted(by_domain.items(), key=lambda x: -x[1])],
        "by_region": [_row(k, v, region=k, sessions=v) for k, v in sorted(by_region.items(), key=lambda x: -x[1])],
        "coverage": len(by_region),
        "coverage_label": "live",
    }


def filters() -> dict[str, Any]:
    dbs = _database_names()
    regions = [db for db in dbs if db.startswith("uat_")] or dbs
    return {"source": "pg_database", "databases": dbs, "regions": regions}


def top_sessions(
    database: str | None = None,
    region: str | None = None,
    domain: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    where = _session_where(database, region, domain)
    rows = _safe_sql(
        "select coalesce(datname,''), coalesce(application_name,''), coalesce(state,'unknown'), "
        "coalesce(wait_event_type,''), coalesce(wait_event,''), count(*)::bigint "
        f"from pg_stat_activity {where} "
        "group by 1,2,3,4,5 order by 6 desc, 1, 2 limit " + str(max(1, min(int(limit), 200)))
    )
    out = [{
        "datname": r[0],
        "application": r[1],
        "state": r[2],
        "wait_event_type": r[3],
        "wait_event": r[4],
        "sessions": _int(r[5]),
    } for r in rows]
    return {"available": True, "source": "pg_stat_activity", "rows": out}


def _domain_relation_clause(domain: str | None = None, schema: str | None = None) -> str:
    clauses = [
        "c.relkind in ('r','p')",
        "n.nspname not in ('pg_catalog','information_schema')",
        "n.nspname not like 'pg_toast%'",
    ]
    if schema:
        clauses.append(f"n.nspname = {_lit(schema)}")
    else:
        hints = _domain_hints(domain)
        if hints:
            like = [
                f"(lower(n.nspname) like {_lit('%' + h + '%')} or lower(c.relname) like {_lit('%' + h + '%')})"
                for h in hints
            ]
            clauses.append("(" + " or ".join(like) + ")")
    return " and ".join(clauses)


def _relation_stats_for_db(dbname: str, domain: str | None, schema: str | None, limit: int) -> list[dict[str, Any]]:
    clause = _domain_relation_clause(domain, schema)
    query = f"""
        select n.nspname, c.relname,
               pg_total_relation_size(c.oid)::bigint as total_bytes,
               greatest(coalesce(s.n_live_tup,0), greatest(c.reltuples,0)::bigint) as est_rows,
               coalesce(s.n_dead_tup,0)::bigint as dead_rows,
               case when coalesce(s.n_live_tup,0) + coalesce(s.n_dead_tup,0) > 0
                    then round((coalesce(s.n_dead_tup,0)::numeric * 100.0) /
                               (coalesce(s.n_live_tup,0) + coalesce(s.n_dead_tup,0)), 2)
                    else 0 end as dead_pct,
               (coalesce(s.n_tup_ins,0) + coalesce(s.n_tup_upd,0) + coalesce(s.n_tup_del,0))::bigint as dml_churn
          from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
          left join pg_stat_user_tables s on s.relid = c.oid
         where {clause}
         order by pg_total_relation_size(c.oid) desc
         limit {max(1, min(int(limit), 100))}
    """
    rows = _safe_sql(query, dbname=dbname)
    return [{
        "datname": dbname,
        "schema": r[0],
        "relation": r[1],
        "size_bytes": _int(r[2]),
        "rows": _int(r[3]),
        "dead_rows": _int(r[4]),
        "dead_pct": _float(r[5]),
        "dml_churn": _int(r[6]),
    } for r in rows]


def domain_detail(
    domain: str,
    database: str | None = None,
    schema: str | None = None,
    range: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    rels: list[dict[str, Any]] = []
    for db in _selected_databases(database, domain):
        rels.extend(_relation_stats_for_db(db, domain, schema, limit))
    rels_by_size = sorted(rels, key=lambda r: r["size_bytes"], reverse=True)[:limit]
    rels_by_rows = sorted(rels, key=lambda r: r["rows"], reverse=True)[:limit]
    rels_by_dead = sorted([r for r in rels if r["dead_pct"] > 0], key=lambda r: r["dead_pct"], reverse=True)[:limit]
    rels_by_churn = sorted([r for r in rels if r["dml_churn"] > 0], key=lambda r: r["dml_churn"], reverse=True)[:limit]
    return {
        "available": True,
        "source": "live PostgreSQL catalogs",
        "domain": domain,
        "range": range or "live",
        "top_by_size": [
            {"datname": r["datname"], "schema": r["schema"], "relation": r["relation"], "value": r["size_bytes"]}
            for r in rels_by_size
        ],
        "top_by_rows": [
            {"datname": r["datname"], "schema": r["schema"], "relation": r["relation"], "value": r["rows"]}
            for r in rels_by_rows
        ],
        "dead_tuples": [
            {"datname": r["datname"], "schema": r["schema"], "relation": r["relation"], "value": r["dead_pct"]}
            for r in rels_by_dead
        ],
        "dml_churn": [
            {"datname": r["datname"], "schema": r["schema"], "relation": r["relation"], "value": r["dml_churn"]}
            for r in rels_by_churn
        ],
        "sessions": top_sessions(database=database, domain=domain, limit=limit).get("rows", []),
    }


def _seed_table_rows(table: str, columns: str) -> list[list[str]]:
    if not _local_seed_fallback_enabled():
        return []
    return _safe_sql(f"select {columns} from object_metrics.{table} order by 1", dbname="object_monitor")


def replication() -> dict[str, Any]:
    repl_rows = _safe_sql(
        "select application_name, state, sync_state, "
        "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn),0)::bigint "
        "from pg_stat_replication"
    )
    slots_rows = _safe_sql(
        "select slot_name, slot_type, coalesce(database,''), active::text, "
        "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn),0)::bigint "
        "from pg_replication_slots order by slot_name"
    )
    slots = [{
        "slot": r[0],
        "type": r[1],
        "database": r[2],
        "active": r[3] == "true",
        "retained_wal_bytes": _int(r[4]),
    } for r in slots_rows]
    if not slots:
        slots = [{
            "slot": r[0],
            "type": r[1],
            "database": r[2],
            "active": r[3] == "true",
            "retained_wal_bytes": _int(r[4]),
        } for r in _seed_table_rows(
            "appmon_replication_slots",
            "slot_name, slot_type, database_name, active::text, retained_wal_bytes",
        )]

    subscriptions: list[dict[str, Any]] = []
    for db in _database_names():
        rows = _safe_sql(
            "select current_database(), subname, array_to_string(subpublications, ','), subenabled::text "
            "from pg_subscription order by subname",
            dbname=db,
        )
        subscriptions.extend({
            "datname": r[0],
            "subscription": r[1],
            "publications": r[2],
            "enabled": r[3] == "true",
        } for r in rows)
    if not subscriptions:
        subscriptions = [{
            "datname": r[0],
            "subscription": r[1],
            "publications": r[2],
            "enabled": r[3] == "true",
        } for r in _seed_table_rows(
            "appmon_subscriptions",
            "datname, subscription, publications, enabled::text",
        )]

    worker_rows = _safe_sql(
        "select coalesce(datname,''), coalesce(backend_type,''), coalesce(application_name,''), count(*)::bigint "
        "from pg_stat_activity "
        "where lower(coalesce(backend_type,'')) like '%replication%' "
        "   or lower(coalesce(application_name,'')) similar to '%(replica|replication|walsender|apply|subscription)%' "
        "group by 1,2,3 order by 4 desc"
    )
    workers = [{
        "datname": r[0],
        "backend_type": r[1],
        "application": r[2],
        "sessions": _int(r[3]),
    } for r in worker_rows]
    if not workers:
        workers = [{
            "datname": r[0],
            "backend_type": r[1],
            "application": r[2],
            "sessions": _int(r[3]),
        } for r in _seed_table_rows(
            "appmon_workers",
            "datname, backend_type, application_name, sessions",
        )]

    return {
        "available": True,
        "source": "pg_stat_replication + pg_replication_slots",
        "rows": [{"application_name": r[0], "state": r[1], "sync_state": r[2],
                  "replay_lag_bytes": _int(r[3])} for r in repl_rows],
        "slots": slots,
        "subscriptions": subscriptions,
        "workers": workers,
    }


def _table_stat_rows(order_col: str, value_col: str, limit: int = 25, max_dbs: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    query = f"""
        select current_database(), schemaname, relname, {value_col}
          from pg_stat_user_tables
         where {value_col} > 0
         order by {order_col} desc
         limit {max(1, min(int(limit), 100))}
    """
    for db in _evidence_databases(max_dbs=max_dbs):
        rows = _safe_sql(query, dbname=db)
        out.extend({"datname": r[0], "schema": r[1], "relation": r[2], "value": _int(r[3])} for r in rows)
    return sorted(out, key=lambda r: r["value"], reverse=True)[:limit]


def _index_rows(limit: int = 25, max_dbs: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    query = f"""
        select current_database(), schemaname, indexrelname, relname, pg_relation_size(indexrelid)::bigint
          from pg_stat_user_indexes
         where pg_relation_size(indexrelid) > 0
         order by pg_relation_size(indexrelid) desc
         limit {max(1, min(int(limit), 100))}
    """
    for db in _evidence_databases(max_dbs=max_dbs):
        rows = _safe_sql(query, dbname=db)
        out.extend({
            "datname": r[0],
            "schema": r[1],
            "index": r[2],
            "table": r[3],
            "value": _int(r[4]),
        } for r in rows)
    return sorted(out, key=lambda r: r["value"], reverse=True)[:limit]


def dba_evidence(limit: int = 25) -> dict[str, Any]:
    rows = _safe_sql(
        "select pid, coalesce(usename,''), state, "
        "coalesce(extract(epoch from now()-xact_start),0)::int, coalesce(wait_event,'') "
        "from pg_stat_activity where xact_start is not null and pid <> pg_backend_pid() "
        f"order by xact_start asc limit {max(1, min(int(limit), 100))}"
    )
    locks = _safe_sql("select mode, count(*)::bigint from pg_locks group by mode order by 2 desc")
    return {
        "available": True,
        "source": "pg_stat_activity + pg_stat_user_*",
        "rows": [{"pid": _int(r[0]), "username": r[1], "state": r[2],
                  "xact_age_sec": _int(r[3]), "wait_event": r[4]} for r in rows],
        "locks": [_row(r[0], _int(r[1]), mode=r[0]) for r in locks],
        "mod_since_analyze": _table_stat_rows("n_mod_since_analyze", "n_mod_since_analyze", limit),
        "seq_scans": _table_stat_rows("seq_scan", "seq_scan", limit),
        "indexes": _index_rows(limit),
    }


def bizmon_dashboards() -> dict[str, Any]:
    return {
        "available": True,
        "source": "live PostgreSQL read-only SQL",
        "credential": "app",
        "dashboards": [
            {
                "id": "business",
                "title": "Banking Business Dashboard",
                "rows": [
                    {"title": "Business Volumes", "panels": [
                        {"id": "business_customers", "title": "Customers", "type": "stat", "has_query": True, "w": 6},
                        {"id": "business_accounts", "title": "Accounts", "type": "stat", "has_query": True, "w": 6},
                        {"id": "business_postings", "title": "Posting Events", "type": "stat", "has_query": True, "w": 6},
                        {"id": "business_revenue", "title": "Transaction Value", "type": "stat", "has_query": True, "w": 6, "unit": "currencyAED"},
                    ]},
                    {"title": "Channel Mix", "panels": [
                        {"id": "business_channel_mix", "title": "Channels", "type": "piechart", "has_query": True, "w": 8},
                        {"id": "business_txn_mix", "title": "Transaction Mix", "type": "bargauge", "has_query": True, "w": 8},
                        {"id": "business_event_trend", "title": "Event Trend", "type": "timeseries", "has_query": True, "w": 8},
                    ]},
                ],
            },
            {
                "id": "management",
                "title": "Enterprise Core Banking Master Scorecard",
                "rows": [
                    {"title": "Executive Health", "panels": [
                        {"id": "management_sessions", "title": "Live Sessions by State", "type": "bargauge", "has_query": True, "w": 8},
                        {"id": "management_db_size", "title": "Database Footprint", "type": "bargauge", "has_query": True, "w": 8, "unit": "bytes"},
                        {"id": "management_risk", "title": "Operational Risk Signals", "type": "bargauge", "has_query": True, "w": 8},
                    ]},
                    {"title": "Adoption and Operations", "panels": [
                        {"id": "management_channel_adoption", "title": "Channel Adoption", "type": "piechart", "has_query": True, "w": 8},
                        {"id": "management_table_churn", "title": "Highest Table Churn", "type": "table", "has_query": True, "w": 8},
                        {"id": "management_locks", "title": "Lock Modes", "type": "bargauge", "has_query": True, "w": 8},
                    ]},
                ],
            },
        ],
    }


def _count_table(dbname: str, qualified: str) -> int:
    row = _safe_one(f"select count(*) from {qualified}", dbname=dbname)
    return _int(row[0]) if row else 0


def _sum_table(dbname: str, qualified: str, expr: str) -> float:
    row = _safe_one(f"select coalesce(sum({expr}),0) from {qualified}", dbname=dbname)
    return _float(row[0]) if row else 0.0


def _count_table_any(qualified: str, domain: str | None = None) -> int:
    total = 0
    for db in _selected_databases(domain=domain, max_dbs=20):
        total += _count_table(db, qualified)
    return total


def _sum_table_any(qualified: str, expr: str, domain: str | None = None) -> float:
    total = 0.0
    for db in _selected_databases(domain=domain, max_dbs=20):
        total += _sum_table(db, qualified, expr)
    return total


def _group_table_any(qualified: str, key_expr: str, domain: str | None = None, limit: int = 20) -> list[list[Any]]:
    totals: dict[str, int] = {}
    query = (
        f"select {key_expr}, count(*)::bigint from {qualified} "
        f"group by 1 order by 2 desc limit {max(1, min(int(limit), 100))}"
    )
    for db in _selected_databases(domain=domain, max_dbs=20):
        for key, value in _safe_sql(query, dbname=db):
            totals[key or "(none)"] = totals.get(key or "(none)", 0) + _int(value)
    return [[key, value] for key, value in sorted(totals.items(), key=lambda item: -item[1])[:limit]]


def _business_channel_rows() -> list[list[Any]]:
    rows: list[list[Any]] = []
    for domain, schema in (("mobile", "mobile"), ("api_gateway", "api_gateway"), ("locker", "locker"), ("document", "document")):
        value = _count_table_any(f"{schema}.events", domain=domain)
        if value:
            rows.append([schema.replace("_", " ").title(), value])
    return rows


def _business_event_trend() -> list[list[Any]]:
    rows: list[list[Any]] = []
    totals: dict[tuple[str, str], int] = {}
    query = (
        "select date_trunc('hour', event_ts)::text, event_type, count(*)::bigint "
        "from tps.posting_events group by 1,2 order by 1,2"
    )
    for db in _selected_databases(domain="tps", max_dbs=20):
        for ts, metric, value in _safe_sql(query, dbname=db):
            key = (ts, metric)
            totals[key] = totals.get(key, 0) + _int(value)
    for (ts, metric), value in sorted(totals.items()):
        rows.append([ts, metric, value])
    return rows


def bizmon_panel(panel: str, range: str | None = None) -> dict[str, Any]:
    if panel == "business_customers":
        rows = [["customers", _count_table_any("crm_slim.customers", domain="service")]]
        return {"available": True, "panel": panel, "columns": ["metric", "value"], "rows": rows}
    if panel == "business_accounts":
        rows = [["accounts", _count_table_any("tps.accounts", domain="tps")]]
        return {"available": True, "panel": panel, "columns": ["metric", "value"], "rows": rows}
    if panel == "business_postings":
        rows = [["events", _count_table_any("tps.posting_events", domain="tps")]]
        return {"available": True, "panel": panel, "columns": ["metric", "value"], "rows": rows}
    if panel == "business_revenue":
        rows = [["AED", round(_sum_table_any("tps.posting_events", "amount", domain="tps"), 2)]]
        return {"available": True, "panel": panel, "columns": ["metric", "value"], "rows": rows}
    if panel == "business_channel_mix" or panel == "management_channel_adoption":
        return {"available": True, "panel": panel, "columns": ["channel", "value"], "rows": _business_channel_rows()}
    if panel == "business_txn_mix":
        rows = _group_table_any("tps.posting_events", "event_type", domain="tps")
        return {"available": True, "panel": panel, "columns": ["event_type", "value"],
                "rows": [[r[0], _int(r[1])] for r in rows]}
    if panel == "business_event_trend":
        return {"available": True, "panel": panel, "columns": ["time", "metric", "value"],
                "rows": _business_event_trend()}
    if panel == "management_sessions":
        rows = _safe_sql("select coalesce(state,'unknown'), count(*)::bigint from pg_stat_activity group by 1 order by 2 desc")
        return {"available": True, "panel": panel, "columns": ["state", "value"],
                "rows": [[r[0], _int(r[1])] for r in rows]}
    if panel == "management_db_size":
        rows = _safe_sql(
            "select datname, pg_database_size(datname)::bigint from pg_database "
            "where not datistemplate order by 2 desc limit 10"
        )
        return {"available": True, "panel": panel, "columns": ["database", "value"],
                "rows": [[r[0], _int(r[1])] for r in rows]}
    if panel == "management_risk":
        locks_count = _int((_safe_one("select count(*) from pg_locks") or ["0"])[0])
        active_count = _int((_safe_one("select count(*) from pg_stat_activity where state = 'active'") or ["0"])[0])
        slot_count = _int((_safe_one("select count(*) from pg_replication_slots") or ["0"])[0])
        if slot_count == 0 and _local_seed_fallback_enabled():
            slot_count = _int((_safe_one(
                "select count(*) from object_metrics.appmon_replication_slots",
                dbname="object_monitor",
            ) or ["0"])[0])
        mod_backlog = 0
        seq_scans = 0
        for db in _selected_databases(max_dbs=20):
            row = _safe_one(
                "select coalesce(sum(n_mod_since_analyze),0), coalesce(sum(seq_scan),0) "
                "from pg_stat_user_tables",
                dbname=db,
            )
            if row:
                mod_backlog += _int(row[0])
                seq_scans += _int(row[1])
        return {"available": True, "panel": panel, "columns": ["signal", "value"], "rows": [
            ["locks held", locks_count],
            ["active sessions", active_count],
            ["analyze backlog", mod_backlog],
            ["sequential scans", seq_scans],
            ["replication slots", slot_count],
        ]}
    if panel == "management_table_churn":
        rows = _table_stat_rows("n_tup_ins + n_tup_upd + n_tup_del", "n_tup_ins + n_tup_upd + n_tup_del", 15)
        return {"available": True, "panel": panel, "columns": ["database", "schema", "relation", "value"],
                "rows": [[r["datname"], r["schema"], r["relation"], r["value"]] for r in rows]}
    if panel == "management_locks":
        rows = _safe_sql("select mode, count(*)::bigint from pg_locks group by 1 order by 2 desc")
        return {"available": True, "panel": panel, "columns": ["mode", "value"],
                "rows": [[r[0], _int(r[1])] for r in rows]}
    return {"available": False, "panel": panel, "columns": [], "rows": [], "error": "unknown panel"}
