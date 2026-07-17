"""Read-only live chart and ASH endpoints for the v28 frontend."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from . import pg_ash, pg_charts, sources as S
from .threads import to_thread

router = APIRouter(prefix="/api/v1", dependencies=[Depends(S.cluster_path_dependency)])


def _window_minutes(window: str) -> int:
    values = {"1h": 60, "6h": 360, "24h": 1440, "7d": 10080, "14d": 20160}
    return values.get(window.lower(), 1440)


@router.get("/charts/{module}")
async def chart_payload(module: str, view: str | None = None,
                        cluster_id: str | None = None):
    """Return live data or an honest unavailable payload; never sample data."""
    return await to_thread(pg_charts.build, module, view)


@router.get("/perf/db-load")
async def db_load(window: str = "24h", dim: str = "wait_class",
                  cluster_id: str | None = None):
    return await to_thread(pg_ash.db_load, _window_minutes(window), dim)


@router.get("/perf/top-sql/history")
async def top_sql_history(window: str = "24h", limit: int = Query(8, ge=1, le=100),
                          cluster_id: str | None = None):
    return await to_thread(pg_ash.topsql_history, _window_minutes(window), limit)


@router.get("/perf/top-sql/compare")
async def top_sql_compare(window: str = "1h", limit: int = Query(15, ge=1, le=100),
                          cluster_id: str | None = None):
    return await to_thread(pg_ash.stmt_compare, _window_minutes(window), limit)


@router.get("/memory/topology")
async def memory_topology(cluster_id: str | None = None):
    return await to_thread(pg_charts.memory_topology)

