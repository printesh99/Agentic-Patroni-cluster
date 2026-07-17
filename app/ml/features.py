"""Normalize collector output into a fixed Phase 1 health feature vector."""
from __future__ import annotations

from typing import Any

FEATURE_FIELDS = [
    "replication_lag_seconds",
    "wal_rate_mb_min",
    "wal_pvc_used_percent",
    "pgdata_pvc_used_percent",
    "active_connections_percent",
    "pgbouncer_pool_used_percent",
    "cpu_percent",
    "memory_percent",
    "deadlocks_per_min",
    "locks_waiting_count",
    "long_txn_count",
    "idle_in_transaction_count",
    "archive_failed_count",
    "backup_duration_minutes",
    "pod_restart_count",
    "logical_slot_inactive_count",
    "replication_slot_retained_wal_mb",
    "pg_stat_statements_slow_query_count",
    "temp_files_mb",
]


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    merged: dict[str, Any] = {}
    for collector_name, payload in raw.items():
        if not isinstance(payload, dict):
            warnings.append(f"{collector_name} payload is invalid")
            continue
        merged.update(payload.get("values") or {})
        warnings.extend(payload.get("warnings") or [])
    if merged.get("replication_lag_seconds") is None and merged.get("replication_lag_bytes") == 0:
        merged["replication_lag_seconds"] = 0.0

    values: dict[str, float | None] = {}
    for field in FEATURE_FIELDS:
        value = _num(merged.get(field))
        values[field] = value
        if value is None:
            warnings.append(f"feature {field} missing")

    role = (raw.get("patroni") or {}).get("values", {}).get("role")
    timeline = _num((raw.get("patroni") or {}).get("values", {}).get("timeline"))
    patroni_status = (raw.get("patroni") or {}).get("patroni_status")
    return {
        "role": role,
        "timeline": int(timeline) if timeline is not None else None,
        "features": values,
        "warnings": sorted(set(warnings)),
        "patroni_status": patroni_status,
        "raw_metrics": raw,
    }
