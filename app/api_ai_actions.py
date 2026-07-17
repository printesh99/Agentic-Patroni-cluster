"""Persisted AI action request and approval endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from .services import approval_service
from .security import Principal, require_principal
from .db.session import SessionLocal
from .services import readiness_service

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


@router.post("/readiness/{gate_name}")
async def record_readiness(gate_name: str, payload: dict = Body(default={}), principal: Principal = Depends(require_principal)):
    if not ({"platform-admin", "senior-dba", "sre-lead"} & set(principal.roles)):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="senior operational role required")
    with SessionLocal() as db:
        row = readiness_service.record(db, gate_name, str(payload.get("status") or "FAIL").upper(),
            dict(payload.get("evidence") or {}), principal, validity_hours=int(payload.get("validity_hours") or 24))
        db.commit(); db.refresh(row)
        return {"id": row.id, "gate_name": row.gate_name, "status": row.status,
                "evidence_sha256": row.evidence_sha256, "valid_until": row.valid_until.isoformat()}
