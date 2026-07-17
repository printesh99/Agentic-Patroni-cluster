"""Metrics Explorer — Prometheus-backed catalog with live SQL fallback."""
from __future__ import annotations

import re
import time
from typing import Any

from . import sources as S

# Curated catalog of metrics the explorer exposes. Each maps to a base metric
# name in Prometheus plus an aggregation and the dimension label to group by.
CATALOG: dict[str, dict[str, Any]] = {
    "connections": {"label": "Active sessions", "metric": "pg_stat_activity_sessions",
                    "agg": "sum", "group": "Sessions", "dimension": "state"},
    "database_size": {"label": "Database size (bytes)", "metric": "pg_database_size_bytes",
                      "agg": "sum", "group": "Storage", "dimension": "datname"},
    "replication_lag": {"label": "Replication lag", "metric": "pg_repl_lag",
                        "agg": "max", "group": "Replication", "dimension": "pod"},
    "locks": {"label": "Locks held", "metric": "pg_locks_count",
              "agg": "sum", "group": "Concurrency", "dimension": "datname"},
    "tps_commit": {"label": "Commits/sec", "metric": "pg_stat_database_xact_commit_total",
                   "agg": "rate", "group": "Throughput", "dimension": "datname"},
    "tps_rollback": {"label": "Rollbacks/sec", "metric": "pg_stat_database_xact_rollback_total",
                     "agg": "rate", "group": "Throughput", "dimension": "datname"},
    "buffers_alloc": {"label": "Buffers allocated", "metric": "pg_stat_bgwriter_buffers_alloc",
                      "agg": "sum", "group": "I/O", "dimension": "pod"},
    "wal_archived": {"label": "WAL archived", "metric": "pg_stat_archiver_archived_count_total",
                     "agg": "max", "group": "WAL", "dimension": "pod"},
}

_NS = lambda: f'namespace="{S.NS}"'
_RATE_CACHE: dict[str, tuple[float, float]] = {}
_LIVE_FALLBACK_KEYS = {
    "connections",
    "database_size",
    "replication_lag",
    "locks",
    "tps_commit",
    "tps_rollback",
    "buffers_alloc",
    "wal_archived",
}


def _promql(spec: dict[str, Any], by: str | None = None) -> str:
    base = f'{spec["metric"]}{{{_NS()}}}'
    if spec["agg"] == "rate":
        base = f"rate({spec['metric']}{{{_NS()}}}[5m])"
    fn = "max" if spec["agg"] == "max" else "sum"
    if by:
        return f"{fn} by ({by}) ({base})"
    return f"{fn} ({base})"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sql_scalar(query: str) -> float | None:
    row = S.sql_one(query)
    if not row:
        return None
    return _float(row[0])


def _live_rate(cache_key: str, query: str) -> float | None:
    first_ts = time.time()
    first = _sql_scalar(query)
    if first is None:
        return None

    prior = _RATE_CACHE.get(cache_key)
    _RATE_CACHE[cache_key] = (first_ts, first)
    if prior and first_ts > prior[0] and first >= prior[1]:
        return max(0.0, (first - prior[1]) / (first_ts - prior[0]))

    # Cold start: take a short direct sample so the live chart is populated on
    # the first page load, even before Prometheus has rate() history.
    time.sleep(1.0)
    second_ts = time.time()
    second = _sql_scalar(query)
    if second is None:
        return None
    _RATE_CACHE[cache_key] = (second_ts, second)
    if second_ts <= first_ts or second < first:
        return 0.0
    return max(0.0, (second - first) / (second_ts - first_ts))


def _live_value(metric: str) -> float | None:
    queries = {
        "connections": "select count(*) from pg_stat_activity",
        "database_size": (
            "select coalesce(sum(pg_database_size(datname)),0) "
            "from pg_database where datallowconn and not datistemplate"
        ),
        "replication_lag": (
            "select coalesce(max(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)),0) "
            "from pg_stat_replication"
        ),
        "locks": "select count(*) from pg_locks",
        "buffers_alloc": "select buffers_alloc from pg_stat_bgwriter",
        "wal_archived": "select archived_count from pg_stat_archiver",
    }
    if metric == "tps_commit":
        return _live_rate(
            "tps_commit",
            "select coalesce(sum(xact_commit),0) from pg_stat_database",
        )
    if metric == "tps_rollback":
        return _live_rate(
            "tps_rollback",
            "select coalesce(sum(xact_rollback),0) from pg_stat_database",
        )
    query = queries.get(metric)
    if not query:
        return None
    return _sql_scalar(query)


def live_series(metric: str, bucket_seconds: int = 60) -> dict[str, Any] | None:
    spec = CATALOG.get(metric)
    if not spec:
        return None
    try:
        value = _live_value(metric)
    except S.SourceError:
        return None
    if value is None:
        return None
    return {
        "available": True,
        "metric": metric,
        "source": "live_postgresql",
        "source_table": spec["metric"],
        "dimension": spec["dimension"],
        "bucket_seconds": bucket_seconds,
        "points": [],
        "live_value": value,
        "history_available": False,
    }


def _live_available(metric: str) -> bool:
    return metric in _LIVE_FALLBACK_KEYS


def _live_entity_rows(metric: str) -> list[dict[str, Any]]:
    queries = {
        "connections": (
            "select coalesce(state,'unknown') as entity, count(*)::float8 "
            "from pg_stat_activity group by 1 order by 2 desc"
        ),
        "database_size": (
            "select datname as entity, pg_database_size(datname)::float8 "
            "from pg_database where datallowconn and not datistemplate "
            "order by 2 desc"
        ),
        "replication_lag": (
            "select coalesce(application_name,'standby') as entity, "
            "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn),0)::float8 "
            "from pg_stat_replication order by 2 desc"
        ),
        "locks": (
            "select coalesce(d.datname,'shared') as entity, count(*)::float8 "
            "from pg_locks l left join pg_database d on d.oid = l.database "
            "group by 1 order by 2 desc"
        ),
        "wal_archived": "select 'primary' as entity, archived_count::float8 from pg_stat_archiver",
        "buffers_alloc": "select 'primary' as entity, buffers_alloc::float8 from pg_stat_bgwriter",
    }
    if metric in ("tps_commit", "tps_rollback"):
        value = _live_value(metric)
        return [{"entity": "cluster", "latest": value or 0.0}]
    query = queries.get(metric)
    if not query:
        return []
    rows = []
    for row in S.sql(query):
        if len(row) < 2:
            continue
        rows.append({"entity": row[0] or "(none)", "latest": _float(row[1])})
    if metric == "replication_lag" and not rows:
        rows.append({"entity": "no standby rows", "latest": 0.0})
    return rows


def live_appmon_trend(bucket_seconds: int = 60) -> dict[str, Any]:
    rows = _live_entity_rows("connections")
    return {
        "available": False,
        "history_available": False,
        "series": [],
        "current": rows,
        "source": "live_postgresql",
        "reason": "current PostgreSQL snapshot only; no historical samples",
    }


def catalog() -> dict[str, Any]:
    metrics = []
    for key, spec in CATALOG.items():
        try:
            has = bool(S.prom_query(_promql(spec)))
        except S.SourceError:
            has = False
        source = "prometheus" if has else "none"
        if not has and _live_available(key):
            has = True
            source = "live_postgresql"
        metrics.append({
            "key": key, "label": spec["label"], "agg": spec["agg"],
            "group": spec["group"], "has_data": has, "source": source,
            "hint": f'{spec["metric"]} · group by {spec["dimension"]}',
        })
    return {"available": True, "source": "prometheus", "metrics": metrics}


def _range_minutes(rng: str | None) -> int:
    if not rng:
        return 24 * 60
    m = re.match(r"(\d+)\s*([hmd])", rng.strip().lower())
    if not m:
        return 24 * 60
    return int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2)]


def series(metric: str, rng: str | None = None) -> dict[str, Any]:
    spec = CATALOG.get(metric)
    if not spec:
        return {"available": False, "metric": metric, "points": [], "reason": "unknown metric"}
    minutes = _range_minutes(rng)
    step = "60s" if minutes <= 60 else ("300s" if minutes <= 1440 else "900s")
    try:
        pts = S.prom_range(_promql(spec), minutes, step)
    except S.SourceError:
        pts = []
    if len(pts) <= 1:
        live = live_series(metric, int(step[:-1]))
        if live:
            return live
    return {
        "available": len(pts) > 1, "metric": metric, "source": "prometheus",
        "source_table": spec["metric"], "dimension": spec["dimension"],
        "bucket_seconds": int(step[:-1]),
        "points": [[int(ts * 1000), v] for ts, v in pts],
    }


def entities(metric: str) -> dict[str, Any]:
    spec = CATALOG.get(metric)
    if not spec:
        return {"available": False, "metric": metric, "entities": []}
    by = spec["dimension"]
    minutes = 60
    rows = []
    try:
        result = S.prom_query(_promql(spec, by=by))
    except S.SourceError:
        result = []
    for r in result:
        ent = r["metric"].get(by, "(none)")
        latest = float(r["value"][1])
        rows.append({"entity": ent, "latest": latest, "avg_value": latest,
                     "max_value": latest, "samples": 1})
    if not rows:
        try:
            rows = [
                {
                    "entity": row["entity"],
                    "latest": row["latest"],
                    "avg_value": row["latest"],
                    "max_value": row["latest"],
                    "samples": 1,
                }
                for row in _live_entity_rows(metric)
            ]
        except S.SourceError:
            rows = []
    return {"available": bool(rows), "metric": metric, "dimension": by, "entities": rows}


def forecast(metric: str, rng: str | None = None) -> dict[str, Any]:
    """Naive linear projection from the trailing trend (planning signal only)."""
    s = series(metric, rng or "24h")
    pts = s["points"]
    if len(pts) < 3:
        return {"available": False, "metric": metric, "points": pts, "forecast": []}
    # least-squares slope over index
    n = len(pts)
    xs = list(range(n))
    ys = [p[1] for p in pts]
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs) or 1
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    step_ms = pts[1][0] - pts[0][0]
    last_ts = pts[-1][0]
    fc = [[last_ts + step_ms * k, round(ys[-1] + slope * k, 3)] for k in range(1, 13)]
    return {"available": True, "metric": metric, "source": "prometheus (linear projection)",
            "points": pts, "forecast": fc, "slope_per_bucket": round(slope, 4)}
