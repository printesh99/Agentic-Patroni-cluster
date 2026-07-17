"""Agentic AI DBA recommendation API."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from .services import ai_agent_service
from .threads import to_thread
from .security import Principal, require_principal

router = APIRouter(prefix="/api/ai-agent", tags=["ai-agent"])


@router.get("/status")
async def ai_agent_status():
    return await to_thread(ai_agent_service.status)


@router.post("/run")
async def run_ai_agent(payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return await to_thread(ai_agent_service.run_agent, payload, "MANUAL", principal.subject_id, True)


@router.get("/runs")
async def ai_agent_runs(limit: int = 50):
    return await to_thread(ai_agent_service.list_runs, limit)


@router.get("/runs/{run_id}")
async def ai_agent_run_detail(run_id: int):
    return await to_thread(ai_agent_service.get_run, run_id)


@router.get("/recommendations")
async def ai_agent_recommendations(
    severity: str | None = None,
    category: str | None = None,
    approval_status: str | None = None,
    cluster_name: str | None = None,
    database_name: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    limit: int = 100,
):
    filters = {
        "severity": severity,
        "category": category,
        "approval_status": approval_status,
        "cluster_name": cluster_name,
        "database_name": database_name,
        "created_from": created_from,
        "created_to": created_to,
    }
    return await to_thread(ai_agent_service.list_recommendations, filters, limit)


@router.get("/recommendations/{recommendation_id}")
async def ai_agent_recommendation_detail(recommendation_id: int):
    return await to_thread(ai_agent_service.get_recommendation, recommendation_id)


@router.post("/recommendations/{recommendation_id}/approve")
async def ai_agent_recommendation_approve(recommendation_id: int, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return await to_thread(ai_agent_service.approve_recommendation, recommendation_id, payload, principal)


@router.post("/recommendations/{recommendation_id}/reject")
async def ai_agent_recommendation_reject(recommendation_id: int, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return await to_thread(ai_agent_service.reject_recommendation, recommendation_id, payload, principal)


@router.post("/recommendations/{recommendation_id}/execute")
async def ai_agent_recommendation_execute(recommendation_id: int, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return await to_thread(ai_agent_service.execute_recommendation, recommendation_id, payload, principal)
