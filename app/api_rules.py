"""Phase 2 deterministic rule-engine API."""
from __future__ import annotations

from fastapi import APIRouter, Body

from .rules import engine
from .services import snapshot_service
from .threads import to_thread

router = APIRouter(prefix="/api/v1")


@router.get("/rules/evaluate/latest")
async def evaluate_latest_rules():
    latest = await to_thread(snapshot_service.latest)
    if not latest.get("available"):
        return latest
    return await to_thread(engine.evaluate, latest)


@router.post("/rules/evaluate")
async def evaluate_rules(payload: dict = Body(default={})):
    snapshot = payload if "features" in payload else {"features": payload}
    return await to_thread(engine.evaluate, snapshot)
