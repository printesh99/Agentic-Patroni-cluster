"""Per-module chart payloads for the v28 chart layer.

Each builder returns the exact datasets one frontend chart row renders,
sourced live (pg_stat_*, pgBackRest, Patroni, Prometheus, Loki, the console
metadata DB). A module with no real source yet returns
``{"available": False}`` and the UI simply does not render that chart row —
no representative samples anywhere.

Response shapes are documented per builder; series are
``[[epoch_ms, value], ...]`` or ``[{name, points: [[epoch_ms, value], ...]}]``.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from sqlalchemy import text

from . import log_parse, loki, pg_ash, pg_backups, pg_metrics, pg_perf
from . import sources as S
from .db.session import engine

_EMPTY = {"available": False}


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _i(v: Any, d: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _series(metric: str, rng: str = "24h") -> list[list[float]]:
    s = pg_metrics.series(metric, rng)
    return s.get("points") or [] if s.get("available") else []


# ---------------------------------------------------------------------------
# Advisor — findings by severity + category (console metadata DB)
# ---------------------------------------------------------------------------
def advisor() -> dict[str, Any]:
    try:
        with engine.begin() as cx:
            sev = cx.execute(text(
                "select lower(severity), count(*) from ai_dba_recommendations"
                " where status = 'open' group by 1 order by 2 desc")).fetchall()
            cat = cx.execute(text(
                "select category, count(*) from ai_dba_recommendations"
                " where status = 'open' group by 1 order by 2 desc limit 8")).fetchall()
    except Exception:  # optional metadata table; never turn the UI into HTTP 500
        return _EMPTY
    return {
        "available": bool(sev),
        "severity": [[s, int(n)] for s, n in sev],
        "categories": [[c, int(n)] for c, n in cat],
        "source": "ai_dba_recommendations",
    }


# ---------------------------------------------------------------------------
# WAL & archive — archived-files rate + live ready-queue depth
# ---------------------------------------------------------------------------
def wal() -> dict[str, Any]:
    gen = _series("wal_archived", "24h")
    ready = None
    try:
        row = S.sql_one(
            "select count(*) from pg_ls_archive_statusdir() where name like '%.ready'")
        if row:
            ready = _i(row[0])
    except S.SourceError:
        pass
    return {
        "available": bool(gen) or ready is not None,
        "archived_rate_series": gen,
        "ready_queue": ready,
        "source": "pg_stat_archiver + archive_status",
    }


# ---------------------------------------------------------------------------
# Backups — real pgBackRest timeline + repo growth
# ---------------------------------------------------------------------------
def backups() -> dict[str, Any]:
    data = pg_backups.build_backups()
    rows = data.get("backups") or []
    timeline = [{
        "label": b.get("label"), "type": b.get("type"),
        "stop_time": b.get("stop_time"),
        "size_gb": round(_f(b.get("database_size_bytes")) / 2**30, 2),
        "repo_gb": round(_f(b.get("repo_size_bytes")) / 2**30, 2),
        "duration": b.get("duration_human"), "error": bool(b.get("error")),
    } for b in rows]
    return {"available": bool(timeline), "timeline": timeline, "source": "pgbackrest info"}


# ---------------------------------------------------------------------------
# DR readiness — RPO from live replication/archive lag (RTO has no source)
# ---------------------------------------------------------------------------
def dr() -> dict[str, Any]:
    rpo_s = None
    try:
        row = S.sql_one(
            "select coalesce(max(extract(epoch from replay_lag)),0) from pg_stat_replication")
        if row is not None:
            rpo_s = _f(row[0]) if row else None
    except S.SourceError:
        pass
    if rpo_s is None:
        return _EMPTY
    return {"available": True, "rpo_seconds": rpo_s, "rto_seconds": None,
            "source": "pg_stat_replication"}


# ---------------------------------------------------------------------------
# Logs — severity histogram over 24h (Loki)
# ---------------------------------------------------------------------------
def logs() -> dict[str, Any]:
    end_ns = loki.now_ns()
    start_ns = end_ns - 24 * 3600 * 10**9
    try:
        streams = loki.query_range(log_parse.build_selector(), start_ns, end_ns,
                                   limit=5000, direction="backward")
        rows = log_parse.flatten(streams)
    except Exception:                    # noqa: BLE001 - loki optional
        return _EMPTY
    buckets: dict[str, dict[int, int]] = {}
    for row in rows:
        hour_s = (int(row["ts_ns"]) // loki.NS_PER_S // 3600) * 3600
        level = row["level"]
        buckets.setdefault(level, {})[hour_s] = buckets.setdefault(level, {}).get(hour_s, 0) + 1
    series = [{"name": level, "points": [[ts * 1000, count] for ts, count in sorted(points.items())]}
              for level, points in sorted(buckets.items())]
    return {"available": bool(series), "series": series, "source": "loki",
            "aggregation_source": "normalized_messages", "truncated": len(rows) >= 5000}


# ---------------------------------------------------------------------------
# Objects — real size treemap (top relations) + per-database growth (Prometheus)
# ---------------------------------------------------------------------------
def objects() -> dict[str, Any]:
    tree = []
    try:
        rows = S.sql(
            "select schemaname || '.' || relname,"
            " pg_total_relation_size(schemaname || '.' || quote_ident(relname))"
            " from pg_stat_user_tables"
            " order by 2 desc limit 12")
        tree = [{"name": r[0], "gb": round(_f(r[1]) / 2**30, 2)} for r in rows]
    except S.SourceError:
        pass
    growth: list[dict[str, Any]] = []
    try:
        result = S.prom_query(
            f'sum by (datname) (pg_database_size_bytes{{namespace="{S.NS}"}})'
            f' - sum by (datname) (pg_database_size_bytes{{namespace="{S.NS}"}} offset 30d)')
        for r in result:
            growth.append({"name": (r.get("metric") or {}).get("datname") or "?",
                           "gb": round(_f(r["value"][1]) / 2**30, 2)})
        growth = sorted(growth, key=lambda g: g["gb"], reverse=True)[:8]
    except (S.SourceError, KeyError, IndexError, TypeError):
        growth = []
    return {"available": bool(tree), "treemap": tree, "growth_30d": growth,
            "source": "pg_stat_user_tables + prometheus"}


# ---------------------------------------------------------------------------
# Performance Insights — per-view datasets (all live catalog queries)
# ---------------------------------------------------------------------------
def perf(view: str | None) -> dict[str, Any]:
    if view in (None, "", "waits"):
        waits = pg_perf.waits()
        rows = waits.get("waits") or waits.get("rows") or []
        donut = []
        longest = []
        try:
            donut = [[r[0], _i(r[1])] for r in S.sql(
                "select coalesce(nullif(wait_event_type,''),'CPU (no wait)'), count(*)"
                " from pg_stat_activity where state = 'active'"
                " and pid <> pg_backend_pid() group by 1 order by 2 desc")]
            longest = [[r[0], _f(r[1])] for r in S.sql(
                "select coalesce(nullif(wait_event_type,''),'CPU') || ' / ' ||"
                " coalesce(wait_event,'on-cpu'),"
                " max(coalesce(extract(epoch from now()-query_start),0))::int"
                " from pg_stat_activity where state = 'active'"
                " and pid <> pg_backend_pid() group by 1 order by 2 desc limit 6")]
        except S.SourceError:
            pass
        return {"available": bool(donut), "waits_donut": donut,
                "longest_waits": longest, "raw": bool(rows),
                "source": "pg_stat_activity"}

    if view in ("topsql", "slow"):
        try:
            rows = S.sql(
                "select calls, round(mean_exec_time::numeric,2)"
                " from pg_stat_statements where calls > 0"
                " order by total_exec_time desc limit 300")
        except S.SourceError:
            return _EMPTY
        pts = [[_i(r[0]), _f(r[1])] for r in rows]
        buckets = [0] * 6           # <1, 1-10, 10-100, 100-1000, 1-10s, >10s
        for _c, mean in pts:
            b = 0 if mean < 1 else 1 if mean < 10 else 2 if mean < 100 else \
                3 if mean < 1000 else 4 if mean < 10000 else 5
            buckets[b] += 1
        return {"available": bool(pts), "scatter": pts, "histogram": buckets,
                "source": "pg_stat_statements"}

    if view == "indexes":
        try:
            summary = S.sql_one(
                "select count(*) filter (where idx_scan > 100),"
                " count(*) filter (where idx_scan between 1 and 100),"
                " count(*) filter (where idx_scan = 0)"
                " from pg_stat_user_indexes")
            unused = S.sql(
                "select indexrelname, pg_relation_size(indexrelid)"
                " from pg_stat_user_indexes where idx_scan = 0"
                " order by 2 desc limit 6")
        except S.SourceError:
            return _EMPTY
        return {
            "available": bool(summary),
            "usage": [["hot (frequent)", _i(summary[0])], ["warm", _i(summary[1])],
                      ["unused", _i(summary[2])]] if summary else [],
            "largest_unused": [[r[0], round(_f(r[1]) / 2**30, 2)] for r in unused],
            "source": "pg_stat_user_indexes",
        }

    if view == "bloat":
        data = pg_perf.bloat(limit=6)
        # pg_perf.bloat() and /perf/bloat use the canonical ``bloat`` key.
        # Retain the older aliases for compatibility with archived payloads.
        rows = data.get("bloat") or data.get("tables") or data.get("rows") or []
        out = []
        for r in rows[:6]:
            if isinstance(r, dict):
                name = r.get("relation") or r.get("table") or r.get("relname") or "?"
                pct = _f(r.get("dead_pct") or r.get("bloat_pct") or r.get("pct"))
                out.append([name, pct])
        return {"available": bool(out), "bloat_pct": out, "source": "pg_stat_user_tables"}

    if view == "vacuum":
        try:
            rows = S.sql(
                "select schemaname || '.' || relname,"
                " coalesce(extract(epoch from now() - greatest(last_vacuum, last_autovacuum))"
                " / 3600.0, -1)::int"
                " from pg_stat_user_tables where n_live_tup > 0"
                " order by greatest(last_vacuum, last_autovacuum) asc nulls first limit 6")
        except S.SourceError:
            return _EMPTY
        oldest = [[r[0], _i(r[1])] for r in rows]
        return {"available": bool(oldest), "oldest_hours": oldest,
                "source": "pg_stat_user_tables"}

    if view == "activity":
        by_app = pg_ash.db_load(minutes=24 * 60, dim="app")
        idle = pg_perf.idle_in_transaction(min_seconds=60)
        idle_rows = idle.get("sessions") or idle.get("rows") or []
        offenders = []
        for r in idle_rows[:6]:
            if isinstance(r, dict):
                label = f"{r.get('application_name') or r.get('app') or '?'} · pid {r.get('pid')}"
                mins = round(_f(r.get('idle_seconds') or r.get('seconds') or r.get('age_seconds')) / 60, 1)
                offenders.append([label, mins])
        return {"available": bool(by_app.get("series")) or bool(offenders),
                "sessions_by_app": by_app.get("series") or [],
                "idle_in_txn_minutes": offenders,
                "source": "ash sampler + pg_stat_activity"}

    return _EMPTY


# ---------------------------------------------------------------------------
# Cluster — Patroni failover history + live member lag
# ---------------------------------------------------------------------------
def cluster() -> dict[str, Any]:
    members = []
    history = []
    try:
        doc = S.patroni_cluster()
        for m in doc.get("members", []):
            members.append({"name": m.get("name"), "role": m.get("role"),
                            "state": m.get("state"), "lag_mb": round(_f(m.get("lag")) / 2**20, 2)
                            if m.get("lag") not in (None, "unknown") else None,
                            "timeline": m.get("timeline")})
        for h in (doc.get("history") or [])[-10:]:
            # patroni history rows: [timeline, lsn, reason, timestamp, server]
            if isinstance(h, (list, tuple)) and len(h) >= 4:
                history.append({"timeline": h[0], "reason": str(h[2]),
                                "at": str(h[3])})
    except S.SourceError:
        return _EMPTY
    return {"available": bool(members), "members": members, "history": history,
            "source": "patroni /cluster"}


# ---------------------------------------------------------------------------
# Replication — lag series (Prometheus) / live logical snapshot
# ---------------------------------------------------------------------------
def replication(view: str | None) -> dict[str, Any]:
    if view == "logical":
        try:
            rows = S.sql(
                "select slot_name, coalesce(active,false),"
                " coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn),0)"
                " from pg_replication_slots where slot_type = 'logical'")
        except S.SourceError:
            return _EMPTY
        slots = [{"slot": r[0], "active": r[1] in ("t", "true", "True"),
                  "retained_wal_gb": round(_f(r[2]) / 2**30, 3)} for r in rows]
        return {"available": bool(slots), "slots": slots,
                "source": "pg_replication_slots"}
    pts = _series("replication_lag", "24h")
    return {"available": bool(pts), "lag_series": pts, "source": "prometheus"}


# ---------------------------------------------------------------------------
# Anomalies — ML anomaly scores from the metadata DB
# ---------------------------------------------------------------------------
def anomalies() -> dict[str, Any]:
    try:
        with engine.begin() as cx:
            rows = cx.execute(text(
                "select extract(epoch from scored_at)::bigint * 1000,"
                " coalesce(anomaly_score, 0), coalesce(is_anomaly, false)"
                " from ml_anomaly_score"
                " where scored_at > now() - interval '48 hours'"
                " order by scored_at")).fetchall()
    except Exception:
        return _EMPTY
    points = [[int(ts), round(_f(score), 4)] for ts, score, _a in rows]
    marks = [[int(ts), round(_f(score), 4)] for ts, score, is_a in rows if is_a]
    return {"available": bool(points), "score_series": points,
            "anomalies": marks, "source": "ml_anomaly_score"}


# ---------------------------------------------------------------------------
# Activity/audit heatmap — hour × weekday of real audited actions
# ---------------------------------------------------------------------------
def heatmap() -> dict[str, Any]:
    try:
        with engine.begin() as cx:
            rows = cx.execute(text(
                "select extract(dow from created_at)::int,"
                " extract(hour from created_at)::int, count(*)"
                " from ai_action_audit"
                " where created_at > now() - interval '7 days' group by 1, 2")).fetchall()
    except Exception:
        return _EMPTY
    cells = [[int(h), (int(d) + 6) % 7, int(n)] for d, h, n in rows]  # Mon-first
    return {"available": bool(cells), "cells": cells, "source": "ai_action_audit"}


# ---------------------------------------------------------------------------
# Capacity — storage forecast (metrics store linear projection)
# ---------------------------------------------------------------------------
def capacity() -> dict[str, Any]:
    fc = pg_metrics.forecast("database_size", "30d")
    if not fc.get("available"):
        return _EMPTY
    cap_bytes = None
    try:
        cap_bytes = S.prom_scalar(
            f'max(kubelet_volume_stats_capacity_bytes{{namespace="{S.NS}"}})')
    except S.SourceError:
        pass
    return {"available": True, "history": fc.get("points") or [],
            "forecast": fc.get("forecast") or [],
            "capacity_bytes": cap_bytes, "source": "metrics store + kubelet"}


# ---------------------------------------------------------------------------
# Collector health — scrape/run success from the collector run log
# ---------------------------------------------------------------------------
def collector() -> dict[str, Any]:
    try:
        with engine.begin() as cx:
            rows = cx.execute(text(
                "select date_trunc('hour', started_at),"
                " count(*) filter (where status in ('ok','success','completed')),"
                " count(*)"
                " from collector_run where started_at > now() - interval '24 hours'"
                " group by 1 order by 1")).fetchall()
    except Exception:                    # noqa: BLE001 - table optional
        return _EMPTY
    series = [[int(ts.timestamp() * 1000), round(_f(ok) / _f(total, 1) * 100, 2)]
              for ts, ok, total in rows if total]
    return {"available": bool(series), "success_series": series,
            "source": "collector_run"}


# ---------------------------------------------------------------------------
# Upgrades / estate — real version inventory
# ---------------------------------------------------------------------------
def upgrades() -> dict[str, Any]:
    versions: dict[str, int] = {}
    try:
        with engine.begin() as cx:
            rows = cx.execute(text(
                "select coalesce(pg_version,'unknown'), count(*)"
                " from cluster_inventory group by 1")).fetchall()
        versions = {str(v): int(n) for v, n in rows}
    except Exception:                    # noqa: BLE001 - table optional
        versions = {}
    if not versions:
        try:
            row = S.sql_one("show server_version")
            if row:
                versions = {"PG " + row[0].split(".")[0]: 1}
        except S.SourceError:
            return _EMPTY
    return {"available": bool(versions),
            "estate": sorted(versions.items(), key=lambda kv: -kv[1]),
            "source": "cluster_inventory / server_version"}


# ---------------------------------------------------------------------------
# Health grid — aggregate for the NOC screen (score/pills + real series)
# ---------------------------------------------------------------------------
def health_grid() -> dict[str, Any]:
    from . import api_v1_screens  # late import: reuse the existing live grid

    base: dict[str, Any] = {}
    try:
        base = api_v1_screens._health_grid()  # noqa: SLF001 - same package
    except Exception:                    # noqa: BLE001
        base = {}
    load = pg_ash.db_load(minutes=24 * 60, dim="wait_class")
    live: dict[str, Any] = {}
    try:
        row = S.sql_one(
            "select count(*),"
            " (select setting::int from pg_settings where name = 'max_connections'),"
            " (select round(100.0 * sum(blks_hit) / nullif(sum(blks_hit + blks_read),0), 2)"
            "   from pg_stat_database),"
            " (select coalesce(max(extract(epoch from now() - xact_start)),0)::int"
            "   from pg_stat_activity where state <> 'idle' and pid <> pg_backend_pid())"
            " from pg_stat_activity")
        if row:
            live = {"connections": _i(row[0]), "max_connections": _i(row[1]),
                    "cache_hit_pct": _f(row[2]), "oldest_txn_seconds": _i(row[3])}
    except S.SourceError:
        pass
    payload = {
        "available": bool(base) or bool(load.get("series")) or bool(live),
        "grid": base,
        "load_series": load.get("series") or [],
        "conn_series": _series("connections", "24h"),
        "tps_series": _series("tps_commit", "24h"),
        "lag_series": _series("replication_lag", "24h"),
        "size_series": _series("database_size", "24h"),
        "live": live,
        "source": "health-grid + metrics store + ash sampler",
    }
    return payload


# ---------------------------------------------------------------------------
# Memory topology — pg_settings + live counts (fills only when measurable)
# ---------------------------------------------------------------------------
def memory_topology() -> dict[str, Any]:
    try:
        rows = S.sql(
            "select name, setting, coalesce(unit,'') from pg_settings where name in"
            " ('shared_buffers','effective_cache_size','wal_buffers','work_mem',"
            "  'maintenance_work_mem','temp_buffers','max_connections')")
        conn = S.sql_one(
            "select count(*) from pg_stat_activity where pid <> pg_backend_pid()")
    except S.SourceError:
        return _EMPTY
    mult = {"8kb": 8192, "kb": 1024, "mb": 1048576, "gb": 1073741824, "": 1}
    params: dict[str, Any] = {}
    for name, setting, unit in (r[:3] for r in rows):
        params[name] = _f(setting) * mult.get(unit.lower(), 1)
    cache_hit = None
    try:
        row = S.sql_one(
            "select round(100.0 * sum(blks_hit) / nullif(sum(blks_hit + blks_read),0), 2)"
            " from pg_stat_database")
        if row:
            cache_hit = _f(row[0])
    except S.SourceError:
        pass
    return {"available": bool(params), "params": params,
            "connections": _i(conn[0]) if conn else None,
            "cache_hit_pct": cache_hit, "source": "pg_settings + pg_stat_*"}


# ---------------------------------------------------------------------------
# Registry — modules with no real source return {"available": False}
# ---------------------------------------------------------------------------
_BUILDERS: dict[str, Callable[..., dict[str, Any]]] = {
    "advisor": advisor,
    "wal": wal,
    "backups": backups,
    "dr": dr,
    "logs": logs,
    "objects": objects,
    "cluster": cluster,
    "anomalies": anomalies,
    "heatmap": heatmap,
    "capacity": capacity,
    "collector": collector,
    "upgrades": upgrades,
    "health_grid": health_grid,
}
_VIEW_BUILDERS: dict[str, Callable[[str | None], dict[str, Any]]] = {
    "perf": perf,
    "replication": replication,
}
# Explicitly sourceless today (kept for a stable 404-free contract):
_NO_SOURCE = {"pgbouncer", "geo", "cost", "optimizer", "posture", "compliance",
              "access", "alerts_history", "platform", "storage", "sla"}


def build(module: str, view: str | None = None) -> dict[str, Any]:
    if module in _VIEW_BUILDERS:
        return _VIEW_BUILDERS[module](view)
    fn = _BUILDERS.get(module)
    if fn is None:
        if module in _NO_SOURCE:
            return {"available": False, "reason": "no live source for this module yet"}
        return {"available": False, "reason": "unknown module"}
    return fn()
