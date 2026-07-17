"""AI DBA recommendation API."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from . import sources as S
from .recommendations import engine, parameter_advisor
from .threads import to_thread

router = APIRouter(prefix="/api/v1/clusters/{cluster_id}", dependencies=[Depends(S.cluster_path_dependency)])


@router.get("/advisor/parameters")
async def advisor_parameters(cluster_id: str, ram_gib: float | None = None, cpu_cores: float | None = None):
    return await to_thread(parameter_advisor.build_response, ram_gib, cpu_cores)


@router.get("/recommendations/summary")
async def recommendations_summary(cluster_id: str):
    payload = await to_thread(engine.list_recommendations, cluster_id, "open", None, 500)
    return {"available": payload.get("available", False), "source": payload.get("source"), "summary": payload.get("summary", {})}


@router.post("/recommendations/run")
async def recommendations_run(cluster_id: str, payload: dict = Body(default={}),
                              ram_gib: float | None = None, cpu_cores: float | None = None,
                              limit: int = 200):
    body_ram = payload.get("ram_gib") if isinstance(payload, dict) else None
    body_cpu = payload.get("cpu_cores") if isinstance(payload, dict) else None
    return await to_thread(engine.run_recommendations, cluster_id,
                           ram_gib if ram_gib is not None else body_ram,
                           cpu_cores if cpu_cores is not None else body_cpu,
                           limit)


@router.get("/recommendations")
async def recommendations(cluster_id: str, status: str | None = "open", category: str | None = None,
                          limit: int = 100, refresh: bool = False,
                          ram_gib: float | None = None, cpu_cores: float | None = None):
    if refresh:
        return await to_thread(engine.run_recommendations, cluster_id, ram_gib, cpu_cores, limit)
    return await to_thread(engine.list_recommendations, cluster_id, status, category, limit)


@router.get("/recommendations/{recommendation_id}")
async def recommendation_detail(cluster_id: str, recommendation_id: int):
    return await to_thread(engine.get_recommendation, cluster_id, recommendation_id)


@router.post("/recommendations/{recommendation_id}/feedback")
async def recommendation_feedback(cluster_id: str, recommendation_id: int, payload: dict = Body(default={})):
    return await to_thread(engine.add_feedback, cluster_id, recommendation_id, payload)
