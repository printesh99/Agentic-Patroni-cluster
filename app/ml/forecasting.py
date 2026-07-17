"""Simple trend forecasting for Phase 4."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

FORECAST_TARGETS = [
    "wal_pvc_used_percent",
    "pgdata_pvc_used_percent",
    "active_connections_percent",
    "pgbouncer_pool_used_percent",
    "replication_lag_seconds",
    "backup_duration_minutes",
]


def linear_forecast(
    points: list[tuple[datetime, float]],
    warning_threshold: float | None,
    critical_threshold: float | None,
    near_term_hours: float = 6.0,
) -> dict[str, Any]:
    clean = [(ts, val) for ts, val in points if ts is not None and val is not None]
    if len(clean) < 3:
        return {"available": False, "status": "insufficient_data", "points": len(clean)}
    clean.sort(key=lambda p: p[0])
    start = clean[0][0]
    xs = [(ts - start).total_seconds() / 3600.0 for ts, _ in clean]
    ys = [float(v) for _, v in clean]
    current_ts, current_value = clean[-1]
    slope = _slope(xs, ys)
    if slope <= 0:
        return {
            "available": True,
            "status": "flat_or_declining",
            "current_value": current_value,
            "growth_per_hour": slope,
            "severity": "normal",
            "predicted_warning_time": None,
            "predicted_critical_time": None,
            "points": len(clean),
        }
    warning_time = _crossing(current_ts, current_value, slope, warning_threshold)
    critical_time = _crossing(current_ts, current_value, slope, critical_threshold)
    severity = _severity(current_value, slope, warning_threshold, critical_threshold, warning_time, critical_time, near_term_hours)
    return {
        "available": True,
        "status": "forecasted",
        "current_value": current_value,
        "growth_per_hour": slope,
        "severity": severity,
        "predicted_warning_time": warning_time,
        "predicted_critical_time": critical_time,
        "points": len(clean),
    }


def _slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom


def _crossing(current_ts: datetime, current: float, slope: float, threshold: float | None) -> datetime | None:
    if threshold is None:
        return None
    if current >= threshold:
        return current_ts
    if slope <= 0:
        return None
    hours = (threshold - current) / slope
    if hours < 0:
        return current_ts
    return current_ts + timedelta(hours=hours)


def _severity(current: float, slope: float, warning: float | None, critical: float | None,
              warning_time: datetime | None, critical_time: datetime | None, near_term_hours: float) -> str:
    now = datetime.now(timezone.utc)
    near = now + timedelta(hours=near_term_hours)
    if critical is not None and current >= critical and slope > 0:
        return "emergency"
    if critical_time is not None and critical_time <= near:
        return "critical"
    if warning is not None and current >= warning:
        return "warning"
    if warning_time is not None and warning_time <= near:
        return "warning"
    if warning_time is not None or critical_time is not None:
        return "info"
    return "normal"
