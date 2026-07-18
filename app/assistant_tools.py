"""Deterministic answer tools for the read-only DBA assistant.

Each tool answers a class of *factual* question straight from live data (SQL /
Prometheus / Patroni) — instant and exact — so common questions never hit the
slow local LLM. A **tokenized** intent router dispatches; anything unmatched
(genuine "why did X happen" RCA / free-form) returns None and falls through to
the existing log+LLM path in ``log_ai.ask()``.

Design notes (from the 495-question eval, 2026-07-09):
- Routing is by whole-word TOKENS, never ``substring in string`` — the old
  matcher misfired three ways: "sync" inside "synchronous_commit" (config Q
  misrouted to topology), "reason" inside "reasonable" (forced the LLM), and a
  too-narrow cluster-state vocabulary (streaming/node/quorum/... timed out).
- Config intent is checked BEFORE live-state, so a question naming a GUC goes to
  pg_settings, not the Patroni topology answer.
- Every tool is wrapped so any exception returns None (fall through) — a bad
  query can never make the assistant worse than the pre-tool behavior.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from . import sources as S

# --------------------------------------------------------------------------
# tokenization + small matcher helpers
# --------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def tokens(q: str) -> set[str]:
    return set(_TOKEN_RE.findall((q or "").lower()))


def _any(tk: set[str], *words: str) -> bool:
    return any(w in tk for w in words)


def _phrase(q: str, *phrases: str) -> bool:
    ql = (q or "").lower()
    return any(p in ql for p in phrases)


_COMMON_TYPOS = {
    "shared_bufer": "shared_buffers", "sesions": "sessions", "curent": "current",
    "blockng": "blocking", "tabels": "tables", "vacum": "vacuum",
    "analyse": "analyze", "readness": "readiness", "scor": "score",
    "conecton": "connection", "databse": "database", "storag": "storage",
    "higest": "highest", "memry": "memory", "utilisation": "utilization",
    "evidnce": "evidence",
}


def _normalize_common_typos(q: str) -> str:
    normalized = q or ""
    for wrong, right in _COMMON_TYPOS.items():
        normalized = re.sub(rf"\b{re.escape(wrong)}\b", right, normalized,
                            flags=re.IGNORECASE)
    return normalized


# RCA / "why" questions must go to the LLM — matched as whole tokens/phrases so
# "reasonable" no longer trips "reason".
_RCA_TOKENS = {"why", "cause", "caused", "reason", "diagnose", "investigate",
               "troubleshoot", "debug", "root"}
_RCA_PHRASES = ("root cause", "why did", "why is", "why are", "what caused",
                "what is causing", "explain why")


def is_rca(q: str) -> bool:
    return _phrase(q, *_RCA_PHRASES) or bool(tokens(q) & _RCA_TOKENS)


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _rows(sql: str, dbname: str = "postgres") -> list[list[str]]:
    return S.sql(sql, dbname=dbname)


_APP_DB_CACHE: list[str] = []


def _app_db() -> str:
    """The application database where per-DB views (pg_stat_statements,
    pg_stat_user_tables) actually have data — those views only show the CURRENT
    database, and the tools otherwise connect to 'postgres' (empty there). Picks
    the largest non-template, non-'postgres' DB; cached; falls back to 'postgres'."""
    if _APP_DB_CACHE:
        return _APP_DB_CACHE[0]
    db = "postgres"
    try:
        row = S.sql_one("select datname from pg_database where not datistemplate "
                        "and datname <> 'postgres' order by pg_database_size(datname) desc limit 1")
        if row and row[0]:
            db = row[0]
    except Exception:
        pass
    _APP_DB_CACHE.append(db)
    return db


# --------------------------------------------------------------------------
# config_settings  →  pg_settings
# --------------------------------------------------------------------------
# Common GUCs, so routing needs no DB call. Extend freely; unknown params simply
# fall through (and can still be caught by the generic "setting/parameter" trigger).
_GUCS = {
    "shared_buffers", "work_mem", "maintenance_work_mem", "effective_cache_size",
    "max_connections", "max_wal_size", "min_wal_size", "checkpoint_timeout",
    "checkpoint_completion_target", "wal_level", "max_worker_processes",
    "max_parallel_workers", "max_parallel_workers_per_gather", "autovacuum",
    "autovacuum_vacuum_scale_factor", "autovacuum_vacuum_cost_limit",
    "autovacuum_freeze_max_age", "autovacuum_max_workers", "random_page_cost",
    "seq_page_cost", "effective_io_concurrency", "synchronous_commit",
    "synchronous_standby_names", "hot_standby_feedback", "wal_keep_size",
    "max_replication_slots", "max_wal_senders", "max_logical_replication_workers",
    "statement_timeout", "idle_in_transaction_session_timeout", "lock_timeout",
    "log_min_duration_statement", "default_statistics_target", "shared_preload_libraries",
    "wal_compression", "wal_buffers", "temp_buffers", "max_prepared_transactions",
    "max_standby_streaming_delay", "fsync", "full_page_writes", "commit_delay",
    "default_transaction_isolation", "track_activity_query_size", "jit",
}
_CONFIG_TRIGGERS = {"setting", "settings", "parameter", "parameters", "configured",
                    "configuration", "guc"}


def _config_params(q: str) -> list[str]:
    tk = tokens(q)
    named = sorted(tk & _GUCS)
    return named


def config_tool(q: str) -> dict[str, Any] | None:
    params = _config_params(q)
    tk = tokens(q)
    # Fleet: "which parameters were changed from their defaults?"
    if _phrase(q, "from default", "from their default", "from the default", "non-default",
               "changed from", "differ from default", "not at default", "modified from"):
        changed = _rows(
            "select name, setting, coalesce(unit,''), source from pg_settings "
            "where source not in ('default','override') and setting is distinct from boot_val "
            "order by name limit 60")
        if changed:
            body = "; ".join(f"{r[0]}={r[1]}{((' '+r[2]) if r[2] else '')} [{r[3]}]" for r in changed)
            return {"answer": f"{len(changed)} parameters changed from default: {body}.",
                    "model": "live-data (pg_settings)", "intent": "config",
                    "evidence": {"changed": changed}}
    # Route only when a real GUC is named, or it's clearly a config question
    # mentioning "value of"/"setting" (avoids grabbing unrelated questions).
    if not params and not (tk & _CONFIG_TRIGGERS and _phrase(q, "value of", "set to", "configured")):
        return None
    if params:
        inlist = ",".join("'" + p.replace("'", "") + "'" for p in params)
        where = f"name IN ({inlist})"
    else:
        return None  # trigger-only with no concrete param: let LLM handle advice
    rows = _rows(
        "select name, setting, coalesce(unit,''), coalesce(short_desc,''), source, boot_val "
        f"from pg_settings where {where} order by name")
    if not rows:
        return None
    parts = []
    for r in rows:
        name, val, unit, desc, source, boot = r[0], r[1], r[2], r[3], r[4], r[5]
        u = f" {unit}" if unit else ""
        chg = "" if val == boot else f" (default {boot}{u}; changed via {source})"
        parts.append(f"{name} = {val}{u}{chg}. {desc}".strip())
    return {"answer": " ".join(parts),
            "model": "live-data (pg_settings)",
            "intent": "config", "evidence": {"pg_settings": rows}}


# --------------------------------------------------------------------------
# CPU capacity/current usage -> OpenShift pod specs + Prometheus
# --------------------------------------------------------------------------
def _cpu_cores(quantity: Any) -> float | None:
    """Convert a Kubernetes CPU quantity to cores without guessing."""
    raw = str(quantity or "").strip()
    if not raw:
        return None
    factors = {"n": 1e-9, "u": 1e-6, "m": 1e-3}
    try:
        if raw[-1:] in factors:
            return float(raw[:-1]) * factors[raw[-1]]
        return float(raw)
    except (TypeError, ValueError):
        return None


def _fmt_cores(value: float | None) -> str:
    if value is None:
        return "not set"
    if value == int(value):
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _promql_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def cpu_capacity_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    if "cpu" not in tk and not _any(tk, "core", "cores", "vcpu", "vcpus"):
        return None
    if is_rca(q):
        return None
    factual = (
        _any(tk, "many", "much", "core", "cores", "vcpu", "vcpus", "allocated",
             "allocation", "request", "requests", "limit", "limits", "capacity",
             "usage", "utilization", "utilisation")
        or _phrase(q, "cpu on my", "cpu for my", "cpu for the", "current cpu")
    )
    if not factual:
        return None

    collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    selector = f"postgres-operator.crunchydata.com/cluster={S.CLUSTER_NAME}"
    try:
        document = S.kubectl_json(["-n", S.NS, "get", "pods", "-l", selector])
    except Exception as exc:
        return {
            "answer": (
                f"CPU allocation for {S.CLUSTER_NAME} could not be read from OpenShift pod "
                f"specifications. No CPU count is inferred. Source status: {type(exc).__name__}; "
                f"collected {collected_at}."
            ),
            "model": "live-data (OpenShift pod specs unavailable)",
            "intent": "cpu_capacity",
            "evidence": {
                "cluster": S.CLUSTER_NAME,
                "namespace": S.NS,
                "collected_at_utc": collected_at,
                "pod_specs_available": False,
                "prometheus_available": False,
            },
        }

    pods: list[dict[str, Any]] = []
    for item in document.get("items", []):
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        container = next(
            (c for c in (spec.get("containers") or []) if c.get("name") == S.DB_CONTAINER),
            None,
        )
        if container is None:
            continue
        resources = container.get("resources") or {}
        requests = resources.get("requests") or {}
        limits = resources.get("limits") or {}
        pods.append({
            "name": (item.get("metadata") or {}).get("name"),
            "phase": status.get("phase"),
            "request_cores": _cpu_cores(requests.get("cpu")),
            "limit_cores": _cpu_cores(limits.get("cpu")),
        })

    if not pods:
        return {
            "answer": (
                f"No {S.DB_CONTAINER} containers were found in the current pod specification "
                f"set for {S.CLUSTER_NAME}; no CPU count is inferred. Collected {collected_at}."
            ),
            "model": "live-data (OpenShift pod specs)",
            "intent": "cpu_capacity",
            "evidence": {
                "cluster": S.CLUSTER_NAME,
                "namespace": S.NS,
                "collected_at_utc": collected_at,
                "pod_specs_available": True,
                "database_pods": [],
                "prometheus_available": False,
            },
        }

    request_values = [p["request_cores"] for p in pods if p["request_cores"] is not None]
    limit_values = [p["limit_cores"] for p in pods if p["limit_cores"] is not None]
    total_request = sum(request_values) if len(request_values) == len(pods) else None
    total_limit = sum(limit_values) if len(limit_values) == len(pods) else None

    usage_cores = None
    prom_error = None
    expression = (
        'sum(rate(container_cpu_usage_seconds_total{namespace="'
        + _promql_value(S.NS)
        + '",pod=~"'
        + _promql_value(re.escape(S.CLUSTER_NAME) + ".*")
        + '",container="'
        + _promql_value(S.DB_CONTAINER)
        + '"}[5m]))'
    )
    try:
        usage_cores = S.prom_scalar(expression)
    except Exception as exc:
        prom_error = type(exc).__name__

    per_pod = "; ".join(
        f"{p['name']}: request {_fmt_cores(p['request_cores'])}, limit {_fmt_cores(p['limit_cores'])} cores"
        for p in pods
    )
    parts = [
        f"{S.CLUSTER_NAME} has {len(pods)} database pod(s).",
        f"Total CPU request: {_fmt_cores(total_request)} cores; total CPU limit: {_fmt_cores(total_limit)} cores.",
        f"Per pod — {per_pod}.",
    ]
    if usage_cores is not None:
        parts.append(f"Current five-minute CPU usage: {_fmt_cores(usage_cores)} cores from Prometheus.")
    else:
        parts.append("Current five-minute CPU usage is unavailable from Prometheus; allocation values remain exact pod-spec data.")
    parts.append(f"Collected {collected_at} from OpenShift pod specifications"
                 + (" and Prometheus." if usage_cores is not None else "."))
    model = "live-data (OpenShift pod specs + Prometheus)" if usage_cores is not None else "live-data (OpenShift pod specs)"
    return {
        "answer": " ".join(parts),
        "model": model,
        "intent": "cpu_capacity",
        "evidence": {
            "cluster": S.CLUSTER_NAME,
            "namespace": S.NS,
            "container": S.DB_CONTAINER,
            "collected_at_utc": collected_at,
            "pod_specs_available": True,
            "database_pods": pods,
            "pod_count": len(pods),
            "total_request_cores": total_request,
            "total_limit_cores": total_limit,
            "prometheus_available": usage_cores is not None,
            "current_usage_cores_5m": usage_cores,
            "prometheus_error_category": prom_error,
        },
    }


def _memory_gib(quantity: Any) -> float | None:
    raw = str(quantity or "").strip()
    if not raw:
        return None
    binary = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}
    decimal = {"K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12}
    try:
        for suffix, factor in binary.items():
            if raw.endswith(suffix):
                return float(raw[:-len(suffix)]) * factor / 2**30
        for suffix, factor in decimal.items():
            if raw.endswith(suffix):
                return float(raw[:-len(suffix)]) * factor / 2**30
        return float(raw) / 2**30
    except (TypeError, ValueError):
        return None


def memory_capacity_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    if not (_any(tk, "memory", "ram") and
            (_any(tk, "usage", "utilization", "request", "requests", "limit", "limits",
                  "current", "configured") or _phrase(q, "memory use"))):
        return None
    if is_rca(q):
        return None
    collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    selector = f"postgres-operator.crunchydata.com/cluster={S.CLUSTER_NAME}"
    try:
        document = S.kubectl_json(["-n", S.NS, "get", "pods", "-l", selector])
    except Exception as exc:
        return {
            "answer": f"Memory evidence is unavailable from Kubernetes ({type(exc).__name__}); no value is inferred.",
            "model": "live-data (Kubernetes memory evidence unavailable)",
            "intent": "cpu_capacity",
            "evidence": {"collected_at_utc": collected_at, "kubernetes_available": False},
        }
    pods = []
    for item in document.get("items", []):
        container = next((c for c in ((item.get("spec") or {}).get("containers") or [])
                          if c.get("name") == S.DB_CONTAINER), None)
        if not container:
            continue
        resources = container.get("resources") or {}
        pods.append({
            "name": (item.get("metadata") or {}).get("name"),
            "request_gib": _memory_gib((resources.get("requests") or {}).get("memory")),
            "limit_gib": _memory_gib((resources.get("limits") or {}).get("memory")),
        })
    usage_gib = None
    expression = (
        'sum(container_memory_working_set_bytes{namespace="' + _promql_value(S.NS)
        + '",pod=~"' + _promql_value(re.escape(S.CLUSTER_NAME) + ".*")
        + '",container="' + _promql_value(S.DB_CONTAINER) + '"}) / 1073741824'
    )
    try:
        usage_gib = S.prom_scalar(expression)
    except Exception:
        pass
    try:
        settings = _rows(
            "select name, setting, coalesce(unit,'') from pg_settings "
            "where name in ('shared_buffers','work_mem','maintenance_work_mem','effective_cache_size') "
            "order by name")
    except Exception:
        settings = []
    total_request = sum(p["request_gib"] for p in pods if p["request_gib"] is not None)
    total_limit = sum(p["limit_gib"] for p in pods if p["limit_gib"] is not None)
    per_pod = "; ".join(
        f"{p['name']}: request {p['request_gib']:.1f} GiB, limit {p['limit_gib']:.1f} GiB"
        for p in pods if p["request_gib"] is not None and p["limit_gib"] is not None)
    setting_text = "; ".join(f"{r[0]}={r[1]}{r[2]}" for r in settings)
    live = (f"Live memory working-set utilization: {usage_gib:.1f} GiB from Prometheus. "
            if usage_gib is not None else
            "Live memory utilization is unavailable from Prometheus; no usage is inferred. ")
    return {
        "answer": (f"Memory capacity from Kubernetes: total request {total_request:.1f} GiB, "
                   f"total limit {total_limit:.1f} GiB. {per_pod}. {live}"
                   f"PostgreSQL memory settings: {setting_text or 'unavailable'}. "
                   f"Collected {collected_at}."),
        "model": "live-data (Kubernetes + Prometheus memory)",
        "intent": "cpu_capacity",
        "evidence": {"kubernetes_available": True, "pods": pods,
                     "total_request_gib": total_request, "total_limit_gib": total_limit,
                     "prometheus_available": usage_gib is not None,
                     "working_set_gib": usage_gib, "pg_settings": settings,
                     "collected_at_utc": collected_at},
    }


# --------------------------------------------------------------------------
# metrics_trends  →  Prometheus range for templated metrics, else live snapshot
# --------------------------------------------------------------------------
# phrase -> (canonical_key, source). "prom" keys reuse the existing series
# templates (real historical trend); "sql:<expr>" gives an exact current value
# with a clear "snapshot" note when no time-series is templated.
_METRIC_MAP: list[tuple[tuple[str, ...], str, str]] = [
    (("active connection", "active session"), "active_connections",
     "sql:select count(*) from pg_stat_activity where state='active'"),
    (("connection count", "connection trend", "number of connection", "connections", "sessions"),
     "connections", "prom:connections"),
    (("cache hit",), "cache_hit_ratio",
     "sql:select round(100*sum(blks_hit)::numeric/nullif(sum(blks_hit)+sum(blks_read),0),2) from pg_stat_database"),
    (("transactions per second", "tps", "commits per second"), "tps", "prom:tps"),
    (("rollbacks per second", "rollback"), "rollbacks",
     "sql:select sum(xact_rollback) from pg_stat_database"),
    (("checkpoint",), "checkpoints",
     "sql:select num_timed + num_requested from pg_stat_checkpointer"),
    (("wal generation", "wal rate"), "wal",
     "sql:select pg_wal_lsn_diff(pg_current_wal_lsn(),'0/0')::bigint"),
    (("temp file", "temp usage"), "temp_files",
     "sql:select sum(temp_files), sum(temp_bytes) from pg_stat_database"),
    (("deadlock",), "deadlocks", "sql:select sum(deadlocks) from pg_stat_database"),
    (("tuples fetched", "tuple fetch"), "tup_fetched", "sql:select sum(tup_fetched) from pg_stat_database"),
    (("tuples returned", "tuple return"), "tup_returned", "sql:select sum(tup_returned) from pg_stat_database"),
    (("buffers written", "buffer"), "buffers",
     "sql:select (select buffers_written from pg_stat_checkpointer) "
     "+ (select buffers_clean from pg_stat_bgwriter)"),
    (("disk read", "block read", "read throughput"), "blks_read",
     "sql:select sum(blks_read) from pg_stat_database"),
    (("index scan",), "idx_scan", "sql:select sum(idx_scan) from pg_stat_user_tables"),
    (("storage", "database size", "db size", "disk usage"), "storage_bytes", "prom:storage_bytes"),
    (("replication lag",), "replication_lag", "repl"),  # handled by the live-state repl answer
]
_METRIC_TRIGGERS = {"trend", "trends", "plot", "graph", "chart", "peak",
                    "history", "historical", "abnormal", "rate"}
_RANGE_MAP = [("7 day", 7 * 1440), ("24 hour", 1440), ("6 hour", 360), ("hour", 60)]


def _range_minutes(q: str) -> int:
    ql = (q or "").lower()
    for phrase, mins in _RANGE_MAP:
        if phrase in ql:
            return mins
    return 1440


def _match_metric(q: str) -> tuple[str, str] | None:
    ql = (q or "").lower().replace("-", " ")
    for phrases, key, src in _METRIC_MAP:
        if any(p in ql for p in phrases):
            return key, src
    return None


def _is_metric_query(q: str) -> bool:
    """True for trend/rate/range-framed questions — these go to the metrics tool
    BEFORE the state tools so 'plot the connection count over 24h' becomes a
    Prometheus trend, while 'how many connections are active' stays a session
    snapshot."""
    if tokens(q) & _METRIC_TRIGGERS:
        return True
    return _phrase(q, "per second", "over the last", "for the last", "last 24",
                   "last 6", "last 7", "last hour", "last week", "past 24", "past hour",
                   "over 24")


# When a prom template returns no series (some exporters don't emit it), fall
# back to an exact SQL snapshot instead of dropping to the slow LLM.
_PROM_FALLBACK_SQL = {
    "tps": "select sum(xact_commit) from pg_stat_database",
    "connections": "select count(*) from pg_stat_activity",
    "storage_bytes": "select sum(pg_database_size(datname)) from pg_database where not datistemplate",
}


def metrics_tool(q: str) -> dict[str, Any] | None:
    m = _match_metric(q)
    if not m:
        return None
    key, src = m
    if src == "repl":
        return None  # let the replication/live-state answer handle lag
    mins = _range_minutes(q)
    sql = None
    if src.startswith("prom:"):
        metric = src.split(":", 1)[1]
        try:
            from . import api_clusters
            expr = api_clusters._metric_promql(metric)
            pts = S.prom_range(expr, mins, "300s") if expr else []
        except Exception:
            pts = []
        vals = [_num(v) for _, v in pts if v is not None]
        if vals:
            first, last = vals[0], vals[-1]
            trend = "rising" if last > first else "falling" if last < first else "flat"
            return {"answer": (f"{key.replace('_',' ')} over the last {mins//60}h: "
                               f"min={min(vals):.0f}, max={max(vals):.0f}, avg={sum(vals)/len(vals):.0f}, "
                               f"latest={last:.0f} ({trend}, {len(vals)} samples). Source: Prometheus."),
                    "model": "live-data (Prometheus series)", "intent": "metrics",
                    "evidence": {"metric": metric, "minutes": mins, "points": pts[-50:]}}
        sql = _PROM_FALLBACK_SQL.get(metric)  # prom empty -> exact SQL snapshot
    elif src.startswith("sql:"):
        sql = src.split(":", 1)[1]
    # SQL snapshot (exact current/cumulative value; historical trend not templated)
    if sql:
        try:
            row = S.sql_one(sql)
        except Exception:
            return None
        if not row:
            return None
        val = ", ".join(str(c) for c in row)
        return {"answer": (f"Prometheus returned no usable {mins//60}h series for "
                           f"{key.replace('_',' ')}; no historical trend is inferred. "
                           f"Current SQL snapshot: {val}."),
                "model": "live-data (SQL snapshot; Prometheus series unavailable)",
                "intent": "metrics",
                "evidence": {"metric": key, "row": row, "prometheus_attempted": True,
                             "prometheus_series_available": False}}
    return None


# --------------------------------------------------------------------------
# sessions / connections  →  pg_stat_activity
# --------------------------------------------------------------------------
def sessions_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    if not (_any(tk, "connection", "connections", "session", "sessions", "idle", "activity",
                 "prepared")
            or _phrase(q, "longest-running", "long-running", "longest running",
                       "running query", "oldest query")):
        return None
    if _any(tk, "why", "cause"):
        return None
    try:
        by_state = _rows("select coalesce(state,'<none>'), count(*) from pg_stat_activity "
                         "group by 1 order by 2 desc")
        totals = S.sql_one("select count(*), "
                           "count(*) filter (where state='active'), "
                           "count(*) filter (where state='idle in transaction'), "
                           "(select setting::int from pg_settings where name='max_connections') "
                           "from pg_stat_activity")
    except Exception:
        return None
    if not totals:
        return None
    total, active, idle_tx, maxc = totals[0], totals[1], totals[2], totals[3]
    states = "; ".join(f"{r[0]}: {r[1]}" for r in by_state)
    return {"answer": (f"{total} connections (of max_connections {maxc}): {active} active, "
                       f"{idle_tx} idle-in-transaction. By state — {states}."),
            "model": "live-data (pg_stat_activity)", "intent": "sessions",
            "evidence": {"by_state": by_state, "totals": totals}}


# --------------------------------------------------------------------------
# locks / blocking  →  pg_locks + pg_stat_activity
# --------------------------------------------------------------------------
def locks_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    if not (_any(tk, "lock", "locks", "blocked", "blocking", "deadlock", "deadlocks", "contention")
            or _phrase(q, "exclusivelock", "accessexclusive", "lock wait", "lock mode", "sharelock")):
        return None
    if _any(tk, "why", "cause") or "leader" in tk:  # "leader lock" is a Patroni concept -> live-state
        return None
    try:
        blocked = _rows(
            "select bl.pid, a.usename, now()-a.query_start, left(a.query,80), "
            "bl2.pid "
            "from pg_locks bl join pg_stat_activity a on a.pid=bl.pid "
            "join pg_locks bl2 on bl2.locktype=bl.locktype and bl2.database is not distinct from bl.database "
            "and bl2.relation is not distinct from bl.relation and bl2.pid<>bl.pid and bl2.granted "
            "where not bl.granted order by 3 desc limit 10")
        ndeadlock = S.sql_one("select coalesce(sum(deadlocks),0) from pg_stat_database")
    except Exception:
        return None
    dl = ndeadlock[0] if ndeadlock else "0"
    if not blocked:
        return {"answer": f"No blocked queries right now. Cumulative deadlocks since stats reset: {dl}.",
                "model": "live-data (pg_locks)", "intent": "locks",
                "evidence": {"blocked": [], "deadlocks": dl}}
    rows = "; ".join(f"pid {r[0]} ({r[1]}) waited {r[2]} on pid {r[4]}: {r[3]}" for r in blocked)
    return {"answer": f"{len(blocked)} blocked session(s): {rows}. Cumulative deadlocks: {dl}.",
            "model": "live-data (pg_locks)", "intent": "locks",
            "evidence": {"blocked": blocked, "deadlocks": dl}}


# --------------------------------------------------------------------------
# slow queries  →  pg_stat_statements
# --------------------------------------------------------------------------
# pg_stat_statements is cluster-wide (same rows in every DB), so any DB with the
# extension works; we query the app DB first, 'postgres' as fallback. The bank
# asks for many *dimensions* of "top queries" — each maps to a different sort
# column (all present in PG18: temp_blks_written, shared_blks_read, rows, calls,
# wal_bytes, stddev_exec_time, ...). NB double-precision cols need ::numeric
# before two-arg round() or the query errors and the tool falls through.
def _is_query_intent(q: str) -> bool:
    tk = tokens(q)
    named = _any(tk, "query", "queries", "statement", "statements", "sql") \
        or _phrase(q, "prepared statement", "n+1")
    dim = _any(tk, "slow", "slowest", "expensive", "calls", "io", "rows", "wal",
               "stddev", "deviation", "variance", "regressed", "temp", "spill",
               "execution", "frequently", "executed") \
        or _phrase(q, "total time", "total execution", "mean execution", "worst mean",
                   "cache hit", "most calls", "most i/o", "the most io", "scan the most",
                   "sequential scan", "full table scan", "rows-returned", "rows returned",
                   "rows-scanned", "rows scanned", "temp space", "spill to disk",
                   "p95", "standard deviation", "expensive write", "write queries",
                   "highest total", "top 10 queries", "top query")
    return named and dim


def _slowq_dimension(q: str) -> tuple[str, str, str]:
    """(order_by_expr, extra_select_expr, human_label) for the requested dimension."""
    tk = tokens(q)
    if _phrase(q, "temp space", "spill to disk") or _any(tk, "temp", "spill"):
        return ("temp_blks_written", "temp_blks_written", "temp blocks written")
    if _phrase(q, "most i/o", "the most io", "most io", "disk read", "block read") or _any(tk, "io"):
        return ("shared_blks_read", "shared_blks_read", "shared blocks read")
    if _phrase(q, "cache hit", "bad cache"):
        return ("shared_blks_read",
                "round(100*shared_blks_hit::numeric/nullif(shared_blks_hit+shared_blks_read,0),1)",
                "cache hit %")
    if _phrase(q, "writes the most wal", "most wal") or "wal" in tk:
        return ("wal_bytes", "wal_bytes", "WAL bytes")
    if _phrase(q, "standard deviation", "highest standard", "regressed") or _any(tk, "stddev", "deviation", "variance"):
        return ("stddev_exec_time", "round(stddev_exec_time::numeric,1)", "stddev ms")
    if _phrase(q, "scan the most", "most rows", "rows-scanned", "rows scanned",
               "rows-returned", "rows returned") or "rows" in tk:
        return ("rows", "rows", "rows")
    if _phrase(q, "most calls", "most frequently", "frequently executed", "n+1") \
            or _any(tk, "calls", "frequently", "executed"):
        return ("calls", "calls", "calls")
    if _phrase(q, "worst mean", "mean execution") or _any(tk, "mean", "average"):
        return ("mean_exec_time", "round(mean_exec_time::numeric,1)", "mean ms")
    return ("total_exec_time", "rows", "rows")


def slowq_tool(q: str) -> dict[str, Any] | None:
    if _any(tokens(q), "why", "cause"):
        return None
    if not (_is_query_intent(q) or _phrase(q, "pg_stat_statements", "top query", "slowest quer",
                                           "sequential scan", "full table scan", "n+1")):
        return None
    # "sequential/full table scans on large tables" -> pg_stat_user_tables, not pgss
    if _phrase(q, "sequential scan", "full table scan"):
        try:
            rows = _rows("select relname, seq_scan::text, seq_tup_read::text, coalesce(idx_scan,0)::text "
                         "from pg_stat_user_tables where seq_scan>0 order by seq_scan desc nulls last limit 6",
                         dbname=_app_db())
        except Exception:
            rows = None
        if rows:
            body = "; ".join(f"{r[0]}: {r[1]} seq scans ({r[2]} tuples read, {r[3]} index scans)" for r in rows)
            return {"answer": f"Tables with the most sequential scans: {body}.",
                    "model": "live-data (pg_stat_user_tables)", "intent": "slow_queries",
                    "evidence": {"seq_scans": rows}}
    order_by, extra, label = _slowq_dimension(q)
    query = ("select round(total_exec_time::numeric)::text, calls::text, "
             f"round(mean_exec_time::numeric,1)::text, ({extra})::text, "
             "left(regexp_replace(query,'\\s+',' ','g'),80) "
             f"from pg_stat_statements order by {order_by} desc nulls last limit 5")
    rows = None
    for db in (_app_db(), "postgres"):
        try:
            rows = _rows(query, dbname=db)
            if rows:
                break
        except Exception:
            rows = None
    if not rows:
        return None
    body = "; ".join(f"[{r[0]}ms total / {r[1]} calls / {r[2]}ms mean / {label} {r[3]}] {r[4]}" for r in rows)
    return {"answer": f"Top SQL statements (queries) by {label}: {body}.",
            "model": "live-data (pg_stat_statements)", "intent": "slow_queries",
            "evidence": {"dimension": label, "top": rows}}


# --------------------------------------------------------------------------
# vacuum / bloat  →  pg_stat_user_tables
# --------------------------------------------------------------------------
def vacuum_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    if not (_any(tk, "vacuum", "vacuuming", "vacuumed", "unvacuumed", "autovacuum",
                 "autovacuumed", "autoanalyze", "analyze", "bloat", "dead", "wraparound",
                 "frozen", "relfrozenxid", "datfrozenxid", "n_dead_tup", "toast")
            or _phrase(q, "n_mod_since_analyze", "dead tup", "need vacuum")):
        return None
    if _any(tk, "why", "cause"):
        return None
    # pg_stat_user_tables shows only the CURRENT database's tables -> query app DB.
    try:
        rows = _rows(
            "select relname, n_dead_tup::text, n_live_tup::text, "
            "coalesce(to_char(greatest(last_autovacuum,last_vacuum),'YYYY-MM-DD HH24:MI'),'never') "
            "from pg_stat_user_tables order by n_dead_tup desc nulls last limit 8",
            dbname=_app_db())
        wrap = S.sql_one("select max(age(datfrozenxid)) from pg_database")
    except Exception:
        return None
    if not rows:
        return None
    age = wrap[0] if wrap else "?"
    body = "; ".join(f"{r[0]}: {r[1]} dead / {r[2]} live (last vac {r[3]})" for r in rows)
    return {"answer": f"Tables by dead tuples — {body}. Max datfrozenxid age: {age}.",
            "model": "live-data (pg_stat_user_tables)", "intent": "vacuum_bloat",
            "evidence": {"tables": rows, "wraparound_age": age}}


# --------------------------------------------------------------------------
# storage / WAL  →  sizes
# --------------------------------------------------------------------------
def _storage_result(answer, model, key, ev):
    return {"answer": answer, "model": f"live-data (PostgreSQL {model})", "intent": "storage_wal",
            "evidence": {key: ev}}


def storage_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    if not (_any(tk, "size", "sizes", "storage", "disk", "space", "largest", "biggest",
                 "tablespace", "tablespaces", "index", "indexes", "schema", "schemas",
                 "toast", "duplicate", "grow", "growing", "grown", "growth")
            or _phrase(q, "how large", "how big", "wal directory", "pg_wal",
                       "large object", "large objects")):
        return None
    if _any(tk, "why", "cause"):
        return None
    adb = _app_db()
    try:
        # --- WAL directory ---
        if _phrase(q, "wal directory", "pg_wal") or ("wal" in tk and _any(tk, "directory", "files", "accumulating", "disk")):
            wal = S.sql_one("select pg_size_pretty(sum(size)), count(*) from pg_ls_waldir()")
            if wal:
                return _storage_result(f"pg_wal directory size: {wal[0]} across {wal[1]} WAL files.",
                                       "pg_ls_waldir", "wal", wal)
        # --- tablespaces ---
        if _any(tk, "tablespace", "tablespaces"):
            ts = _rows("select spcname, pg_size_pretty(pg_tablespace_size(oid)) from pg_tablespace order by 2 desc")
            if ts:
                return _storage_result("Tablespace usage: " + "; ".join(f"{r[0]}: {r[1]}" for r in ts),
                                       "pg_tablespace", "tablespaces", ts)
        # --- duplicate indexes ---
        if _phrase(q, "duplicate index", "duplicate indexes") or ("duplicate" in tk and _any(tk, "index", "indexes")):
            dup = _rows("select indrelid::regclass::text, count(*), string_agg(indexrelid::regclass::text,', ') "
                        "from pg_index group by indrelid, indkey having count(*)>1 limit 15", dbname=adb)
            if dup:
                return _storage_result("Duplicate indexes: " + "; ".join(f"{r[0]} x{r[1]} ({r[2]})" for r in dup),
                                       "pg_index", "duplicates", dup)
            return _storage_result("No duplicate indexes found (same table + same columns).",
                                   "pg_index", "duplicates", [])
        # --- unused indexes ---
        if "unused" in tk and _any(tk, "index", "indexes"):
            un = _rows("select relname, indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) "
                       "from pg_stat_user_indexes where idx_scan=0 "
                       "order by pg_relation_size(indexrelid) desc limit 10", dbname=adb)
            if un:
                return _storage_result("Unused indexes (idx_scan=0), largest first: "
                                       + "; ".join(f"{r[1]} on {r[0]} ({r[2]})" for r in un),
                                       "pg_stat_user_indexes", "unused", un)
        # --- index vs table ratio ---
        if _any(tk, "index", "indexes") and _phrase(q, "vs table", "versus table", "ratio", "compared to table", "indexes vs", "index size to table"):
            iv = S.sql_one("select pg_size_pretty(sum(pg_indexes_size(relid))), pg_size_pretty(sum(pg_relation_size(relid))), "
                           "round(100*sum(pg_indexes_size(relid))::numeric/nullif(sum(pg_relation_size(relid)),0),1) "
                           "from pg_stat_user_tables", dbname=adb)
            if iv:
                return _storage_result(f"Index vs table size: indexes {iv[0]} vs tables {iv[1]} ({iv[2]}% index/table).",
                                       "pg_stat_user_tables", "index_vs_table", iv)
        # --- largest indexes ---
        if _any(tk, "index", "indexes"):
            idx = _rows("select indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) "
                        "from pg_stat_user_indexes order by pg_relation_size(indexrelid) desc limit 10", dbname=adb)
            if idx:
                return _storage_result("Largest indexes: " + "; ".join(f"{r[0]}: {r[1]}" for r in idx),
                                       "pg_relation_size", "indexes", idx)
        # --- schema sizes ---
        if _any(tk, "schema", "schemas"):
            sc = _rows("select schemaname, pg_size_pretty(sum(pg_total_relation_size(relid))) "
                       "from pg_stat_user_tables group by 1 order by sum(pg_total_relation_size(relid)) desc limit 10", dbname=adb)
            if sc:
                return _storage_result("Schema sizes (largest first): " + "; ".join(f"{r[0]}: {r[1]}" for r in sc),
                                       "pg_stat_user_tables", "schemas", sc)
        # --- toast ---
        if "toast" in tk:
            to = _rows("select relname, pg_size_pretty(pg_total_relation_size(reltoastrelid)) from pg_class "
                       "where reltoastrelid<>0 order by pg_total_relation_size(reltoastrelid) desc limit 6", dbname=adb)
            if to:
                return _storage_result("Largest TOAST tables: " + "; ".join(f"{r[0]}: {r[1]}" for r in to),
                                       "pg_class", "toast", to)
        # --- large objects / orphaned ---
        if _phrase(q, "large object", "large objects") or "orphaned" in tk:
            lo = S.sql_one("select count(*), coalesce(pg_size_pretty(sum(pg_column_size(data))),'0') from pg_largeobject", dbname=adb)
            n = lo[0] if lo else "0"
            return _storage_result(f"Large objects in {adb}: {n} entries" + (f" (~{lo[1]})" if lo and lo[1] else "")
                                   + ". PostgreSQL does not auto-track orphaned LOs; run lo_unlink/vacuumlo to reclaim.",
                                   "pg_largeobject", "large_objects", lo)
        # --- growth (no true history) -> most-inserted tables as proxy ---
        if _any(tk, "grow", "growing", "grown", "growth"):
            gr = _rows("select relname, n_tup_ins::text, pg_size_pretty(pg_total_relation_size(relid)) "
                       "from pg_stat_user_tables order by n_tup_ins desc nulls last limit 8", dbname=adb)
            if gr:
                return _storage_result("Most-inserted (recently growing) tables — "
                                       + "; ".join(f"{r[0]}: {r[1]} inserts, {r[2]}" for r in gr)
                                       + ". NOTE: per-day growth needs the metrics history store; this is cumulative inserts since stats reset.",
                                       "pg_stat_user_tables", "growth", gr)
        # --- largest single table ---
        if _phrase(q, "largest table", "biggest table") or ("table" in tk and _any(tk, "largest", "biggest")):
            lt = _rows("select schemaname||'.'||relname, pg_size_pretty(pg_total_relation_size(relid)) "
                       "from pg_stat_user_tables order by pg_total_relation_size(relid) desc limit 8", dbname=adb)
            if lt:
                return _storage_result("Largest tables: " + "; ".join(f"{r[0]}: {r[1]}" for r in lt),
                                       "pg_total_relation_size", "tables", lt)
        # --- default: database sizes ---
        dbs = _rows("select datname, pg_size_pretty(pg_database_size(datname)) "
                    "from pg_database where datistemplate=false "
                    "order by pg_database_size(datname) desc limit 10")
    except Exception:
        return None
    if not dbs:
        return None
    return _storage_result("Database sizes (largest first): " + "; ".join(f"{r[0]}: {r[1]}" for r in dbs),
                           "pg_database_size", "databases", dbs)


# --------------------------------------------------------------------------
# logical replication  →  pg_replication_slots / pg_subscription / pg_publication
# --------------------------------------------------------------------------
def logical_repl_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    if not (_any(tk, "subscription", "subscriptions", "publication", "publications", "slot", "slots")
            or _phrase(q, "logical replication")):
        return None
    if _any(tk, "why", "cause"):
        return None
    # "physical replication slot" / "standby ... replication slot" are HA, not
    # logical — let the live-state answer handle those.
    mentions_logical = _any(tk, "logical", "subscription", "subscriptions",
                            "publication", "publications")
    if not mentions_logical and _any(tk, "physical", "standby", "standbys", "streaming"):
        return None
    try:
        slots = _rows(
            "select slot_name, active::text, slot_type, "
            "pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) "
            "from pg_replication_slots where slot_type='logical' "
            "order by pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn) desc nulls last limit 12")
        counts = S.sql_one(
            "select (select count(*) from pg_subscription), "
            "(select count(*) from pg_subscription where subenabled=false), "
            "(select count(*) from pg_replication_slots where slot_type='logical' and not active)")
    except Exception:
        return None
    nsub = counts[0] if counts else "?"
    ndis = counts[1] if counts else "?"
    ninactive = counts[2] if counts else "?"
    body = "; ".join(f"{r[0]} [{r[2]}, {'active' if r[1]=='t' else 'INACTIVE'}, {r[3]} behind]" for r in slots) \
        if slots else "no logical slots"
    return {"answer": (f"{nsub} logical subscription(s) ({ndis} disabled); {ninactive} inactive logical slot(s). "
                       f"Slots by WAL retained: {body}. NOTE: 'behind' is vs current WAL LSN and overstates "
                       f"lag for filtered subscriptions."),
            "model": "live-data (pg_replication_slots)", "intent": "logical_replication",
            "evidence": {"slots": slots, "counts": counts}}


# --------------------------------------------------------------------------
# roles / security  →  pg_roles
# --------------------------------------------------------------------------
def roles_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    # "who owns <db>" -> database ownership from pg_database
    if _phrase(q, "who owns", "owner of", "owned by") and _any(tk, "database", "db"):
        try:
            owners = _rows("select datname, pg_get_userbyid(datdba) from pg_database "
                           "where not datistemplate order by datname")
        except Exception:
            owners = None
        if owners:
            body = "; ".join(f"{r[0]} -> {r[1]}" for r in owners)
            return {"answer": f"Database owners: {body}.",
                    "model": "live-data (pg_database)", "intent": "roles_security",
                    "evidence": {"owners": owners}}
    if not _any(tk, "role", "roles", "superuser", "superusers", "privilege", "privileges",
                "password", "login"):
        return None
    if _any(tk, "why", "cause"):
        return None
    # "role and state of every node" is a Patroni topology question, not pg_roles.
    if _any(tk, "node", "nodes", "member", "members", "patroni") and not _any(
            tk, "superuser", "superusers", "privilege", "privileges", "password"):
        return None
    try:
        summ = S.sql_one("select count(*), count(*) filter (where rolsuper), "
                         "count(*) filter (where rolcanlogin), count(*) filter (where rolreplication) "
                         "from pg_roles")
        supers = _rows("select rolname from pg_roles where rolsuper order by 1")
    except Exception:
        return None
    if not summ:
        return None
    su = ", ".join(r[0] for r in supers) if supers else "none"
    return {"answer": (f"{summ[0]} roles total: {summ[1]} superuser, {summ[2]} can login, "
                       f"{summ[3]} with replication. Superusers: {su}."),
            "model": "live-data (pg_roles)", "intent": "roles_security",
            "evidence": {"summary": summ, "superusers": supers}}


# --------------------------------------------------------------------------
# backups  →  pg_backups.build_backups / build_schedules / build_pitr_preview
# --------------------------------------------------------------------------
def _fmt_bytes(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


def _bk(answer: str, ev: dict) -> dict[str, Any]:
    return {"answer": answer, "model": "live-data (pgbackrest)", "intent": "backups", "evidence": ev}


def backups_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    if _any(tk, "why", "cause"):
        return None
    if not (_any(tk, "backup", "backups", "pgbackrest", "restore", "pitr", "rpo",
                 "recovery", "recoverable", "archived", "archiving")
            or _phrase(q, "archive command", "archive_command", "point-in-time", "point in time",
                       "restore point", "wal archiv", "archive push", "archive queue",
                       "gaps in the wal", "gaps in wal", "last archived", "archive_mode")):
        return None
    try:
        from . import pg_backups as B
        info = B.build_backups()
        sched = B.build_schedules()
        pitr = B.build_pitr_preview()
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    backups = info.get("backups") or []
    archive = info.get("archive") or {}
    settings = info.get("settings") or {}
    summ = info.get("summary") or {}
    pgo = info.get("pgo") or {}
    fulls = [b for b in backups if b.get("type") == "full"]
    diffs = [b for b in backups if b.get("type") == "diff"]
    last = backups[-1] if backups else {}
    last_full = fulls[-1] if fulls else {}

    def blabel(b):
        return (f"{b.get('type')} {b.get('label')} at {b.get('stop_time')} "
                f"(took {b.get('duration_human')}, db {_fmt_bytes(b.get('database_size_bytes'))}, "
                f"repo {_fmt_bytes(b.get('repo_size_bytes'))})")

    if _phrase(q, "differential", "diff backup") or "differential" in tk:
        if diffs:
            return _bk(f"Most recent DIFFERENTIAL backup: {blabel(diffs[-1])}.", {"diff": diffs[-1]})
    if _phrase(q, "last full", "latest full", "recent full") or ("full" in tk and _any(tk, "last", "latest", "recent", "when")):
        if last_full:
            return _bk(f"Last FULL backup: {blabel(last_full)}.", {"full": last_full})
    if _phrase(q, "chain healthy", "backup chain", "is the pgbackrest"):
        return _bk(f"Backup chain status: {summ.get('status')}. {len(backups)} backups "
                   f"({len(fulls)} full, {len(diffs)} diff); {archive.get('failed_count', 0)} archive failures.",
                   {"summary": summ, "archive": archive})
    if _phrase(q, "archiving keeping up", "archive command", "archive_command", "archive push",
               "failed archive", "archive queue", "gaps in the wal", "gaps in wal", "succeeding",
               "keeping up", "archive_mode", "last successful wal", "last archived"):
        return _bk(f"WAL archiving: mode={settings.get('archive_mode')}, "
                   f"command='{settings.get('archive_command')}'. Last archived WAL={archive.get('last_archived_wal')}, "
                   f"failed_count={archive.get('failed_count')}, last_failed={archive.get('last_failed_wal')}. "
                   f"Archived range {archive.get('min_wal')}..{archive.get('max_wal')} (contiguous → no gaps).",
                   {"archive": archive, "settings": settings})
    if _phrase(q, "retention", "how many backup", "backups retained", "currently retained", "how many are retained"):
        crons = "; ".join(f"{s.get('kind')}: {s.get('cron')}" for s in (sched.get("schedules") or []))
        return _bk(f"{len(backups)} backups currently retained ({len(fulls)} full, {len(diffs)} diff). Schedules — {crons}.",
                   {"schedules": sched})
    if _phrase(q, "restore point", "pitr", "point-in-time", "point in time", "recoverable",
               "rpo", "valid restore", "possible right now"):
        return _bk(f"PITR available={pitr.get('available')}: earliest {pitr.get('earliest_restore_time')} → "
                   f"latest {pitr.get('latest_restore_time')}, RPO {pitr.get('rpo_seconds')}s, "
                   f"{pitr.get('full_backups')} full backups anchoring the window.", {"pitr": pitr})
    if _phrase(q, "how long", "duration") or ("take" in tk and _any(tk, "backup", "last")):
        if last:
            return _bk(f"Last backup ({last.get('label')}) took {last.get('duration_human')}.", {"last": last})
    if _phrase(q, "compression ratio", "how large", "size of the backup", "size of backup",
               "repository size", "repo size", "latest backup"):
        if last:
            db, repo = last.get("database_size_bytes"), last.get("repo_size_bytes")
            ratio = round(float(db) / float(repo), 2) if db and repo else "?"
            return _bk(f"Latest backup {last.get('label')}: database {_fmt_bytes(db)}, "
                       f"repository {_fmt_bytes(repo)} (compression ~{ratio}x).", {"last": last})
    if "incremental" in tk:
        kinds = ", ".join(sorted({s.get("kind") for s in (sched.get("schedules") or [])}))
        return _bk(f"Configured backup types: {kinds} (full weekly + differential daily). "
                   f"No separate incremental schedule.", {"schedules": sched})
    if _phrase(q, "repository reachable", "repo reachable"):
        repo = info.get("repo") or {}
        return _bk(f"pgBackRest repo status: {summ.get('status')} (stanza '{repo.get('stanza')}', {repo.get('cipher')}).",
                   {"repo": repo})
    if "standby" in tk:
        return _bk(f"Backups run from standby: {pgo.get('standby')} (pgBackRest {pgo.get('pgbackrest')}, "
                   f"cluster {pgo.get('name')}).", {"pgo": pgo})
    if _phrase(q, "verified", "verify"):
        return _bk(f"pgBackRest reports status '{summ.get('status')}' for all {len(backups)} backups "
                   f"(error flags clear). Automatic post-backup verification is not configured; "
                   f"run 'pgbackrest verify' for a deep check.", {"summary": summ})
    if _phrase(q, "failed in the last week", "any backup failed", "has any backup failed"):
        failed = [b for b in backups if b.get("error")]
        return _bk(f"{len(failed)} failed backups recorded across the retained set. "
                   f"Archive failures {archive.get('failed_count')}; last archived {archive.get('last_archived_wal')}.",
                   {"failed": failed, "archive": archive})
    # default summary
    return _bk(f"Backups: {len(backups)} retained ({len(fulls)} full, {len(diffs)} diff), status {summ.get('status')}. "
               f"Last full {last_full.get('label')} ({last_full.get('stop_time')}). "
               f"PITR {pitr.get('earliest_restore_time')} → {pitr.get('latest_restore_time')}, "
               f"RPO {pitr.get('rpo_seconds')}s. Archive failures: {archive.get('failed_count')}.",
               {"summary": summ, "pitr": pitr})


# --------------------------------------------------------------------------
# logs / errors  →  loki + pg_log_analytics (deterministic, no LLM)
# --------------------------------------------------------------------------
_LOG_RANGES = [("7 day", "7d", 7 * 24 * 3600), ("last week", "7d", 7 * 24 * 3600),
               ("past week", "7d", 7 * 24 * 3600), ("24 hour", "24h", 24 * 3600),
               ("last day", "24h", 24 * 3600), ("6 hour", "6h", 6 * 3600),
               ("last hour", "1h", 3600), ("1 hour", "1h", 3600), ("past hour", "1h", 3600)]
_LOG_SUMMARY_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any], int, int]] = {}
_LOG_SUMMARY_LOCK = threading.Lock()
_LOG_SUMMARY_TTL_S = 15.0


def _log_range(q: str) -> tuple[str, int]:
    ql = (q or "").lower()
    for ph, lab, sec in _LOG_RANGES:
        if ph in ql:
            return lab, sec
    return "24h", 24 * 3600  # "recently" / unspecified


def _cached_log_summary(label: str, seconds: int) -> tuple[dict[str, Any], int, int]:
    from . import loki, pg_log_analytics as LA
    key = (label, seconds)
    now = time.monotonic()
    cached = _LOG_SUMMARY_CACHE.get(key)
    if cached and now - cached[0] <= _LOG_SUMMARY_TTL_S:
        return cached[1], cached[2], cached[3]
    # Single-flight identical windows so concurrent assistant requests do not
    # each launch the same expensive Loki aggregation.
    with _LOG_SUMMARY_LOCK:
        now = time.monotonic()
        cached = _LOG_SUMMARY_CACHE.get(key)
        if cached and now - cached[0] <= _LOG_SUMMARY_TTL_S:
            return cached[1], cached[2], cached[3]
        end = loki.now_ns()
        start = end - seconds * 10 ** 9
        summary = LA.summary(start, end, "5m")
        _LOG_SUMMARY_CACHE[key] = (time.monotonic(), summary, start, end)
        return summary, start, end


def logs_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    if _any(tk, "why", "cause"):
        return None
    if not ((tk & _LOGS_TOKENS) or _phrase(q, *_LOGS_PHRASES)):
        return None
    try:
        from . import pg_log_analytics as LA
        lab, sec = _log_range(q)
        summ, start, end = _cached_log_summary(lab, sec)
    except Exception:
        return None
    if not isinstance(summ, dict) or not summ.get("available"):
        return None
    sev = summ.get("by_severity") or {}
    total = summ.get("total", 0)
    errc = summ.get("error_count", sev.get("error", 0))
    fatal, error, warn = sev.get("fatal", 0), sev.get("error", 0), sev.get("warn", 0)
    sigc = summ.get("signature_count", 0)
    if _phrase(q, "fatal") or "fatal" in tk:
        lead = f"{fatal} FATAL log entries in the last {lab}."
    elif "panic" in tk:
        lead = f"{sev.get('panic', 0)} PANIC messages in the last {lab} (PANIC surfaces at fatal level)."
    elif _phrase(q, "signature", "signatures"):
        try:
            sigs = LA.signatures(start, end, None)
            n = sigs.get("count", 0) if isinstance(sigs, dict) else 0
        except Exception:
            n = sigc
        lead = f"{n} distinct error signatures in the last {lab}."
    elif _phrase(q, "connection failure", "connection failures"):
        lead = f"{errc} error-level log lines (including any connection failures) in the last {lab}."
    elif _phrase(q, "checkpoint", "warning", "warnings"):
        lead = f"{warn} warning-level log lines (checkpoint/WAL) in the last {lab}."
    else:
        lead = f"{errc} error-level log entries in the last {lab}."
    return {"answer": f"{lead} Totals over {lab}: {total} log lines — {fatal} fatal, {error} error, "
                      f"{warn} warn, {sigc} error signatures. Source: Loki.",
            "model": "live-data (loki)", "intent": "logs_errors", "evidence": {"summary": summ}}


# --------------------------------------------------------------------------
# capacity / tuning advice  →  recommendations engine + parameter_advisor + readiness
# --------------------------------------------------------------------------
def _cap(answer: str, ev: dict) -> dict[str, Any]:
    return {"answer": answer, "model": "live-data (recommendations)", "intent": "capacity_tuning", "evidence": ev}


def capacity_tool(q: str) -> dict[str, Any] | None:
    tk = tokens(q)
    # Readiness is handled first (before the why-guard) so "what is the current
    # readiness score and why?" gets a fast factual answer; the router only sends
    # explicit "explain why … dropped" RCA phrasings to the LLM instead.
    if _phrase(q, "readiness score") or ("readiness" in tk and "score" in tk):
        try:
            from . import pg_ops
            r = pg_ops.readiness()
            s = r.get("summary", {})
            bad = [i for i in r.get("items", []) if not i.get("ok")]
            det = "; ".join(f"{i.get('name')}: {i.get('detail')}" for i in bad) or "all checks OK"
            return _cap(f"Readiness score {s.get('score')}/100 ({s.get('status')}): {s.get('critical')} critical, "
                        f"{s.get('warnings')} warnings of {s.get('total')} checks. Issues — {det}.", {"readiness": r})
        except Exception:
            pass
    if _any(tk, "why", "cause"):
        return None
    if not (_any(tk, "recommend", "recommendation", "recommendations", "tuning", "tune",
                 "capacity", "provisioned", "headroom", "optimize", "optimise", "advice")
            or _phrase(q, "performance risk", "most improve", "reduce replication lag",
                       "over-provisioned", "under-provisioned", "capacity risk", "tuning change",
                       "would you change", "single change", "biggest risk", "indexes you would recommend",
                       "indexes to add", "risks in the next")):
        return None
    try:
        from .recommendations import parameter_advisor, engine
    except Exception:
        return None
    try:
        pa = parameter_advisor.build_response(None, None)
    except Exception:
        pa = None
    try:
        recs = engine.list_recommendations("uat", "open", None, 50)
    except Exception:
        recs = None
    advice = [x for x in ((pa or {}).get("recommendations") or []) if x.get("status") == "advice"]
    rsum = (recs or {}).get("summary") or {}
    if _any(tk, "index", "indexes"):
        idxrecs = [x for x in ((recs or {}).get("recommendations") or [])
                   if "index" in (str(x.get("category", "")) + str(x.get("recommendation_type", ""))).lower()]
        if idxrecs:
            body = "; ".join(x.get("title", "") for x in idxrecs[:5])
            return _cap(f"{len(idxrecs)} index recommendations: {body}.", {"index_recs": idxrecs})
        return _cap(f"No automated index-add recommendations are open. {rsum.get('total', 0)} high-impact SQL "
                    f"fingerprints are flagged for plan/index review — inspect those in the Advisor.", {"rec_summary": rsum})
    if _phrase(q, "over-provisioned", "under-provisioned", "provisioned") or ("cpu" in tk and _any(tk, "provisioned", "over", "under")):
        cap = (pa or {}).get("capacity") or {}
        return _cap(f"CPU/RAM sizing is not registered (capacity_known={cap.get('capacity_known')}), so provisioning "
                    f"can't be scored against a target. Configure ram_gib/cpu_cores for a firm verdict; meanwhile "
                    f"compare active sessions vs max_connections and CPU metrics.", {"capacity": cap})
    if _phrase(q, "capacity risk", "risks in the next", "next week"):
        return _cap(f"No hard capacity limits are tracked. Open risk signals: {rsum.get('total', 0)} DBA "
                    f"recommendations ({rsum.get('warning', 0)} warnings). Check readiness for replication-lag/disk warnings.",
                    {"rec_summary": rsum})
    # tuning-change / biggest-risk / single-change / recommend
    if advice:
        body = "; ".join(f"{a.get('parameter')}: {a.get('current')}→{a.get('recommended')} ({a.get('rationale')})" for a in advice)
        extra = f" Plus {rsum.get('total', 0)} SQL-level recommendations ({rsum.get('warning', 0)} warnings)." if rsum else ""
        return _cap(f"Top tuning recommendations: {body}.{extra}", {"advice": advice, "rec_summary": rsum})
    if rsum:
        return _cap(f"{rsum.get('total', 0)} open DBA recommendations ({rsum.get('warning', 0)} warning, "
                    f"{rsum.get('info', 0)} info) — mostly high-impact SQL plan reviews. No parameter changes strongly "
                    f"advised right now.", {"rec_summary": rsum})
    return None


# --------------------------------------------------------------------------
# router
# --------------------------------------------------------------------------
_LOGS_TOKENS = {"log", "logs", "fatal", "panic"}
_LOGS_PHRASES = ("in the logs", "error signatures", "connection failures",
                 "log entries", "log message", "warnings in")

# State tools, tried in precedence order AFTER config and the trend-metric gate.
_STATE_TOOLS: list[Callable[[str], "dict[str, Any] | None"]] = [
    logical_repl_tool, locks_tool, sessions_tool, slowq_tool,
    vacuum_tool, storage_tool, roles_tool,
]


def _safe(tool: Callable[[str], "dict[str, Any] | None"], q: str) -> dict[str, Any] | None:
    try:
        return tool(q)
    except Exception:
        return None


def route(question: str) -> dict[str, Any] | None:
    """Return a deterministic answer dict, or None to fall through (live-state
    fast-path, then the log+LLM path). RCA/why and log questions always fall
    through. Precedence: config (named GUC) → trend-framed metrics → state tools
    → leftover metric phrases."""
    q = _normalize_common_typos(question or "")
    # "what is the current readiness score and why?" is a factual capacity
    # question even though it contains "why" — answer it before the RCA guard.
    if _phrase(q, "readiness score") and _phrase(q, "what is", "current", "what's", "show"):
        ans = _safe(capacity_tool, q)
        if ans is not None:
            return ans
    if is_rca(q):
        return None
    ans = _safe(cpu_capacity_tool, q)
    if ans is not None:
        return ans
    ans = _safe(memory_capacity_tool, q)
    if ans is not None:
        return ans
    tk = tokens(q)
    if (("vacuum" in tk or "autovacuum" in tk or "analyze" in tk)
            and ("table" in tk or "tables" in tk or "need" in tk or "dead" in tk)):
        ans = _safe(vacuum_tool, q)
        if ans is not None:
            return ans
    # log questions -> deterministic Loki summary (falls through to LLM only if Loki errors)
    if (tk & _LOGS_TOKENS) or _phrase(q, *_LOGS_PHRASES):
        return _safe(logs_tool, q)
    # config wins when a real GUC is named (fixes the sync* -> topology misroute)
    ans = _safe(config_tool, q)
    if ans is not None:
        return ans
    # backups / capacity / query-dimension tools run BEFORE the metric gate and the
    # storage tool so "backup repo size", "indexes to recommend" and "queries using
    # the most temp space" reach the right tool instead of being grabbed by storage.
    for tool in (backups_tool, capacity_tool, slowq_tool):
        ans = _safe(tool, q)
        if ans is not None:
            return ans
    # trend/rate/range-framed questions -> metrics before the state tools
    if _is_metric_query(q):
        ans = _safe(metrics_tool, q)
        if ans is not None:
            return ans
    for tool in _STATE_TOOLS:
        ans = _safe(tool, q)
        if ans is not None:
            return ans
    # leftover bare metric phrases (e.g. "what is the cache hit ratio")
    ans = _safe(metrics_tool, q)
    if ans is not None:
        return ans
    return None
