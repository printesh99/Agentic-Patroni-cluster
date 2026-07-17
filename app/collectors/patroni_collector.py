"""Patroni status collector for Phase 1 snapshots."""
from __future__ import annotations

from typing import Any

from .. import sources as S


def collect() -> dict[str, Any]:
    warnings: list[str] = []
    try:
        cluster = S.patroni_cluster()
    except Exception as exc:
        return {
            "source": "patroni",
            "available": False,
            "values": {},
            "warnings": [f"patroni cluster unavailable: {exc}"],
        }
    members = cluster.get("members", []) or []
    leader = next((m for m in members if m.get("role") == "leader"), None)
    values = {
        "role": "leader" if leader else None,
        "timeline": cluster.get("timeline") or (leader or {}).get("timeline"),
        "leader": (leader or {}).get("name"),
        "members": len(members),
        "streaming_replicas": sum(1 for m in members if m.get("state") == "streaming"),
    }
    if not leader:
        warnings.append("patroni leader not found")
    return {
        "source": "patroni",
        "available": True,
        "values": values,
        "patroni_status": cluster,
        "warnings": warnings,
    }
