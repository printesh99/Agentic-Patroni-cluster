"""Metrics Explorer + AppMon/BizMon routes (Phase 4 read)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from . import pg_metrics, pg_appmon
from . import sources as S
from .threads import to_thread

router = APIRouter(prefix="/api/v1/clusters/{cluster_id}", dependencies=[Depends(S.cluster_path_dependency)])


# --- Metrics Explorer ------------------------------------------------------
@router.get("/metrics/catalog")
async def metrics_catalog(cluster_id: str):
    return await to_thread(pg_metrics.catalog)


@router.get("/metrics/entities")
async def metrics_entities(cluster_id: str, metric: str = "connections"):
    return await to_thread(pg_metrics.entities, metric)


@router.get("/metrics/forecast")
async def metrics_forecast(cluster_id: str, metric: str = "connections", range: str | None = None):
    return await to_thread(pg_metrics.forecast, metric, range)


# Note: /metrics/series is served by api_clusters and delegates catalog metrics
# here, so the Explorer and Overview share one series endpoint.


# --- AppMon ----------------------------------------------------------------
@router.get("/appmon/overview")
async def appmon_overview(
    cluster_id: str,
    database: str | None = None,
    region: str | None = None,
    domain: str | None = None,
):
    return await to_thread(pg_appmon.overview, database, region, domain)


@router.get("/appmon/filters")
async def appmon_filters(cluster_id: str):
    return await to_thread(pg_appmon.filters)


@router.get("/appmon/top-sessions")
async def appmon_top_sessions(
    cluster_id: str,
    database: str | None = None,
    region: str | None = None,
    domain: str | None = None,
    limit: int = 25,
):
    return await to_thread(pg_appmon.top_sessions, database, region, domain, limit)


@router.get("/appmon/domain/{domain}")
async def appmon_domain(
    cluster_id: str,
    domain: str,
    database: str | None = None,
    schema: str | None = None,
    range: str | None = None,
    limit: int = 25,
):
    return await to_thread(pg_appmon.domain_detail, domain, database, schema, range, limit)


@router.get("/appmon/replication")
async def appmon_replication(cluster_id: str):
    return await to_thread(pg_appmon.replication)


@router.get("/appmon/dba-evidence")
async def appmon_dba_evidence(cluster_id: str, limit: int = 25):
    return await to_thread(pg_appmon.dba_evidence, limit)


# --- BizMon (business dashboards; live SQL with local seed fallback) --------
@router.get("/bizmon/dashboards")
async def bizmon_dashboards(cluster_id: str):
    return await to_thread(pg_appmon.bizmon_dashboards)


@router.get("/bizmon/panel/{panel}")
async def bizmon_panel(cluster_id: str, panel: str, range: str | None = None):
    return await to_thread(pg_appmon.bizmon_panel, panel, range)
