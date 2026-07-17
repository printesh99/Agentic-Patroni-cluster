"""Evidence collectors for the Agentic AI DBA recommendation loop."""
from __future__ import annotations

import os
import re
from typing import Any

from .. import loki
from .. import pg_perf
from .. import sources as S
from ..collectors import patroni_collector, postgres_collector, prometheus_collector


LOG_PATTERNS = [
    "ERROR",
    "FATAL",
    "PANIC",
    "deadlock detected",
    "out of memory",
    "remaining connection slots are reserved",
    "canceling statement due to statement timeout",
    "could not serialize access",
    "temporary file",
    "archive command failed",
    "pgBackRest error",
    "Patroni failover",
    "Patroni switchover",
    "replica lag",
    "OOMKilled",
]

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(password\s*['=:]\s*)[^'\s]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(token\s*['=:]\s*)[A-Za-z0-9._-]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._-]+"), r"\1***REDACTED***"),
    (re.compile(r"postgres(?:ql)?://[^:\s]+:[^@\s]+@"), "postgres://***REDACTED***@"),
]


def _redact(line: str) -> str:
    out = str(line or "")
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out[:1000]


def _safe_call(name: str, fn, *args, **kwargs) -> dict[str, Any]:
    try:
        payload = fn(*args, **kwargs)
        if isinstance(payload, dict):
            return payload
        return {"available": True, "source": name, "payload": payload}
    except Exception as exc:
        return {"available": False, "source": name, "warnings": [str(exc)], "error": str(exc)}


def collect_metrics(lookback_minutes: int = 30) -> dict[str, Any]:
    payload = _safe_call("prometheus", prometheus_collector.collect)
    if not payload.get("available"):
        payload.setdefault("warnings", []).append("Prometheus is not configured or returned no data")
    payload["lookback_minutes"] = lookback_minutes
    return payload


def collect_patroni() -> dict[str, Any]:
    return _safe_call("patroni", patroni_collector.collect)


def _sum_matrix(matrix: list[dict[str, Any]]) -> int:
    total = 0
    for series in matrix:
        for _, value in series.get("values", []):
            try:
                total += int(float(value))
            except (TypeError, ValueError):
                continue
    return total


def collect_loki_logs(lookback_minutes: int = 30, limit: int = 100) -> dict[str, Any]:
    if not (os.environ.get("PGC_LOKI_URL") or os.environ.get("LOKI_URL") or os.environ.get("LOKI_BASE_URL")):
        return {
            "available": False,
            "source": "loki",
            "lookback_minutes": lookback_minutes,
            "warnings": ["Loki URL is not configured; log evidence skipped"],
            "patterns": {},
            "samples": [],
        }
    end = loki.now_ns()
    start = end - int(lookback_minutes) * 60 * loki.NS_PER_S
    selector = f'{{namespace="{S.NS}"}}'
    pattern_counts: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    warnings: list[str] = []
    for pattern in LOG_PATTERNS:
        try:
            safe_pattern = pattern.replace("\\", "\\\\").replace('"', '\\"')
            matrix = loki.metric_range(f'count_over_time({selector} |~ "(?i){safe_pattern}" [{int(lookback_minutes)}m])', start, end)
            count = _sum_matrix(matrix)
            pattern_counts[pattern] = count
        except Exception as exc:
            warnings.append(f"pattern {pattern} unavailable: {exc}")
            pattern_counts[pattern] = 0
    try:
        query = selector + ' |~ "(?i)error|fatal|panic|deadlock|out of memory|connection slots|statement timeout|archive command failed|pgbackrest|failover|switchover|replica lag|oomkilled"'
        streams = loki.query_range(query, start, end, limit=limit)
        for stream in streams:
            labels = stream.get("stream") or {}
            for ts, line in stream.get("values") or []:
                samples.append({"ts": ts, "labels": labels, "line": _redact(line)})
                if len(samples) >= limit:
                    break
            if len(samples) >= limit:
                break
    except Exception as exc:
        warnings.append(f"log sample query unavailable: {exc}")
    return {
        "available": bool(samples or any(pattern_counts.values())),
        "source": "loki",
        "lookback_minutes": lookback_minutes,
        "patterns": pattern_counts,
        "samples": samples,
        "warnings": warnings,
    }


def _query_rows(name: str, sql: str, dbname: str = "postgres", limit: int | None = None) -> dict[str, Any]:
    try:
        rows = S.sql(sql, dbname=dbname)
        if limit is not None:
            rows = rows[:limit]
        return {"available": True, "source": name, "rows": rows}
    except Exception as exc:
        return {"available": False, "source": name, "rows": [], "warnings": [str(exc)], "error": str(exc)}


def collect_sql_stats(database_name: str | None = None, limit: int = 50) -> dict[str, Any]:
    dbname = database_name or "postgres"
    payload: dict[str, Any] = {
        "source": "postgresql",
        "database_name": database_name,
        "postgres_snapshot": _safe_call("postgres_collector", postgres_collector.collect),
        "top_sql_total": _safe_call("pg_stat_statements_total", pg_perf.topsql, "total", database_name, limit),
        "top_sql_mean": _safe_call("pg_stat_statements_mean", pg_perf.topsql, "mean", database_name, limit),
        "activity": _safe_call("pg_stat_activity", pg_perf.session_summary),
        "idle_in_transaction": _safe_call("pg_stat_activity_idle_in_xact", pg_perf.idle_in_transaction, 300),
        "bloat": _safe_call("pg_stat_user_tables_bloat", pg_perf.bloat, database_name, limit),
        "vacuum": _safe_call("pg_stat_user_tables_vacuum", pg_perf.vacuum, database_name, limit),
        "index_usage": _safe_call("pg_stat_user_indexes", pg_perf.index_advisor, database_name, limit),
        "blocking": _query_rows(
            "pg_locks_blocking",
            "select blocked.pid::text, blocked.usename, blocked.datname, blocking.pid::text, blocking.usename, "
            "coalesce(blocked.wait_event_type,''), coalesce(blocked.wait_event,''), "
            "replace(left(blocked.query,500),chr(10),' ') "
            "from pg_locks bl "
            "join pg_stat_activity blocked on blocked.pid = bl.pid "
            "join pg_locks kl on kl.locktype = bl.locktype and kl.database is not distinct from bl.database "
            "and kl.relation is not distinct from bl.relation and kl.page is not distinct from bl.page "
            "and kl.tuple is not distinct from bl.tuple and kl.virtualxid is not distinct from bl.virtualxid "
            "and kl.transactionid is not distinct from bl.transactionid and kl.classid is not distinct from bl.classid "
            "and kl.objid is not distinct from bl.objid and kl.objsubid is not distinct from bl.objsubid "
            "and kl.pid <> bl.pid "
            "join pg_stat_activity blocking on blocking.pid = kl.pid "
            "where not bl.granted and kl.granted limit 50",
            dbname=dbname,
        ),
        "replication": _query_rows(
            "pg_stat_replication",
            "select coalesce(application_name,''), coalesce(client_addr::text,''), coalesce(state,''), "
            "coalesce(sync_state,''), coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn),0)::text "
            "from pg_stat_replication",
            dbname=dbname,
        ),
        "database_size": _query_rows(
            "pg_database_size",
            "select datname, pg_database_size(datname)::text from pg_database where datallowconn order by 2::bigint desc limit 25",
            dbname="postgres",
        ),
        "sequential_scan_candidates": _query_rows(
            "pg_stat_user_tables_seq_scan",
            "select schemaname, relname, seq_scan::text, idx_scan::text, n_live_tup::text, "
            "n_dead_tup::text, coalesce(last_autoanalyze::text,''), coalesce(last_autovacuum::text,'') "
            "from pg_stat_user_tables "
            "where n_live_tup > 10000 and seq_scan > greatest(idx_scan, 10) "
            "order by seq_scan desc limit 50",
            dbname=dbname,
        ),
    }
    payload["available"] = any(
        isinstance(v, dict) and v.get("available")
        for k, v in payload.items()
        if k not in {"source", "database_name"}
    )
    return payload


def collect_all(cluster_name: str | None = None, database_name: str | None = None,
                lookback_minutes: int = 30) -> dict[str, Any]:
    return {
        "cluster_name": cluster_name or S.CLUSTER_NAME,
        "namespace": S.NS,
        "database_name": database_name,
        "lookback_minutes": int(lookback_minutes),
        "metrics": collect_metrics(lookback_minutes),
        "loki": collect_loki_logs(lookback_minutes),
        "postgres": collect_sql_stats(database_name),
        "patroni": collect_patroni(),
    }
