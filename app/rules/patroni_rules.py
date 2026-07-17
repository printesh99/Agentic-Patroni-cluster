from __future__ import annotations

from typing import Any

from .common import finding


def evaluate(_features: dict[str, Any], snapshot: dict[str, Any], _defaults: dict[str, Any]) -> list[dict[str, Any]]:
    status = snapshot.get("patroni_status") or {}
    members = status.get("members") or []
    out: list[dict[str, Any]] = []
    if members:
        leader = next((m for m in members if m.get("role") == "leader"), None)
        if leader is None:
            out.append(finding("PATRONI_LEADER_MISSING", "emergency", "patroni",
                               "Patroni leader is missing", "patroni_leader", None, "present", "runbook_patroni_failover"))
        replicas = [m for m in members if m.get("role") in ("replica", "sync_standby")]
        not_streaming = [m.get("name") for m in replicas if m.get("state") != "streaming"]
        if not_streaming:
            out.append(finding("REPLICA_NOT_STREAMING", "critical", "patroni",
                               "One or more replicas are not streaming",
                               "replica_state", not_streaming, "streaming", "runbook_replication_lag"))
    return out
