"""Cluster-scoped read endpoints: overview, metrics series, appmon trend (Phase 3)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends

from . import sources as S
from . import pg_overview
from . import pg_cluster
from . import pg_metrics
from .threads import to_thread

router = APIRouter(prefix="/api/v1", dependencies=[Depends(S.cluster_path_dependency)])


def _range_minutes(rng: str | None) -> int:
    if not rng:
        return 24 * 60
    m = re.match(r"(\d+)\s*([hmd])", rng.strip().lower())
    if not m:
        return 24 * 60
    n, unit = int(m.group(1)), m.group(2)
    return n * {"m": 1, "h": 60, "d": 1440}[unit]


def _step_for(minutes: int) -> str:
    if minutes <= 60:
        return "60s"
    if minutes <= 24 * 60:
        return "300s"
    return "900s"


# PromQL templates for each metric the UI asks /metrics/series for. Each must
# reduce to a single series so prom_range returns one [ts,val] list.
#
# IMPORTANT: formatted per-call (via _metric_promql()), NOT frozen into a
# module-level dict at import time — S.NS is now cluster_id-dependent
# (see sources.py), and a dict built once at import would bake in whatever
# cluster happened to be active first, ignoring cluster_id on every
# subsequent request regardless of which cluster the caller asked for.
_METRIC_PROMQL_TEMPLATES = {
    "connections": 'sum(pg_stat_activity_sessions{{namespace="{ns}"}})',
    "storage_bytes": 'sum(max by (datname) (pg_database_size_bytes{{namespace="{ns}"}}))',
    "tps": 'sum(rate(pg_stat_database_xact_commit_total{{namespace="{ns}"}}[5m]))',
}


def _metric_promql(metric: str) -> str | None:
    template = _METRIC_PROMQL_TEMPLATES.get(metric)
    return template.format(ns=S.NS) if template else None

_LIVE_FALLBACK_METRIC = {
    "connections": "connections",
    "storage_bytes": "database_size",
    "tps": "tps_commit",
}


@router.get("/ui/overview/{cluster_id}")
async def ui_overview(cluster_id: str, range: str | None = None):
    return await to_thread(pg_overview.build_overview)


@router.get("/ui/cluster/{cluster_id}")
async def ui_cluster(cluster_id: str):
    return await to_thread(pg_cluster.build_cluster)


@router.get("/clusters/{cluster_id}/metrics/series")
async def metrics_series(cluster_id: str, metric: str = "connections", range: str | None = None):
    minutes = _range_minutes(range)
    expr = _metric_promql(metric)
    if expr is None:
        # Fall back to the Metrics Explorer catalog (pg_*, replication, etc.).
        if metric in pg_metrics.CATALOG:
            return await to_thread(pg_metrics.series, metric, range)
        return {"available": False, "metric": metric, "points": [],
                "reason": "unknown metric"}
    try:
        points = await to_thread(S.prom_range, expr, minutes, _step_for(minutes))
    except S.SourceError:
        points = []
    # convert ms? UI uses p[0] as timestamp directly for echarts (expects ms).
    points_ms = [[int(ts * 1000), val] for ts, val in points]
    if len(points_ms) <= 1:
        fallback_metric = _LIVE_FALLBACK_METRIC.get(metric)
        if fallback_metric:
            live = await to_thread(pg_metrics.live_series, fallback_metric, int(_step_for(minutes)[:-1]))
            if live:
                live["metric"] = metric
                return live
    return {
        "available": len(points_ms) > 1,
        "metric": metric,
        "source": "prometheus",
        "points": points_ms,
    }


@router.get("/clusters/{cluster_id}/appmon/trend")
async def appmon_trend(cluster_id: str, range: str | None = None):
    """Sessions per state over time (fallback source for the connection card)."""
    minutes = _range_minutes(range)
    step = _step_for(minutes)
    series = []
    for state in ("active", "idle", "idle in transaction"):
        expr = f'sum(pg_stat_activity_sessions{{namespace="{S.NS}",state="{state}"}})'
        try:
            pts = await to_thread(S.prom_range, expr, minutes, step)
        except S.SourceError:
            pts = []
        if pts:
            series.append({
                "name": state,
                "points": [[int(ts * 1000), val] for ts, val in pts],
            })
    if not series:
        return await to_thread(pg_metrics.live_appmon_trend, int(step[:-1]))
    return {"available": bool(series), "series": series, "source": "prometheus"}
