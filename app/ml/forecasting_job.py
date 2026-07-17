"""Persist Phase 4 forecasts from health snapshot history."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from .. import ai_config, sources as S
from ..db.models import ClusterHealthSnapshot, MlForecastResult
from ..db.session import SessionLocal
from .forecasting import FORECAST_TARGETS, linear_forecast
from ..services import inventory_service


def run(cluster_name: str | None = None) -> dict[str, Any]:
    cluster = cluster_name or S.CLUSTER_NAME
    thresholds = ai_config.load_thresholds().get("defaults") or {}
    with SessionLocal() as db:
        inv = inventory_service.resolve(db, cluster_name=cluster)
        rows = db.execute(
            select(ClusterHealthSnapshot)
            .where(ClusterHealthSnapshot.inventory_id == inv.id)
            .order_by(ClusterHealthSnapshot.collected_at.asc(), ClusterHealthSnapshot.id.asc())
        ).scalars().all()
        results = []
        for metric in FORECAST_TARGETS:
            points = [(r.collected_at, getattr(r, metric)) for r in rows if getattr(r, metric) is not None]
            spec = thresholds.get(metric) or {}
            forecast = linear_forecast(points, _num(spec.get("warning")), _num(spec.get("critical")))
            raw_output = _json_safe(forecast)
            result_row = MlForecastResult(
                inventory_id=inv.id,
                metric_name=metric,
                current_value=forecast.get("current_value"),
                growth_per_hour=forecast.get("growth_per_hour"),
                predicted_warning_time=forecast.get("predicted_warning_time"),
                predicted_critical_time=forecast.get("predicted_critical_time"),
                severity=forecast.get("severity") or forecast.get("status"),
                raw_output=raw_output,
            )
            db.add(result_row)
            db.flush()
            results.append(_serialize(result_row))
        db.commit()
        return {"available": True, "status": "forecasted", "cluster_name": cluster, "forecasts": results}


def latest(limit: int = 50) -> dict[str, Any]:
    with SessionLocal() as db:
        inv = inventory_service.resolve(db)
        rows = db.execute(
            select(MlForecastResult)
            .where(MlForecastResult.inventory_id == inv.id)
            .order_by(MlForecastResult.forecast_at.desc(), MlForecastResult.id.desc())
            .limit(limit)
        ).scalars().all()
        return {"available": True, "forecasts": [_serialize(r) for r in rows]}


def _serialize(row: MlForecastResult) -> dict[str, Any]:
    return {
        "id": row.id,
        "inventory_id": row.inventory_id,
        "metric_name": row.metric_name,
        "forecast_at": row.forecast_at.isoformat() if row.forecast_at else None,
        "current_value": row.current_value,
        "growth_per_hour": row.growth_per_hour,
        "predicted_warning_time": row.predicted_warning_time.isoformat() if row.predicted_warning_time else None,
        "predicted_critical_time": row.predicted_critical_time.isoformat() if row.predicted_critical_time else None,
        "severity": row.severity,
        "raw_output": _json_safe(row.raw_output),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
