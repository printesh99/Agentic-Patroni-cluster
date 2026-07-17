"""Persisted AI action request and approval endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from .services import approval_service
from .security import Principal, require_principal

router = APIRouter(prefix="/api/v1/actions", tags=["ai-actions"])


@router.post("/request")
async def request_action(payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return approval_service.request_action(payload, principal)


@router.get("/audit")
async def action_audit(limit: int = 100):
    return {"actions": approval_service.list_audit(limit=limit)}


@router.post("/{action_id}/approve")
async def approve_action(action_id: int, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return approval_service.approve_action(action_id, payload, principal)


@router.post("/{action_id}/reject")
async def reject_action(action_id: int, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return approval_service.reject_action(action_id, payload, principal)


@router.post("/{action_id}/execute")
async def execute_action(action_id: int, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    return approval_service.execute_action(action_id, payload, principal)
