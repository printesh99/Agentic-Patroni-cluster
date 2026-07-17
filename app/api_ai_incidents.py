"""Phase 5 AI incident API."""
from __future__ import annotations

from fastapi import APIRouter

from .services import incident_service
from .threads import to_thread

router = APIRouter(prefix="/api/v1")


@router.post("/ai/incidents/evaluate")
async def evaluate_incident():
    return await to_thread(incident_service.evaluate_and_upsert)


@router.get("/ai/incidents")
async def list_incidents(limit: int = 50):
    return await to_thread(incident_service.list_incidents, limit)


@router.get("/ai/incidents/{incident_id}")
async def get_incident(incident_id: int):
    return await to_thread(incident_service.get_incident, incident_id)


@router.post("/ai/incidents/{incident_id}/explain")
async def explain_incident(incident_id: int):
    return await to_thread(incident_service.explain, incident_id)


@router.post("/ai/incidents/{incident_id}/close")
async def close_incident(incident_id: int):
    return await to_thread(incident_service.set_status, incident_id, "closed")


@router.post("/ai/incidents/{incident_id}/acknowledge")
async def acknowledge_incident(incident_id: int):
    return await to_thread(incident_service.set_status, incident_id, "acknowledged")
