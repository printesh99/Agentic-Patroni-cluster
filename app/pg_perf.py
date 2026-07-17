"""Performance page endpoints — pg_stat_activity / _statements / _user_tables / _user_indexes."""
from __future__ import annotations

from typing import Any

from . import sources as S

# ``query`` may contain the field separator? No — psql -F handles columns; we
# put query text last and never split it further (it can't contain \x1f).


def sessions(state: str | None = None, db: str | None = None, search: str | None = None, limit: int = 500) -> dict[str, Any]:
    clauses = ["pid <> pg_backend_pid()"]
    if state:
        clauses.append("state = '" + state.replace("'", "''") + "'")
    if db:
        clauses.append("datname = '" + db.replace("'", "''") + "'")
    if search:
        q = search.replace("'", "''")
        clauses.append("(query ilike '%" + q + "%' or application_name ilike '%" + q + "%' or usename ilike '%" + q + "%')")
    where = " and ".join(clauses)
    rows = S.sql(
        "select pid, coalesce(usename,''), coalesce(datname,''), "
        "coalesce(application_name,''), coalesce(client_addr::text,'local'), "
        "coalesce(state,''), coalesce(wait_event_type,''), coalesce(wait_event,''), "
        "coalesce(extract(epoch from now()-query_start),0)::int, "
        "coalesce(extract(epoch from now()-xact_start),0)::int, "
        "replace(left(query,1000),chr(10),' ') "
        f"from pg_stat_activity where {where} order by query_start nulls last limit {int(limit)}"
    )
    out = [{
        "pid": int(r[0]), "username": r[1], "user": r[1],
        "database": r[2], "db": r[2],
        "application_name": r[3], "application": r[3],
        "client_addr": r[4], "state": r[5],
        "wait_event_type": r[6], "wait_event": r[7],
        "query_age_sec": int(r[8]), "xact_age_sec": int(r[9]), "query": r[10],
    } for r in rows]
    return {"available": True, "source": "pg_stat_activity", "sessions": out,
            "summary": {"total": len(out)}}


def lock_tree() -> dict[str, Any]:
    rows = S.sql(
        """
        with blocked as (
          select a.pid, unnest(pg_blocking_pids(a.pid)) as blocker_pid
          from pg_stat_activity a
          where cardinality(pg_blocking_pids(a.pid)) > 0
        ),
        waiting_lock as (
          select distinct on (l.pid)
            l.pid,
            l.mode,
            coalesce(l.relation::regclass::text, '') as relation,
            coalesce(extract(epoch from now() - l.waitstart),
                     extract(epoch from now() - a.query_start), 0)::int as wait_sec
          from pg_locks l
          left join pg_stat_activity a on a.pid = l.pid
          where not l.granted
          order by l.pid, l.waitstart nulls last
        ),
        blocker_lock as (
          select distinct on (l.pid)
            l.pid,
            l.mode,
            coalesce(l.relation::regclass::text, '') as relation
          from pg_locks l
          where l.granted and l.relation is not null
          order by l.pid, l.relation::text
        )
        select
          b.blocker_pid,
          coalesce(ba.usename, ''),
          coalesce(ba.datname, ''),
          coalesce(ba.state, ''),
          coalesce(replace(left(ba.query, 500), chr(10), ' '), ''),
          coalesce(bl.mode, ''),
          coalesce(bl.relation, ''),
          coalesce(extract(epoch from now() - ba.query_start), 0)::int,
          b.pid,
          coalesce(wa.usename, ''),
          coalesce(wa.datname, ''),
          coalesce(wa.state, ''),
          coalesce(replace(left(wa.query, 500), chr(10), ' '), ''),
          coalesce(wl.mode, ''),
          coalesce(wl.relation, ''),
          coalesce(wl.wait_sec, 0)
        from blocked b
        left join pg_stat_activity ba on ba.pid = b.blocker_pid
        left join pg_stat_activity wa on wa.pid = b.pid
        left join blocker_lock bl on bl.pid = b.blocker_pid
        left join waiting_lock wl on wl.pid = b.pid
        order by b.blocker_pid, b.pid
        """
    )
    by_blocker: dict[int, dict[str, Any]] = {}
    for r in rows:
        blocker_pid = int(r[0])
        node = by_blocker.setdefault(blocker_pid, {
            "id": f"blocker-{blocker_pid}",
            "blocker": {
                "pid": blocker_pid,
                "user": r[1],
                "db": r[2],
                "state": r[3],
                "query": r[4],
                "mode": r[5],
                "relation": r[6] or "-",
                "waitSec": int(r[7] or 0),
            },
            "blocked": [],
        })
        node["blocked"].append({
            "pid": int(r[8]),
            "user": r[9],
            "db": r[10],
            "state": r[11],
            "query": r[12],
            "mode": r[13],
            "relation": r[14] or "-",
            "waitSec": int(r[15] or 0),
        })
    return {
        "available": True,
        "source": "pg_blocking_pids + pg_locks + pg_stat_activity",
        "tree": list(by_blocker.values()),
    }


def waits() -> dict[str, Any]:
    rows = S.sql(
        "select coalesce(wait_event_type,'CPU / none'), coalesce(wait_event,'-'), count(*) "
        "from pg_stat_activity where pid <> pg_backend_pid() and state = 'active' "
        "group by 1,2 order by 3 desc"
    )
    waits = [{"wait_event_type": r[0], "wait_event": r[1], "sessions": int(r[2])} for r in rows]
    classes: dict[str, int] = {}
    for w in waits:
        classes[w["wait_event_type"]] = classes.get(w["wait_event_type"], 0) + w["sessions"]
    return {
        "available": True,
        "source": "pg_stat_activity",
        "waits": waits,
        "classes": [{"wait_event_type": k, "sessions": v} for k, v in
                    sorted(classes.items(), key=lambda kv: -kv[1])],
    }


def slow(min_seconds: float = 5, limit: int = 100) -> dict[str, Any]:
    rows = S.sql(
        "select pid, coalesce(usename,''), coalesce(datname,''), "
        "coalesce(client_addr::text,'local'), coalesce(application_name,''), state, "
        "coalesce(wait_event_type,''), coalesce(wait_event,''), query_id::text, "
        "coalesce(extract(epoch from now()-query_start),0)::int, "
        "coalesce(extract(epoch from now()-xact_start),0)::int, "
        "replace(left(query,500),chr(10),' ') "
        "from pg_stat_activity where state = 'active' and pid <> pg_backend_pid() "
        f"and now()-query_start >= make_interval(secs => {float(min_seconds)}) "
        f"order by 7 desc limit {int(limit)}"
    )
    out = []
    for r in rows:
        out.append({
            "pid": int(r[0]), "username": r[1], "database": r[2], "client_addr": r[3],
            "application_name": r[4], "state": r[5],
            "wait_event_type": r[6], "wait_event": r[7], "queryid": r[8] or None,
            "query_age_sec": int(r[9]), "xact_age_sec": int(r[10]), "query": r[11],
        })
    return {"available": True, "source": "pg_stat_activity", "slow_queries": out}


def vacuum(database: str | None = None, limit: int = 75) -> dict[str, Any]:
    rows = S.sql(
        "select schemaname, relname, n_dead_tup, n_live_tup, "
        "coalesce(to_char(last_autovacuum,'YYYY-MM-DD HH24:MI'),'never'), "
        "coalesce(to_char(last_autoanalyze,'YYYY-MM-DD HH24:MI'),'never'), "
        "autovacuum_count, n_mod_since_analyze "
        f"from pg_stat_user_tables order by n_dead_tup desc limit {int(limit)}"
    )
    out = []
    for r in rows:
        out.append({
            "schemaname": r[0], "table_name": r[1],
            "dead_tuples": int(r[2]), "live_tuples": int(r[3]),
            "dead_tuple_percent": round(100 * int(r[2]) / max(1, int(r[2]) + int(r[3])), 2),
            "last_autovacuum": r[4], "last_autoanalyze": r[5],
            "autovacuum_count": int(r[6]), "mod_since_analyze": int(r[7]),
        })
    return {"available": True, "source": "pg_stat_user_tables", "vacuum": out}


def bloat(database: str | None = None, limit: int = 75) -> dict[str, Any]:
    rows = S.sql(
        "select schemaname, relname, n_dead_tup, "
        "round(100*n_dead_tup::numeric/nullif(n_live_tup+n_dead_tup,0),2), "
        "pg_total_relation_size(relid), n_live_tup, n_mod_since_analyze "
        f"from pg_stat_user_tables order by n_dead_tup desc limit {int(limit)}"
    )
    out = []
    for r in rows:
        out.append({
            "schema_name": r[0], "table_name": r[1], "dead_tuples": int(r[2]),
            "dead_tuple_percent": float(r[3]) if r[3] not in ("", None) else 0.0,
            "size_bytes": int(r[4]), "live_tuples": int(r[5]), "mod_since_analyze": int(r[6]),
        })
    return {"available": True, "source": "pg_stat_user_tables", "bloat": out}


def topsql(sort: str = "total", db: str | None = None, limit: int = 75) -> dict[str, Any]:
    order = {"total": "total_exec_time", "mean": "mean_exec_time",
             "calls": "calls", "rows": "rows"}.get(sort, "total_exec_time")
    try:
        rows = S.sql(
            "select queryid::text, replace(left(query,400),chr(10),' '), calls, "
            "round(mean_exec_time::numeric,3), round(total_exec_time::numeric,2), rows, "
            "round(100*shared_blks_hit::numeric/nullif(shared_blks_hit+shared_blks_read,0),1) "
            f"from pg_stat_statements order by {order} desc nulls last limit {int(limit)}"
        )
    except S.SourceError:
        return {"available": False, "source": "pg_stat_statements", "top_sql": [],
                "reason": "pg_stat_statements not available"}
    out = []
    for r in rows:
        out.append({
            "queryid": r[0], "query": r[1], "calls": int(r[2]),
            "mean_exec_ms": float(r[3]), "total_exec_ms": float(r[4]),
            "rows": int(r[5]), "cache_hit_pct": float(r[6]) if r[6] not in ("", None) else None,
        })
    return {"available": True, "source": "pg_stat_statements", "top_sql": out}


def index_advisor(database: str | None = None, limit: int = 75) -> dict[str, Any]:
    rows = S.sql(
        "select s.schemaname, s.relname, s.indexrelname, s.idx_scan, "
        "pg_relation_size(s.indexrelid), i.indisunique, i.indisprimary "
        "from pg_stat_user_indexes s join pg_index i on i.indexrelid = s.indexrelid "
        "where s.idx_scan = 0 and not i.indisprimary "
        f"order by pg_relation_size(s.indexrelid) desc limit {int(limit)}"
    )
    out = []
    for r in rows:
        size_bytes = int(r[4])
        recommendation = "review_unused_large" if size_bytes >= 256 * 1024 * 1024 else "review_unused"
        is_unique = str(r[5]).lower() in {"t", "true", "1", "yes"}
        is_primary = str(r[6]).lower() in {"t", "true", "1", "yes"}
        out.append({
            "schema_name": r[0], "schemaname": r[0], "table_name": r[1],
            "index_name": r[2], "idx_scan": int(r[3]), "size_bytes": size_bytes,
            "is_unique": is_unique, "is_primary": is_primary,
            "recommendation": recommendation,
            "recommendation_text": "Review unused large index (0 scans)" if recommendation == "review_unused_large" else "Review unused index (0 scans)",
            "rationale": "Validate workload history before dropping; zero-scan indexes still may protect rare queries or constraints.",
        })
    return {"available": True, "source": "pg_stat_user_indexes", "hypopg_available": False,
            "recommendations": out}


def application_activity(database: str | None = None, limit: int = 100) -> dict[str, Any]:
    # Summary counts by state.
    srow = S.sql_one(
        "select count(*), count(*) filter (where state='active'), "
        "count(*) filter (where state='idle'), "
        "count(*) filter (where state='idle in transaction'), "
        "(select setting::int from pg_settings where name='max_connections') "
        "from pg_stat_activity"
    ) or ["0", "0", "0", "0", "0"]
    summary = {
        "total": int(srow[0]), "total_client_sessions": int(srow[0]),
        "active_sessions": int(srow[1]), "active": int(srow[1]),
        "idle_sessions": int(srow[2]), "idle_in_transaction_sessions": int(srow[3]),
        "maxConnections": int(srow[4]),
        "usagePercent": round(100 * int(srow[0]) / max(1, int(srow[4])), 1),
    }

    def breakdown(expr: str, label: str) -> list[dict[str, Any]]:
        rows = S.sql(
            f"select coalesce(({expr})::text,'(none)'), count(*), "
            "count(*) filter (where state='active'), count(*) filter (where state='idle') "
            "from pg_stat_activity group by 1 order by 2 desc limit 50"
        )
        return [{label: r[0], "sessions": int(r[1]), "active": int(r[2]), "idle": int(r[3])}
                for r in rows]

    return {
        "available": True,
        "source": "pg_stat_activity",
        "summary": summary,
        "source_breakdown": breakdown("case when client_addr is null then 'local socket' "
                                      "else host(client_addr) end", "connection_source"),
        "user_breakdown": breakdown("usename", "username"),
        "application_breakdown": breakdown("application_name", "application_name"),
        "database_breakdown": breakdown("datname", "database"),
        "client_breakdown": breakdown("client_addr", "client_addr"),
    }


def plan_detail(queryid: str, database: str | None = None) -> dict[str, Any]:
    """Return statement metadata plus an explicit no-EXPLAIN plan placeholder."""
    try:
        rows = S.sql(
            "select queryid::text, replace(left(query,1000),chr(10),' '), calls, "
            "round(mean_exec_time::numeric,3), round(total_exec_time::numeric,2), rows "
            f"from pg_stat_statements where queryid::text = '{queryid.replace(chr(39), chr(39)+chr(39))}' "
            "limit 1",
            dbname=database or "postgres",
        )
    except S.SourceError:
        rows = []
    stmt = None
    if rows:
        r = rows[0]
        stmt = {"queryid": r[0], "query": r[1], "calls": int(r[2]),
                "mean_exec_ms": float(r[3]), "total_exec_ms": float(r[4]),
                "rows": int(r[5])}
    return {
        "available": bool(stmt),
        "source": "pg_stat_statements",
        "queryid": queryid,
        "statement": stmt,
        "plan": None,
        "analysis": {
            "findings": [] if stmt else [{
                "severity": "info",
                "title": "Plan not captured",
                "detail": "No persisted EXPLAIN plan is available for this query id.",
            }]
        },
        "safe_explain": False,
    }


def backend_detail(pid: int) -> dict[str, Any]:
    rows = S.sql(
        "select pid, coalesce(usename,''), coalesce(datname,''), "
        "coalesce(application_name,''), coalesce(client_addr::text,'local'), "
        "coalesce(state,''), coalesce(wait_event_type,''), coalesce(wait_event,''), "
        "coalesce(extract(epoch from now()-query_start),0)::int, "
        "coalesce(extract(epoch from now()-xact_start),0)::int, "
        "replace(left(query,1000),chr(10),' ') "
        f"from pg_stat_activity where pid = {int(pid)}"
    )
    if not rows:
        return {"available": False, "pid": pid, "reason": "backend not found"}
    r = rows[0]
    session = {
        "pid": int(r[0]), "username": r[1], "user": r[1],
        "database": r[2], "db": r[2],
        "application_name": r[3], "application": r[3],
        "client_addr": r[4], "state": r[5],
        "wait_event_type": r[6], "wait_event": r[7],
        "query_age_sec": int(r[8]), "xact_age_sec": int(r[9]), "query": r[10],
    }
    return {"available": True, "source": "pg_stat_activity", "session": session,
            "analysis": {"findings": []}, "plan": None, "safe_explain": False}


def session_insight(pid: int) -> dict[str, Any]:
    return backend_detail(pid)


def session_summary() -> dict[str, Any]:
    rows = S.sql(
        "select pid, coalesce(usename,''), coalesce(datname,''), "
        "coalesce(application_name,''), coalesce(client_addr::text,'local'), "
        "coalesce(state,''), coalesce(wait_event_type,''), coalesce(wait_event,''), "
        "coalesce(extract(epoch from now()-query_start),0)::int, "
        "coalesce(extract(epoch from now()-xact_start),0)::int, "
        "replace(left(query,500),chr(10),' ') "
        "from pg_stat_activity where pid <> pg_backend_pid() order by query_start nulls last"
    )
    sessions = [{
        "pid": int(r[0]), "username": r[1], "database": r[2],
        "application_name": r[3], "client_addr": r[4], "state": r[5],
        "wait_event_type": r[6], "wait_event": r[7],
        "query_age_sec": int(r[8]), "xact_age_sec": int(r[9]), "query": r[10],
    } for r in rows]
    return {"available": True, "source": "pg_stat_activity",
            "sessions": sessions, "summary": {"total": len(sessions)}}


def idle_in_transaction(min_seconds: int = 30) -> dict[str, Any]:
    rows = S.sql(
        "select pid, coalesce(usename,''), coalesce(datname,''), "
        "coalesce(application_name,''), coalesce(client_addr::text,'local'), "
        "coalesce(extract(epoch from now()-xact_start),0)::int, "
        "replace(left(query,500),chr(10),' ') "
        "from pg_stat_activity where state = 'idle in transaction' "
        f"and coalesce(extract(epoch from now()-xact_start),0) >= {int(min_seconds)} "
        "order by 6 desc"
    )
    sessions = [{
        "pid": int(r[0]), "username": r[1], "database": r[2],
        "application_name": r[3], "client_addr": r[4],
        "xact_age_sec": int(r[5]), "query": r[6],
    } for r in rows]
    return {"available": True, "source": "pg_stat_activity",
            "sessions": sessions, "idle_in_transaction": sessions}
