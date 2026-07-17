"""Phase 4 forecasting API."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Body

from .ml import forecasting, forecasting_job
from .threads import to_thread

router = APIRouter(prefix="/api/v1")


@router.post("/ml/forecast/test")
async def test_forecast(payload: dict = Body(default={})):
    now = datetime.now(timezone.utc)
    current = float(payload.get("current_value", 78))
    growth = float(payload.get("growth_per_hour", 8.2))
    warning = float(payload.get("warning", 85))
    critical = float(payload.get("critical", 95))
    points = [
        (now - timedelta(hours=2), current - growth * 2),
        (now - timedelta(hours=1), current - growth),
        (now, current),
    ]
    result = forecasting.linear_forecast(points, warning, critical)
    return {"available": True, "metric_name": payload.get("metric_name", "wal_pvc_used_percent"), **_serialize_test(result)}


@router.post("/ml/forecast/{cluster_name}")
async def run_forecast(cluster_name: str):
    return await to_thread(forecasting_job.run, cluster_name)


@router.get("/ml/forecasts")
async def list_forecasts(limit: int = 50):
    return await to_thread(forecasting_job.latest, limit)


def _serialize_test(result: dict) -> dict:
    out = dict(result)
    for key in ("predicted_warning_time", "predicted_critical_time"):
        if out.get(key) is not None:
            out[key] = out[key].isoformat()
    return out
