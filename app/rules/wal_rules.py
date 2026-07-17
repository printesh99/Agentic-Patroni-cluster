from __future__ import annotations

from typing import Any

from .common import finding, present, threshold


def _pct_rule(features: dict[str, Any], defaults: dict[str, Any], metric: str, rule_prefix: str, category: str, runbook: str) -> list[dict[str, Any]]:
    value = features.get(metric)
    if not present(value):
        return []
    emergency = threshold(defaults, metric, "emergency", 95)
    critical = threshold(defaults, metric, "critical", 85)
    warning = threshold(defaults, metric, "warning", 75)
    if value >= emergency:
        return [finding(f"{rule_prefix}_EMERGENCY", "emergency", category,
                        f"{metric} is above emergency threshold", metric, value, emergency, runbook)]
    if value >= critical:
        return [finding(f"{rule_prefix}_CRITICAL", "critical", category,
                        f"{metric} is above critical threshold", metric, value, critical, runbook)]
    if value >= warning:
        return [finding(f"{rule_prefix}_WARNING", "warning", category,
                        f"{metric} is above warning threshold", metric, value, warning, runbook)]
    return []


def evaluate(features: dict[str, Any], _snapshot: dict[str, Any], defaults: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    out.extend(_pct_rule(features, defaults, "wal_pvc_used_percent", "WAL_PVC_USED", "wal", "runbook_wal_disk_full"))
    out.extend(_pct_rule(features, defaults, "pgdata_pvc_used_percent", "PGDATA_PVC_USED", "storage", "runbook_pgdata_disk_pressure"))
    return out
