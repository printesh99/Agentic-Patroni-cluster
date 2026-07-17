"""Manual health snapshot API for Phase 1."""
from __future__ import annotations

from fastapi import APIRouter

from .services import snapshot_service
from .threads import to_thread

router = APIRouter(prefix="/api/v1")


@router.post("/health-check/run")
async def run_health_check():
    return await to_thread(snapshot_service.collect_and_persist)


@router.post("/health-check/run/{cluster_name}")
async def run_health_check_cluster(cluster_name: str):
    result = await to_thread(snapshot_service.collect_and_persist)
    result["requested_cluster"] = cluster_name
    return result


@router.get("/health-check/latest")
async def latest_health_check():
    return await to_thread(snapshot_service.latest)


@router.get("/health-check/latest/{cluster_name}")
async def latest_health_check_cluster(cluster_name: str):
    result = await to_thread(snapshot_service.latest)
    result["requested_cluster"] = cluster_name
    return result
