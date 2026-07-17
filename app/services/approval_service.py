"""Human approval workflow for AI-recommended actions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from .. import jobs
from ..db.models import AiActionAudit, AiIncident
from ..db.session import SessionLocal
from ..security import Principal
from .. import ai_config

MUTATING_LEVELS = {"L3", "L4", "L5"}
APPROVALS_REQUIRED = {"L0": 0, "L1": 0, "L2": 0, "L3": 1, "L4": 1, "L5": 2}
SENIOR_LEVELS = {"L4", "L5"}
SENIOR_ROLES = {"platform-admin", "senior-dba", "sre-lead"}


def _serialize(row: AiActionAudit) -> dict[str, Any]:
    return {
        "id": row.id,
        "incident_id": row.incident_id,
        "action_level": row.action_level,
        "action_type": row.action_type,
        "command_preview": row.command_preview,
        "requested_by": row.requested_by,
        "approved_by": row.approved_by,
        "executed_by": row.executed_by,
        "execution_status": row.execution_status,
        "output": row.output,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "mutations_enabled": jobs.mutations_enabled(),
        "approvals_required": APPROVALS_REQUIRED.get(row.action_level or "L5", 2),
    }


def _history(row: AiActionAudit) -> list[dict[str, Any]]:
    if not row.output:
        return []
    try:
        import json
        parsed = json.loads(row.output)
        if isinstance(parsed, dict) and isinstance(parsed.get("history"), list):
            return parsed["history"]
    except Exception:
        pass
    return []


def _write_history(row: AiActionAudit, event: str, actor: Principal, detail: str | None = None) -> None:
    import json
    history = _history(row)
    history.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": actor.subject_id,
        "roles": sorted(actor.roles),
        "detail": detail,
    })
    row.output = json.dumps({"history": history}, indent=2)


def request_action(payload: dict[str, Any], principal: Principal) -> dict[str, Any]:
    actor = principal
    level = str(payload.get("action_level") or payload.get("level") or "L5").upper()
    if level not in APPROVALS_REQUIRED:
        raise HTTPException(status_code=400, detail=f"unsupported action_level {level}")
    incident_id = payload.get("incident_id")
    command_preview = str(payload.get("command_preview") or payload.get("command") or "").strip()
    if not command_preview:
        raise HTTPException(status_code=400, detail="command_preview is required")

    with SessionLocal() as db:
        if incident_id is not None:
            incident = db.get(AiIncident, int(incident_id))
            if incident is None:
                raise HTTPException(status_code=404, detail="incident not found")
        row = AiActionAudit(
            incident_id=int(incident_id) if incident_id is not None else None,
            action_level=level,
            action_type=str(payload.get("action_type") or "ai_recommended_action"),
            command_preview=command_preview,
            requested_by=actor.subject_id,
            execution_status="approved" if APPROVALS_REQUIRED[level] == 0 else "requested",
        )
        _write_history(row, "requested", actor, "command preview captured")
        db.add(row)
        db.commit()
        db.refresh(row)
        return _serialize(row)


def approve_action(action_id: int, payload: dict[str, Any], principal: Principal) -> dict[str, Any]:
    actor = principal
    with SessionLocal() as db:
        row = db.get(AiActionAudit, action_id)
        if row is None:
            raise HTTPException(status_code=404, detail="action not found")
        if row.execution_status in {"rejected", "executed", "blocked"}:
            raise HTTPException(status_code=409, detail=f"cannot approve status {row.execution_status}")
        if row.requested_by == actor.subject_id:
            raise HTTPException(status_code=409, detail="requester cannot approve their own action")
        if row.action_level in SENIOR_LEVELS and not (set(actor.roles) & SENIOR_ROLES):
            raise HTTPException(status_code=403, detail="senior approval role required")
        existing = [p.strip() for p in (row.approved_by or "").split(",") if p.strip()]
        if actor.subject_id not in existing:
            existing.append(actor.subject_id)
        required = APPROVALS_REQUIRED.get(row.action_level or "L5", 2)
        row.approved_by = ", ".join(existing)
        row.execution_status = "approved" if len(existing) >= required else "pending_approval"
        _write_history(row, "approved", actor, f"{len(existing)}/{required} approvals")
        db.commit()
        db.refresh(row)
        return _serialize(row)


def reject_action(action_id: int, payload: dict[str, Any], principal: Principal) -> dict[str, Any]:
    actor = principal
    with SessionLocal() as db:
        row = db.get(AiActionAudit, action_id)
        if row is None:
            raise HTTPException(status_code=404, detail="action not found")
        row.execution_status = "rejected"
        _write_history(row, "rejected", actor, str(payload.get("reason") or "no reason provided"))
        db.commit()
        db.refresh(row)
        return _serialize(row)


def execute_action(action_id: int, payload: dict[str, Any], principal: Principal) -> dict[str, Any]:
    actor = principal
    confirm = bool(payload.get("confirm") or payload.get("execute"))
    with SessionLocal() as db:
        row = db.get(AiActionAudit, action_id)
        if row is None:
            raise HTTPException(status_code=404, detail="action not found")
        if not ai_config.action_execution_allowed():
            row.execution_status = "blocked"
            _write_history(row, "blocked", actor, "agentic execution disabled by policy")
            db.commit(); db.refresh(row)
            return _serialize(row)
        if row.execution_status != "approved":
            _write_history(row, "execute_blocked", actor, f"status {row.execution_status} is not approved")
            db.commit()
            db.refresh(row)
            return _serialize(row)
        if not confirm:
            row.execution_status = "preview_only"
            _write_history(row, "preview_only", actor, "execution confirmation missing")
            db.commit()
            db.refresh(row)
            return _serialize(row)
        if row.action_level in MUTATING_LEVELS and not jobs.mutations_enabled():
            row.execution_status = "blocked"
            _write_history(row, "blocked", actor, "PGC_ALLOW_MUTATIONS is not enabled")
            db.commit()
            db.refresh(row)
            return _serialize(row)
        row.executed_by = actor.subject_id
        row.execution_status = "executed"
        _write_history(row, "executed", actor, "guarded shell recorded execution approval; no arbitrary shell was invoked")
        db.commit()
        db.refresh(row)
        return _serialize(row)


def list_audit(limit: int = 100) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.execute(select(AiActionAudit).order_by(AiActionAudit.id.desc()).limit(limit)).scalars().all()
        return [_serialize(row) for row in rows]
