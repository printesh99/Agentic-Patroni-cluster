"""Deduped alert notifications for Phase 7."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ALERTS: list[dict[str, Any]] = []
_SEEN: set[tuple[int, str]] = set()


def emit_for_incident(incident: dict[str, Any]) -> dict[str, Any]:
    key = (int(incident["id"]), incident.get("severity") or "unknown")
    if key in _SEEN:
        return {"sent": False, "reason": "deduped", "key": key}
    _SEEN.add(key)
    alert = {
        "id": f"alert-{incident['id']}-{incident.get('severity')}",
        "incident_id": incident["id"],
        "severity": incident.get("severity"),
        "title": incident.get("title"),
        "status": incident.get("status"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "channel": "local",
    }
    ALERTS.insert(0, alert)
    return {"sent": True, "alert": alert}


def list_alerts(limit: int = 50) -> dict[str, Any]:
    return {"available": True, "alerts": ALERTS[:limit]}
