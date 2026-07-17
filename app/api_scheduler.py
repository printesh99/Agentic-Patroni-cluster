"""Phase 7 scheduler and alert API."""
from __future__ import annotations

from fastapi import APIRouter

from .services import alert_service, scheduler_service
from .threads import to_thread

router = APIRouter(prefix="/api/v1")


@router.post("/scheduler/tick")
async def scheduler_tick():
    return await to_thread(scheduler_service.tick)


@router.post("/scheduler/agent/tick")
async def scheduler_agent_tick():
    return await to_thread(scheduler_service.agent_tick)


@router.post("/scheduler/start")
async def scheduler_start(run_now: bool | None = None):
    return await to_thread(scheduler_service.start, run_now)


@router.post("/scheduler/agent/start")
async def scheduler_agent_start(run_now: bool | None = None):
    return await to_thread(scheduler_service.start_agent, run_now)


@router.post("/scheduler/stop")
async def scheduler_stop():
    return await to_thread(scheduler_service.stop)


@router.get("/scheduler/status")
async def scheduler_status():
    return await to_thread(scheduler_service.status)


@router.get("/alerts/notifications")
async def alert_notifications(limit: int = 50):
    return await to_thread(alert_service.list_alerts, limit)
