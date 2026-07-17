"""Human approval workflow for AI-recommended actions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from .. import jobs
from ..db.models import AiActionAudit, AiActionApproval, AiActionPlan, AiIncident
from ..db.session import SessionLocal
from ..security import Principal
from .. import ai_config
from . import action_control_service, evidence_service, inventory_service

MUTATING_LEVELS = {"L3", "L4", "L5"}
APPROVALS_REQUIRED = {"L0": 0, "L1": 0, "L2": 0, "L3": 1, "L4": 1, "L5": 2}
SENIOR_LEVELS = {"L4", "L5"}
SENIOR_ROLES = {"platform-admin", "senior-dba", "sre-lead"}


def _serialize(row: AiActionAudit, db=None) -> dict[str, Any]:
    plan = db.execute(select(AiActionPlan).where(AiActionPlan.action_audit_id == row.id)).scalar_one_or_none() if db else None
    approvals = db.execute(select(AiActionApproval).where(AiActionApproval.action_audit_id == row.id,
                           AiActionApproval.decision == "APPROVE")).scalars().all() if db else []
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
        "approvals_received": len(approvals),
        "plan_sha256": plan.canonical_sha256 if plan else None,
        "expires_at": plan.expires_at.isoformat() if plan else None,
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
        db.flush()
        expires_minutes = max(5, min(int(payload.get("expires_minutes") or 60), 1440))
        canonical = {"action_audit_id": row.id, "incident_id": row.incident_id,
                     "action_level": level, "action_type": row.action_type,
                     "command_preview": command_preview, "requested_by": actor.subject_id}
        db.add(AiActionPlan(plan_id=str(uuid.uuid4()), action_audit_id=row.id,
                           canonical_sha256=evidence_service.canonical_sha256(canonical),
                           canonical_payload=evidence_service.redact(canonical),
                           expires_at=datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)))
        db.commit()
        db.refresh(row)
        return _serialize(row, db)


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
        plan = db.execute(select(AiActionPlan).where(AiActionPlan.action_audit_id == row.id)).scalar_one()
        if plan.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
            row.execution_status = "expired"; db.commit()
            raise HTTPException(status_code=409, detail="action plan expired")
        existing_rows = db.execute(select(AiActionApproval).where(
            AiActionApproval.action_audit_id == row.id, AiActionApproval.decision == "APPROVE")).scalars().all()
        existing = [p.subject_id for p in existing_rows]
        if actor.subject_id not in existing:
            db.add(AiActionApproval(action_audit_id=row.id, plan_sha256=plan.canonical_sha256,
                subject_id=actor.subject_id, decision="APPROVE", roles=sorted(actor.roles),
                reason=str(payload.get("reason") or "approved")))
            existing.append(actor.subject_id)
        required = APPROVALS_REQUIRED.get(row.action_level or "L5", 2)
        row.approved_by = ", ".join(existing)
        row.execution_status = "approved" if len(existing) >= required else "pending_approval"
        _write_history(row, "approved", actor, f"{len(existing)}/{required} approvals")
        db.commit()
        db.refresh(row)
        return _serialize(row, db)


def reject_action(action_id: int, payload: dict[str, Any], principal: Principal) -> dict[str, Any]:
    actor = principal
    with SessionLocal() as db:
        row = db.get(AiActionAudit, action_id)
        if row is None:
            raise HTTPException(status_code=404, detail="action not found")
        row.execution_status = "rejected"
        plan = db.execute(select(AiActionPlan).where(AiActionPlan.action_audit_id == row.id)).scalar_one()
        previous = db.execute(select(AiActionApproval).where(AiActionApproval.action_audit_id == row.id,
                              AiActionApproval.subject_id == actor.subject_id)).scalar_one_or_none()
        if previous is None:
            db.add(AiActionApproval(action_audit_id=row.id, plan_sha256=plan.canonical_sha256,
                subject_id=actor.subject_id, decision="REJECT", roles=sorted(actor.roles),
                reason=str(payload.get("reason") or "no reason provided")))
        _write_history(row, "rejected", actor, str(payload.get("reason") or "no reason provided"))
        db.commit()
        db.refresh(row)
        return _serialize(row, db)


def execute_action(action_id: int, payload: dict[str, Any], principal: Principal) -> dict[str, Any]:
    actor = principal
    confirm = bool(payload.get("confirm") or payload.get("execute"))
    with SessionLocal() as db:
        row = db.get(AiActionAudit, action_id)
        if row is None:
            raise HTTPException(status_code=404, detail="action not found")
        plan = db.execute(select(AiActionPlan).where(AiActionPlan.action_audit_id == row.id)).scalar_one_or_none()
        expires = plan.expires_at if plan and plan.expires_at.tzinfo else (plan.expires_at.replace(tzinfo=timezone.utc) if plan else None)
        if plan is None or expires <= datetime.now(timezone.utc):
            row.execution_status = "expired"; _write_history(row, "expired", actor, "canonical plan expired")
            db.commit(); db.refresh(row); return _serialize(row, db)
        plan_allowed, plan_reasons = action_control_service.validate_action_plan(
            db, row.id, str(payload.get("plan_sha256") or "") or None)
        if not plan_allowed:
            _write_history(row, "execute_blocked", actor, "; ".join(plan_reasons))
            db.commit(); db.refresh(row); return _serialize(row, db)
        if not ai_config.action_execution_allowed():
            row.execution_status = "blocked"
            _write_history(row, "blocked", actor, "agentic execution disabled by policy")
            db.commit(); db.refresh(row)
            return _serialize(row, db)
        if row.execution_status != "approved":
            _write_history(row, "execute_blocked", actor, f"status {row.execution_status} is not approved")
            db.commit()
            db.refresh(row)
            return _serialize(row, db)
        if not confirm:
            row.execution_status = "preview_only"
            _write_history(row, "preview_only", actor, "execution confirmation missing")
            db.commit()
            db.refresh(row)
            return _serialize(row, db)
        if row.action_level in MUTATING_LEVELS and not jobs.mutations_enabled():
            row.execution_status = "blocked"
            _write_history(row, "blocked", actor, "PGC_ALLOW_MUTATIONS is not enabled")
            db.commit()
            db.refresh(row)
            return _serialize(row, db)
        inv = inventory_service.resolve(db)
        allowed, reasons = action_control_service.readiness(db, inv.id, row.action_level or "L5", row.action_type or "")
        if not allowed:
            row.execution_status = "blocked"; _write_history(row, "blocked", actor, "; ".join(reasons))
            db.commit(); db.refresh(row); return _serialize(row, db)
        row.executed_by = actor.subject_id
        row.execution_status = "executed"
        _write_history(row, "executed", actor, "guarded shell recorded execution approval; no arbitrary shell was invoked")
        db.commit()
        db.refresh(row)
        return _serialize(row, db)


def list_audit(limit: int = 100) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.execute(select(AiActionAudit).order_by(AiActionAudit.id.desc()).limit(limit)).scalars().all()
        return [_serialize(row, db) for row in rows]
