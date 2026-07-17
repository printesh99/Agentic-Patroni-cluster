"""ASH-style session sampler + statement history store.

Persists two kinds of history into the console's metadata database so the
timeline charts (DB load by wait class, sessions by application, top-SQL
compare windows) render *real* history instead of seeded curves:

  * ``ash_sample``  — aggregated pg_stat_activity samples per dimension
  * ``stmt_sample`` — periodic pg_stat_statements snapshots

Sampling is opportunistic (any read triggers a sample when the last one is
stale) plus an optional per-process background loop started from main.py
(``ASH_SAMPLER_ENABLED``, default on). Writes dedupe on a time bucket so
multiple uvicorn workers do not double-count. Until samples accrue the
endpoints return real empty series — never fabricated points.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from sqlalchemy import text

from . import sources as S
from .db.session import engine

log = logging.getLogger("objectmonitor.ash")

SAMPLE_INTERVAL_S = int(os.getenv("ASH_SAMPLE_INTERVAL_S", "60"))
STMT_INTERVAL_S = int(os.getenv("ASH_STMT_INTERVAL_S", "600"))
RETENTION_DAYS = int(os.getenv("ASH_RETENTION_DAYS", "14"))

_DIMS = ("wait_class", "app", "db", "user", "state")
_schema_ready = False
_schema_lock = threading.Lock()
_last_sample: dict[str, float] = {}   # cluster -> monotonic ts
_last_stmt: dict[str, float] = {}


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with engine.begin() as cx:
            cx.execute(text(
                "create table if not exists ash_sample ("
                " bucket_ts timestamptz not null,"
                " cluster text not null,"
                " dim text not null,"
                " key text not null,"
                " sessions real not null,"
                " primary key (bucket_ts, cluster, dim, key))"
            ))
            cx.execute(text(
                "create index if not exists ash_sample_ts_idx"
                " on ash_sample (cluster, dim, bucket_ts)"
            ))
            cx.execute(text(
                "create table if not exists stmt_sample ("
                " bucket_ts timestamptz not null,"
                " cluster text not null,"
                " queryid text not null,"
                " query text,"
                " calls bigint,"
                " total_ms double precision,"
                " mean_ms double precision,"
                " rows bigint,"
                " primary key (bucket_ts, cluster, queryid))"
            ))
            cx.execute(text(
                "create index if not exists stmt_sample_ts_idx"
                " on stmt_sample (cluster, bucket_ts)"
            ))
        _schema_ready = True


def _cluster() -> str:
    try:
        return S.NS
    except Exception:
        return "default"


def _bucket_ms(interval_s: int) -> int:
    now = int(time.time())
    return (now - now % interval_s) * 1000


def sample_once(force: bool = False) -> bool:
    """Aggregate the current pg_stat_activity into ash_sample. Returns True
    when a sample was written (throttled to one per interval per cluster)."""
    cluster = _cluster()
    now = time.monotonic()
    if not force and now - _last_sample.get(cluster, 0.0) < SAMPLE_INTERVAL_S * 0.9:
        return False
    _last_sample[cluster] = now

    rows = S.sql(
        "select coalesce(nullif(wait_event_type,''),'CPU'),"
        " coalesce(nullif(application_name,''),'(none)'),"
        " coalesce(datname,'(none)'), coalesce(usename,'(none)'),"
        " coalesce(state,'(none)')"
        " from pg_stat_activity"
        " where pid <> pg_backend_pid() and state is not null and state <> 'idle'"
        " limit 2000"
    )
    agg: dict[tuple[str, str], int] = {}
    for r in rows:
        wait, app, db, usr, state = (r + ["", "", "", "", ""])[:5]
        # active sessions carry the wait class; non-active keep their state
        wc = wait if state == "active" else "Client"
        for dim, key in (("wait_class", wc), ("app", app), ("db", db),
                         ("user", usr), ("state", state)):
            agg[(dim, key)] = agg.get((dim, key), 0) + 1

    _ensure_schema()
    bucket = _bucket_ms(SAMPLE_INTERVAL_S)
    with engine.begin() as cx:
        for (dim, key), n in agg.items():
            cx.execute(text(
                "insert into ash_sample (bucket_ts, cluster, dim, key, sessions)"
                " values (to_timestamp(:ts / 1000.0), :cluster, :dim, :key, :n)"
                " on conflict do nothing"
            ), {"ts": bucket, "cluster": cluster, "dim": dim, "key": key, "n": n})
        if not agg:
            # record the empty observation so charts show a real zero, not a gap
            cx.execute(text(
                "insert into ash_sample (bucket_ts, cluster, dim, key, sessions)"
                " values (to_timestamp(:ts / 1000.0), :cluster, 'wait_class', '(idle)', 0)"
                " on conflict do nothing"
            ), {"ts": bucket, "cluster": cluster})
        cx.execute(text(
            "delete from ash_sample where bucket_ts < now() - make_interval(days => :d)"
        ), {"d": RETENTION_DAYS})
    return True


def sample_statements(force: bool = False) -> bool:
    """Snapshot pg_stat_statements (top 100 by total time) into stmt_sample."""
    cluster = _cluster()
    now = time.monotonic()
    if not force and now - _last_stmt.get(cluster, 0.0) < STMT_INTERVAL_S * 0.9:
        return False
    _last_stmt[cluster] = now

    try:
        rows = S.sql(
            "select queryid::text, replace(left(query,500),chr(10),' '), calls,"
            " round(total_exec_time::numeric,1), round(mean_exec_time::numeric,3), rows"
            " from pg_stat_statements order by total_exec_time desc limit 100"
        )
    except S.SourceError:
        return False

    _ensure_schema()
    bucket = _bucket_ms(STMT_INTERVAL_S)
    with engine.begin() as cx:
        for r in rows:
            cx.execute(text(
                "insert into stmt_sample"
                " (bucket_ts, cluster, queryid, query, calls, total_ms, mean_ms, rows)"
                " values (to_timestamp(:ts / 1000.0), :cluster, :qid, :q, :calls, :total, :mean, :rows)"
                " on conflict do nothing"
            ), {"ts": bucket, "cluster": cluster, "qid": r[0], "q": r[1],
                "calls": int(float(r[2] or 0)), "total": float(r[3] or 0),
                "mean": float(r[4] or 0), "rows": int(float(r[5] or 0))})
        cx.execute(text(
            "delete from stmt_sample where bucket_ts < now() - make_interval(days => :d)"
        ), {"d": RETENTION_DAYS})
    return True


def opportunistic_sample() -> None:
    """Called from read endpoints: keep history warm while the console is open."""
    try:
        sample_once()
    except Exception as exc:            # noqa: BLE001 - reads must never fail
        log.debug("ash sample skipped: %s", exc)
    try:
        sample_statements()
    except Exception as exc:            # noqa: BLE001
        log.debug("stmt sample skipped: %s", exc)


def db_load(minutes: int = 60, dim: str = "wait_class") -> dict[str, Any]:
    """Stacked series of sampled sessions grouped by ``dim`` over the window."""
    if dim not in _DIMS:
        return {"available": False, "dim": dim, "series": [], "reason": "unknown dimension"}
    opportunistic_sample()
    _ensure_schema()
    with engine.begin() as cx:
        rows = cx.execute(text(
            "select extract(epoch from bucket_ts)::bigint * 1000, key, sessions"
            " from ash_sample"
            " where cluster = :cluster and dim = :dim"
            " and bucket_ts > now() - make_interval(mins => :m)"
            " order by bucket_ts"
        ), {"cluster": _cluster(), "dim": dim, "m": minutes}).fetchall()

    buckets: list[int] = []
    seen: set[int] = set()
    per_key: dict[str, dict[int, float]] = {}
    for ts, key, sessions in rows:
        ts = int(ts)
        if ts not in seen:
            seen.add(ts)
            buckets.append(ts)
        if key == "(idle)":
            continue
        per_key.setdefault(str(key), {})[ts] = float(sessions)

    totals = {k: sum(v.values()) for k, v in per_key.items()}
    top = sorted(totals, key=totals.get, reverse=True)[:8]
    series = [{
        "name": k,
        "points": [[ts, per_key[k].get(ts, 0.0)] for ts in buckets],
    } for k in top]
    return {
        "available": bool(series),
        "dim": dim,
        "bucket_seconds": SAMPLE_INTERVAL_S,
        "window_minutes": minutes,
        "samples": len(buckets),
        "series": series,
        "source": "pg_stat_activity sampler",
    }


def topsql_history(minutes: int = 24 * 60, limit: int = 8) -> dict[str, Any]:
    """Per-statement mean-time series from the snapshot store."""
    opportunistic_sample()
    _ensure_schema()
    with engine.begin() as cx:
        top = cx.execute(text(
            "select queryid, max(query), sum(total_ms) t from stmt_sample"
            " where cluster = :cluster and bucket_ts > now() - make_interval(mins => :m)"
            " group by queryid order by t desc limit :lim"
        ), {"cluster": _cluster(), "m": minutes, "lim": limit}).fetchall()
        series = []
        for qid, query, _t in top:
            pts = cx.execute(text(
                "select extract(epoch from bucket_ts)::bigint * 1000, mean_ms, calls"
                " from stmt_sample"
                " where cluster = :cluster and queryid = :qid"
                " and bucket_ts > now() - make_interval(mins => :m)"
                " order by bucket_ts"
            ), {"cluster": _cluster(), "qid": qid, "m": minutes}).fetchall()
            series.append({
                "queryid": qid, "query": query,
                "points": [[int(ts), float(mean or 0)] for ts, mean, _c in pts],
                "calls": int(pts[-1][2]) if pts else 0,
            })
        n = cx.execute(text(
            "select count(distinct bucket_ts) from stmt_sample"
            " where cluster = :cluster and bucket_ts > now() - make_interval(mins => :m)"
        ), {"cluster": _cluster(), "m": minutes}).scalar() or 0
    return {"available": bool(series), "source": "pg_stat_statements snapshots",
            "snapshots": int(n), "series": series}


def stmt_compare(window_minutes: int = 60, limit: int = 15) -> dict[str, Any]:
    """Compare the latest statement snapshot against one ~window ago: per-query
    delta of calls and mean time (deployment before/after view)."""
    opportunistic_sample()
    _ensure_schema()
    with engine.begin() as cx:
        bounds = cx.execute(text(
            "select max(bucket_ts) filter (where bucket_ts > now() - make_interval(mins => :m)),"
            " max(bucket_ts) filter (where bucket_ts <= now() - make_interval(mins => :m))"
            " from stmt_sample where cluster = :cluster"
        ), {"cluster": _cluster(), "m": window_minutes}).fetchone()
        newest, oldest = bounds or (None, None)
        if not newest or not oldest:
            return {"available": False, "rows": [],
                    "reason": "need snapshots on both sides of the window"}
        rows = cx.execute(text(
            "select a.queryid, max(a.query),"
            " max(b.calls) calls_before, max(a.calls) calls_after,"
            " max(b.mean_ms) mean_before, max(a.mean_ms) mean_after"
            " from stmt_sample a join stmt_sample b"
            "   on b.queryid = a.queryid and b.cluster = a.cluster"
            " where a.cluster = :cluster and a.bucket_ts = :new and b.bucket_ts = :old"
            " group by a.queryid"
            " order by greatest(max(a.mean_ms), max(b.mean_ms)) desc limit :lim"
        ), {"cluster": _cluster(), "new": newest, "old": oldest, "lim": limit}).fetchall()
    out = []
    for qid, query, cb, ca, mb, ma in rows:
        mb, ma = float(mb or 0), float(ma or 0)
        out.append({
            "queryid": qid, "query": query,
            "calls_before": int(cb or 0), "calls_after": int(ca or 0),
            "mean_before_ms": mb, "mean_after_ms": ma,
            "delta_pct": round((ma - mb) / mb * 100, 1) if mb else None,
        })
    return {"available": bool(out), "window_minutes": window_minutes,
            "before": str(oldest), "after": str(newest), "rows": out}


def capture_statements_now() -> dict[str, Any]:
    ok = False
    try:
        ok = sample_statements(force=True)
    except Exception as exc:            # noqa: BLE001
        return {"ok": False, "captured": False, "reason": str(exc)}
    return {"ok": True, "captured": ok}


_bg_started = False


def start_background() -> None:
    """Optional per-process sampling loop (gated by ASH_SAMPLER_ENABLED)."""
    global _bg_started
    if _bg_started or os.getenv("ASH_SAMPLER_ENABLED", "true").lower() not in ("1", "true", "yes"):
        return
    _bg_started = True

    def _loop() -> None:
        while True:
            try:
                sample_once()
                sample_statements()
            except Exception as exc:    # noqa: BLE001 - sampler must survive
                log.debug("background sample failed: %s", exc)
            time.sleep(SAMPLE_INTERVAL_S)

    threading.Thread(target=_loop, name="ash-sampler", daemon=True).start()
    log.info("ASH sampler started (interval %ss)", SAMPLE_INTERVAL_S)
