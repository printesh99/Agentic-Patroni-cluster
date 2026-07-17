from __future__ import annotations

from typing import Any

from .common import finding, present, threshold


def evaluate(features: dict[str, Any], _snapshot: dict[str, Any], defaults: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lag = features.get("replication_lag_seconds")
    if present(lag):
        crit = threshold(defaults, "replication_lag_seconds", "critical", 300)
        warn = threshold(defaults, "replication_lag_seconds", "warning", 60)
        if lag >= crit:
            out.append(finding("REPLICATION_LAG_CRITICAL", "critical", "replication",
                               "Replication lag is above critical threshold",
                               "replication_lag_seconds", lag, crit, "runbook_replication_lag"))
        elif lag >= warn:
            out.append(finding("REPLICATION_LAG_WARNING", "warning", "replication",
                               "Replication lag is above warning threshold",
                               "replication_lag_seconds", lag, warn, "runbook_replication_lag"))

    inactive = features.get("logical_slot_inactive_count")
    if present(inactive) and inactive > 0:
        out.append(finding("LOGICAL_SLOT_INACTIVE", "critical", "replication",
                           "One or more logical replication slots are inactive",
                           "logical_slot_inactive_count", inactive, 0, "runbook_logical_replication"))

    retained = features.get("replication_slot_retained_wal_mb")
    if present(retained):
        crit = threshold(defaults, "replication_slot_retained_wal_mb", "critical", 51200)
        warn = threshold(defaults, "replication_slot_retained_wal_mb", "warning", 20480)
        if retained >= crit:
            out.append(finding("SLOT_RETAINED_WAL_CRITICAL", "critical", "replication",
                               "Replication slot retained WAL is above critical threshold",
                               "replication_slot_retained_wal_mb", retained, crit, "runbook_replication_slot_wal"))
        elif retained >= warn:
            out.append(finding("SLOT_RETAINED_WAL_WARNING", "warning", "replication",
                               "Replication slot retained WAL is above warning threshold",
                               "replication_slot_retained_wal_mb", retained, warn, "runbook_replication_slot_wal"))
    return out
