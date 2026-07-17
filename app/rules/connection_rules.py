from __future__ import annotations

from typing import Any

from .common import finding, present, threshold


def _pct(features: dict[str, Any], defaults: dict[str, Any], metric: str, prefix: str, runbook: str) -> list[dict[str, Any]]:
    value = features.get(metric)
    if not present(value):
        return []
    crit = threshold(defaults, metric, "critical", 85)
    warn = threshold(defaults, metric, "warning", 75)
    if value >= crit:
        return [finding(f"{prefix}_CRITICAL", "critical", "connections",
                        f"{metric} is above critical threshold", metric, value, crit, runbook)]
    if value >= warn:
        return [finding(f"{prefix}_WARNING", "warning", "connections",
                        f"{metric} is above warning threshold", metric, value, warn, runbook)]
    return []


def evaluate(features: dict[str, Any], _snapshot: dict[str, Any], defaults: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    out.extend(_pct(features, defaults, "active_connections_percent", "ACTIVE_CONNECTIONS", "runbook_connection_exhaustion"))
    out.extend(_pct(features, defaults, "pgbouncer_pool_used_percent", "PGBOUNCER_POOL", "runbook_pgbouncer_exhaustion"))
    return out
