from __future__ import annotations

from typing import Any

from .common import finding, present, threshold


def evaluate(features: dict[str, Any], _snapshot: dict[str, Any], defaults: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    checks = [
        ("locks_waiting_count", "LOCKS_WAITING", "lock_contention", "runbook_lock_contention"),
        ("long_txn_count", "LONG_TRANSACTIONS", "transactions", "runbook_long_transactions"),
        ("idle_in_transaction_count", "IDLE_IN_TRANSACTION", "transactions", "runbook_idle_in_transaction"),
    ]
    for metric, prefix, category, runbook in checks:
        value = features.get(metric)
        if not present(value):
            continue
        crit = threshold(defaults, metric, "critical", 10)
        warn = threshold(defaults, metric, "warning", 3)
        if value >= crit:
            out.append(finding(f"{prefix}_CRITICAL", "critical", category,
                               f"{metric} is above critical threshold", metric, value, crit, runbook))
        elif value >= warn:
            out.append(finding(f"{prefix}_WARNING", "warning", category,
                               f"{metric} is above warning threshold", metric, value, warn, runbook))

    deadlocks = features.get("deadlocks_per_min")
    if present(deadlocks) and deadlocks > 0:
        out.append(finding("DEADLOCKS_DETECTED", "critical", "lock_contention",
                           "Deadlocks detected in the recent window",
                           "deadlocks_per_min", deadlocks, 0, "runbook_deadlocks"))
    return out
