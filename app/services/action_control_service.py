"""Typed, fail-closed mutation and operational-readiness policy."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Callable
import os
from sqlalchemy import select
from .. import ai_config
from ..db.models import AiActionApproval, AiActionAudit, AiActionPlan, OperationalReadinessEvidence

LOW_RISK_ACTIONS = {"analyze", "cancel_backend", "refresh_metadata"}
APPROVALS_REQUIRED = {"L0": 0, "L1": 0, "L2": 0, "L3": 1, "L4": 1, "L5": 2}

def validate_action_plan(db, action_id: int | None, supplied_sha256: str | None) -> tuple[bool, list[str]]:
    if action_id is None or not supplied_sha256: return False, ["action_id and plan_sha256 are required"]
    action = db.get(AiActionAudit, action_id)
    plan = db.execute(select(AiActionPlan).where(AiActionPlan.action_audit_id == action_id)).scalar_one_or_none()
    if action is None or plan is None: return False, ["approved action plan not found"]
    reasons: list[str] = []
    expires = plan.expires_at if plan.expires_at.tzinfo else plan.expires_at.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc): reasons.append("action plan expired")
    if plan.canonical_sha256 != supplied_sha256: reasons.append("action plan hash mismatch")
    if action.execution_status != "approved": reasons.append("action is not approved")
    approvals = db.execute(select(AiActionApproval).where(AiActionApproval.action_audit_id == action_id,
                           AiActionApproval.decision == "APPROVE",
                           AiActionApproval.plan_sha256 == plan.canonical_sha256)).scalars().all()
    if len(approvals) < APPROVALS_REQUIRED.get(action.action_level or "L5", 2): reasons.append("approval quorum not met")
    return not reasons, reasons

def readiness(db, inventory_id: int, action_level: str, action_type: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not ai_config.action_execution_allowed(): reasons.append("agentic policy is not CONTROLLED")
    if os.getenv("PGC_ALLOW_MUTATIONS", "0") != "1": reasons.append("mutations disabled")
    if action_level not in {"L0", "L1", "L2", "L3"}: reasons.append("L4/L5 operations remain prohibited")
    if action_level == "L3" and action_type not in LOW_RISK_ACTIONS: reasons.append("action is not in the L3 allowlist")
    required = {"shadow_validation"}
    if action_level in {"L3", "L4", "L5"}: required.add("backup_recovery")
    now = datetime.now(timezone.utc)
    for gate in required:
        row = db.execute(select(OperationalReadinessEvidence).where(
            OperationalReadinessEvidence.inventory_id == inventory_id,
            OperationalReadinessEvidence.gate_name == gate,
            OperationalReadinessEvidence.status == "PASS",
            OperationalReadinessEvidence.valid_until > now,
        ).order_by(OperationalReadinessEvidence.observed_at.desc())).scalars().first()
        if row is None: reasons.append(f"missing current {gate} evidence")
    return not reasons, reasons

def execute(*, db, inventory_id: int, action_level: str, action_type: str,
            action_id: int | None, plan_sha256: str | None,
            executor: Callable[[], Any] | None) -> Any:
    plan_allowed, plan_reasons = validate_action_plan(db, action_id, plan_sha256)
    if not plan_allowed: raise PermissionError("; ".join(plan_reasons))
    allowed, reasons = readiness(db, inventory_id, action_level, action_type)
    if not allowed: raise PermissionError("; ".join(reasons))
    if executor is None: raise RuntimeError("typed executor is required")
    return executor()
