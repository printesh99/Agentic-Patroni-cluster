"""Typed FastAPI surface for the central pg_profile subsystem."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from .pg_profile import client, feature_extractor, report_service, retention_service, service
from .pg_profile.config import settings
from .pg_profile.schemas import BaselineFeedback, ReportCreate, RetentionRequest, SampleRequest, ServerCreate
from .pg_profile.security import REPORT_CSP, require_dba, sanitize_error
from .threads import to_thread

router = APIRouter(prefix="/api/v1/pg-profile", tags=["pg-profile"])


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@router.get("/status")
async def status():
    health = await to_thread(client.collection_health)
    servers = await to_thread(service.list_servers, 1, 0)
    run_counts = await to_thread(service.run_status_counts)
    reports = await to_thread(report_service.list_reports, 1, 0, None, None)
    return {**health, "configured": settings.public_dict(), "registered_servers": servers["total"],
            "failed_sample_runs": run_counts.get("FAILED", 0),
            "reports": reports["total"], "status": "LIVE" if health.get("available") else "UNAVAILABLE"}


@router.get("/servers")
async def servers(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    return await to_thread(service.list_servers, limit, offset)


@router.post("/servers", status_code=201)
async def create_server(payload: ServerCreate, request: Request):
    actor = require_dba(request)
    try:
        return await to_thread(service.create_server, payload, actor.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/servers/{server_id}")
async def server_detail(server_id: int):
    row = await to_thread(service.get_server, server_id)
    if not row:
        raise HTTPException(status_code=404, detail="pg_profile server not found")
    return row


@router.post("/servers/{server_id}/verify")
async def verify(server_id: int, request: Request):
    actor = require_dba(request)
    try:
        return await to_thread(service.verify_server, server_id, actor.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/servers/{server_id}/sample")
async def sample(server_id: int, payload: SampleRequest, request: Request):
    actor = require_dba(request)
    if not actor.service and payload.trigger_type != "MANUAL":
        raise HTTPException(status_code=403, detail="interactive callers may request MANUAL samples only")
    try:
        return await to_thread(service.collect_sample, server_id, payload.trigger_type, actor.name,
                               payload.incident_id, payload.idempotency_key, 2)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/servers/{server_id}/samples")
async def samples(server_id: int, days: int = Query(7, ge=1, le=365),
                  limit: int = Query(100, ge=1, le=500)):
    server = await to_thread(service.get_server, server_id, include_private=True)
    if not server:
        raise HTTPException(status_code=404, detail="pg_profile server not found")
    try:
        rows = await to_thread(client.list_samples, server.server_name, days)
    except Exception as exc:
        return {"available": False, "status": "UNAVAILABLE", "items": [], "reason": sanitize_error(exc, 300)}
    return {"available": True, "status": "HISTORICAL", "items": _json_safe(rows[:limit]), "total": len(rows)}


@router.get("/runs")
async def runs(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               server_id: int | None = None):
    return await to_thread(service.list_runs, limit, offset, server_id)


@router.get("/reports")
async def reports(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
                  server_id: int | None = None, incident_id: int | None = None):
    return await to_thread(report_service.list_reports, limit, offset, server_id, incident_id)


@router.post("/reports", status_code=202)
async def create_report(payload: ReportCreate, request: Request):
    actor = require_dba(request)
    try:
        return await to_thread(report_service.generate, payload, actor.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/reports/{report_id}")
async def report_detail(report_id: int):
    row = await to_thread(report_service.get_report, report_id, False)
    if not row:
        raise HTTPException(status_code=404, detail="report not found")
    server = await to_thread(service.get_server, row["pgprofile_server_id"])
    if not server:
        raise HTTPException(status_code=404, detail="report not found")
    return row


@router.get("/reports/{report_id}/content")
async def report_content(report_id: int):
    row = await to_thread(report_service.get_report, report_id, True)
    if not row or not row.get("html"):
        raise HTTPException(status_code=404, detail="sanitized report content unavailable")
    server = await to_thread(service.get_server, row["pgprofile_server_id"])
    if not server:
        raise HTTPException(status_code=404, detail="report not found")
    return Response(content=row["html"], media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": REPORT_CSP, "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "Cache-Control": "private, no-store",
        "Cross-Origin-Resource-Policy": "same-origin",
    })


@router.get("/features")
async def features(server_id: int | None = None, feature_type: str | None = None,
                   limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return await to_thread(feature_extractor.list_features, server_id, feature_type, limit, offset)


@router.get("/query-history")
async def query_history(server_id: int | None = None, database: str | None = None,
                        query_id: str | None = None, limit: int = Query(200, ge=1, le=500),
                        offset: int = Query(0, ge=0)):
    return await to_thread(feature_extractor.query_history, server_id, database, query_id, limit, offset)


@router.get("/baselines")
async def baselines(server_id: int | None = None, limit: int = Query(200, ge=1, le=500),
                    offset: int = Query(0, ge=0)):
    return await to_thread(feature_extractor.list_baselines, server_id, limit, offset)


@router.post("/baselines/{baseline_id}/feedback")
async def baseline_feedback(baseline_id: int, payload: BaselineFeedback, request: Request):
    actor = require_dba(request)
    try:
        return await to_thread(feature_extractor.set_baseline_feedback, baseline_id, payload.state,
                               payload.note, actor.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/retention/run")
async def retention(payload: RetentionRequest, request: Request):
    require_dba(request)
    return await to_thread(retention_service.run, payload.dry_run, payload.server_id)


@router.post("/internal/collect")
async def cron_collect(request: Request):
    actor = require_dba(request)
    if not actor.service:
        raise HTTPException(status_code=403, detail="service identity required")
    return await to_thread(service.scheduled_collect_all)
