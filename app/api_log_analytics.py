"""Log Analytics Center API (Phase 4) — error intelligence over Loki.

Routes under ``/api/v1/clusters/{id}/log-analytics``. Read-only; mirrors the
existing router conventions and ``{source, available, ...}`` shapes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from . import pg_log_analytics as A
from . import sources as S
from .api_logs import _window
from .threads import to_thread

router = APIRouter(prefix="/api/v1/clusters/{cluster_id}", dependencies=[Depends(S.cluster_path_dependency)])

summary_router = APIRouter(prefix="/api/v1")


@summary_router.get("/log-analytics/summary")
async def global_la_summary(cluster_id: str | None = None, range: str = "6h", step: str = "5m"):
    s, e = _window(None, None, range)
    return await to_thread(A.summary, s, e, step)


@router.get("/log-analytics/summary")
async def la_summary(cluster_id: str, start: str | None = None, end: str | None = None,
                     range: str | None = None, step: str = "5m"):
    s, e = _window(start, end, range or "6h")
    return await to_thread(A.summary, s, e, step)


@router.get("/log-analytics/signatures")
async def la_signatures(cluster_id: str, level: str | None = None,
                        component: str | None = None, limit: int = 50,
                        start: str | None = None, end: str | None = None,
                        range: str | None = None):
    s, e = _window(start, end, range or "6h")
    return await to_thread(A.signatures, s, e, level, component, limit)


@router.get("/log-analytics/signatures/{sid}")
async def la_signature_detail(cluster_id: str, sid: str, start: str | None = None,
                              end: str | None = None, range: str | None = None,
                              step: str = "5m"):
    s, e = _window(start, end, range or "6h")
    return await to_thread(A.signature_detail, sid, s, e, step)


@router.get("/log-analytics/categories")
async def la_categories(cluster_id: str, start: str | None = None,
                        end: str | None = None, range: str | None = None):
    s, e = _window(start, end, range or "6h")
    return await to_thread(A.categories, s, e)


@router.get("/log-analytics/findings")
async def la_findings(cluster_id: str, start: str | None = None,
                      end: str | None = None, range: str | None = None):
    s, e = _window(start, end, range or "6h")
    return await to_thread(A.findings, s, e)
