"""Logs Explorer API (Phase 3) — search, facets, histogram, context, tail.

Backed by Loki (``loki.py``) + normalization (``log_parse.py``). Read-only: no
endpoint mutates the cluster. Mirrors the existing ``/api/v1/clusters/{id}``
router conventions and the ``{source, available, ...}`` response shape.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from . import loki, log_parse, jobs
from . import sources as S
from .threads import to_thread

router = APIRouter(prefix="/api/v1/clusters/{cluster_id}", dependencies=[Depends(S.cluster_path_dependency)])
ws_router = APIRouter()  # websocket lives off the cluster-scoped prefix

_SOURCE = "loki"
_DUR = re.compile(r"^(\d+)\s*([smhd])$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}
MAX_LIMIT = 5000
# Query guardrail: never scan beyond Loki's retention window (loki-values.yaml = 7d).
MAX_RANGE_S = 7 * 86400


def _parse_dur(text: str | None, default_s: int) -> int:
    if not text:
        return default_s
    m = _DUR.match(text.strip().lower())
    if not m:
        return default_s
    return int(m.group(1)) * _UNIT_S[m.group(2)]


def _to_ns(val: str | None) -> int | None:
    """Accept unix seconds (int/float) or RFC3339; return unix-nanoseconds."""
    if not val:
        return None
    val = val.strip()
    try:
        return int(float(val) * loki.NS_PER_S)
    except ValueError:
        pass
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp() * loki.NS_PER_S)
    except ValueError:
        return None


def _window(start: str | None, end: str | None, range_: str | None) -> tuple[int, int]:
    """Resolve (start_ns, end_ns). Precedence: explicit start/end, else range, else 1h.

    Clamps the span to MAX_RANGE_S so a crafted start/end can't force Loki to
    scan beyond retention.
    """
    end_ns = _to_ns(end) or loki.now_ns()
    start_ns = _to_ns(start)
    if start_ns is None:
        start_ns = end_ns - _parse_dur(range_, 3600) * loki.NS_PER_S
    floor = end_ns - MAX_RANGE_S * loki.NS_PER_S
    if start_ns < floor:
        start_ns = floor
    return start_ns, end_ns


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------
@router.get("/logs/search")
async def logs_search(
    cluster_id: str,
    q: str | None = None,
    component: list[str] | None = Query(default=None),
    level: list[str] | None = Query(default=None),
    pod: str | None = None,
    container: str | None = None,
    start: str | None = None,
    end: str | None = None,
    range: str | None = None,
    limit: int = 200,
    direction: str = "backward",
):
    limit = max(1, min(limit, MAX_LIMIT))
    direction = "forward" if direction == "forward" else "backward"
    start_ns, end_ns = _window(start, end, range)
    query = log_parse.build_query(q, component, level, pod, container)

    def _run() -> dict[str, Any]:
        streams = loki.query_range(query, start_ns, end_ns, limit=limit, direction=direction)
        entries = log_parse.flatten(streams, limit=limit)
        jobs._audit("logs-search", "dba", dry_run=False, executed=True,
                    detail=f"{query} [{limit}]")
        return {
            "available": True, "source": _SOURCE, "query": query,
            "start_ns": str(start_ns), "end_ns": str(end_ns),
            "count": len(entries), "limit": limit, "direction": direction,
            "entries": entries,
        }

    return await to_thread(_run)


# --------------------------------------------------------------------------
# Facets for the filter UI
# --------------------------------------------------------------------------
_LABELS_CACHE: dict[str, Any] = {"ts": 0.0, "range": None, "data": None}
_LABELS_TTL = 30.0


@router.get("/logs/diag")
async def logs_diag(cluster_id: str, namespace: str | None = None, window_h: int = 24):
    """Log-pipeline self-check: transport, authorized namespace label, tenant data."""
    return await to_thread(loki.diag, namespace, window_h)


@router.get("/logs/labels")
async def logs_labels(cluster_id: str, range: str | None = None):
    rng = range or "6h"
    now = time.time()
    if (_LABELS_CACHE["data"] is not None and _LABELS_CACHE["range"] == rng
            and now - _LABELS_CACHE["ts"] < _LABELS_TTL):
        return _LABELS_CACHE["data"]
    start_ns, end_ns = _window(None, None, rng)

    def _run() -> dict[str, Any]:
        match = log_parse.build_selector()  # {namespace="..."}
        streams = loki.query_range(match, start_ns, end_ns, limit=5000, direction="backward")
        rows = log_parse.flatten(streams)
        comps, levels, pods, containers = set(), set(), set(), set()
        for r in rows:
            if r.get("component"):
                comps.add(r["component"])
            if r.get("level"):
                levels.add(r["level"])
            if r.get("pod"):
                pods.add(r["pod"])
            if r.get("container"):
                containers.add(r["container"])
        return {
            "available": True, "source": _SOURCE, "facet_source": "normalized_messages",
            "components": sorted(comps), "levels": sorted(levels),
            "pods": sorted(pods), "containers": sorted(containers),
            "severities": log_parse.SEVERITIES,
        }

    data = await to_thread(_run)
    _LABELS_CACHE.update(ts=now, range=rng, data=data)
    return data


# --------------------------------------------------------------------------
# Volume histogram (timeline bar over the results)
# --------------------------------------------------------------------------
@router.get("/logs/histogram")
async def logs_histogram(
    cluster_id: str,
    q: str | None = None,
    component: list[str] | None = Query(default=None),
    level: list[str] | None = Query(default=None),
    pod: str | None = None,
    container: str | None = None,
    start: str | None = None,
    end: str | None = None,
    range: str | None = None,
    step: str = "1m",
):
    start_ns, end_ns = _window(start, end, range)
    base = log_parse.build_query(q, component, level, pod, container)

    def _run() -> dict[str, Any]:
        streams = loki.query_range(base, start_ns, end_ns, limit=5000, direction="backward")
        rows = log_parse.flatten(streams)
        wanted = {item.upper() for item in (level or [])}
        if wanted:
            rows = [row for row in rows if row["level"] in wanted]
        # bucket_ts -> {severity: count}; also keep total per bucket
        buckets: dict[float, dict[str, Any]] = {}
        step_s = {"1m": 60, "5m": 300, "10m": 600, "30m": 1800, "1h": 3600}.get(step, 60)
        for row in rows:
            unix_s = int(row["ts_ns"]) // loki.NS_PER_S
            bucket = float((unix_s // step_s) * step_s)
            b = buckets.setdefault(bucket, {"ts": bucket, "total": 0,
                                            **{k: 0 for k in log_parse.SEVERITIES}})
            b[row["severity"]] += 1
            b["total"] += 1
        series = [buckets[k] for k in sorted(buckets)]
        return {
            "available": True, "source": _SOURCE, "aggregation_source": "normalized_messages", "step": step,
            "start_ns": str(start_ns), "end_ns": str(end_ns),
            "buckets": series, "total": sum(b["total"] for b in series),
        }

    return await to_thread(_run)


# --------------------------------------------------------------------------
# Context — surrounding lines for one entry (±window on the same stream)
# --------------------------------------------------------------------------
@router.get("/logs/context/{ts_ns}")
async def logs_context(
    cluster_id: str,
    ts_ns: str,
    component: list[str] | None = Query(default=None),
    pod: str | None = None,
    container: str | None = None,
    window: str = "5m",
    limit: int = 50,
):
    limit = max(1, min(limit, 500))
    try:
        center = int(ts_ns)
    except ValueError:
        center = loki.now_ns()
    span = _parse_dur(window, 300) * loki.NS_PER_S
    selector = log_parse.build_selector(component, None, pod, container)

    def _run() -> dict[str, Any]:
        before = log_parse.flatten(
            loki.query_range(selector, center - span, center, limit=limit, direction="backward"))
        after = log_parse.flatten(
            loki.query_range(selector, center, center + span, limit=limit, direction="forward"))
        return {
            "available": True, "source": _SOURCE, "ts_ns": ts_ns,
            "selector": selector,
            "before": [e for e in before if e["ts_ns"] < ts_ns],
            "after": [e for e in after if e["ts_ns"] >= ts_ns],
        }

    return await to_thread(_run)


# --------------------------------------------------------------------------
# Download — current query as redacted NDJSON
# --------------------------------------------------------------------------
@router.get("/logs/download")
async def logs_download(
    cluster_id: str,
    q: str | None = None,
    component: list[str] | None = Query(default=None),
    level: list[str] | None = Query(default=None),
    pod: str | None = None,
    container: str | None = None,
    start: str | None = None,
    end: str | None = None,
    range: str | None = None,
    limit: int = 5000,
):
    limit = max(1, min(limit, MAX_LIMIT))
    start_ns, end_ns = _window(start, end, range)
    query = log_parse.build_query(q, component, level, pod, container)

    def _collect() -> list[dict[str, Any]]:
        streams = loki.query_range(query, start_ns, end_ns, limit=limit, direction="backward")
        jobs._audit("logs-download", "dba", dry_run=False, executed=True, detail=query)
        return log_parse.flatten(streams, limit=limit)

    entries = await to_thread(_collect)

    def _ndjson():
        for e in entries:
            yield json.dumps(e, separators=(",", ":")) + "\n"

    fname = f"logs-{S.CLUSTER_ID}-{int(time.time())}.ndjson"
    return StreamingResponse(_ndjson(), media_type="application/x-ndjson",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# --------------------------------------------------------------------------
# Live tail (WebSocket) — poll Loki forward and push new entries.
# Client may send a first JSON frame: {q, component[], level[], pod, container}.
# --------------------------------------------------------------------------
@ws_router.websocket("/ws/logs/tail")
async def logs_tail(ws: WebSocket):
    await ws.accept()
    try:
        try:
            first = await asyncio.wait_for(ws.receive_text(), timeout=2.0)
            flt = json.loads(first) if first else {}
        except (asyncio.TimeoutError, json.JSONDecodeError):
            flt = {}
        query = log_parse.build_query(
            flt.get("q"), flt.get("component"), flt.get("level"),
            flt.get("pod"), flt.get("container"))
        await ws.send_json({"type": "info", "query": query})

        # Start a touch in the past so the first poll has immediate context, and
        # only advance `last_ns` to the newest entry we actually emit — never to
        # `now`. Loki ingestion lags a few seconds; jumping to `now` would skip
        # lines whose timestamp precedes `now` but which arrive later.
        last_ns = loki.now_ns() - 5 * loki.NS_PER_S
        while True:
            now = loki.now_ns()
            if now > last_ns:
                streams = await to_thread(
                    loki.query_range, query, last_ns + 1, now, 500, "forward")
                entries = log_parse.flatten(streams)
                entries.sort(key=lambda r: r["ts_ns"])  # oldest-first for a tail
                for e in entries:
                    await ws.send_json({"type": "entry", **e})
                    last_ns = max(last_ns, int(e["ts_ns"]))
            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # pragma: no cover - surface then close
        try:
            await ws.send_json({"type": "error", "error": str(exc)})
        finally:
            await ws.close()
