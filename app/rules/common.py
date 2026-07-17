from __future__ import annotations

from typing import Any


SEVERITY_RANK = {"info": 1, "warning": 2, "critical": 3, "emergency": 4}


def finding(
    rule_id: str,
    severity: str,
    category: str,
    message: str,
    metric: str,
    value: Any,
    threshold: Any,
    runbook_id: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "category": category,
        "message": message,
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "recommended_runbook_id": runbook_id,
    }


def threshold(defaults: dict[str, Any], metric: str, level: str, fallback: float) -> float:
    spec = defaults.get(metric) or {}
    try:
        return float(spec.get(level, fallback))
    except (TypeError, ValueError):
        return fallback


def present(value: Any) -> bool:
    return value is not None
