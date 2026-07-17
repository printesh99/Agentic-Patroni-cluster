"""Persist cryptographically identified, expiring operational gate evidence."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from ..db.models import OperationalReadinessEvidence
from . import evidence_service, inventory_service

def record(db, gate_name: str, status: str, evidence: dict, principal,
           *, validity_hours: int = 24) -> OperationalReadinessEvidence:
    if gate_name not in {"shadow_validation", "backup_recovery", "identity_boundary"}:
        raise ValueError("unsupported readiness gate")
    if status not in {"PASS", "FAIL"}: raise ValueError("status must be PASS or FAIL")
    inv = inventory_service.resolve(db)
    now = datetime.now(timezone.utc)
    row = OperationalReadinessEvidence(inventory_id=inv.id, gate_name=gate_name, status=status,
        evidence_sha256=evidence_service.canonical_sha256(evidence), evidence=evidence_service.redact(evidence),
        observed_at=now, valid_until=now + timedelta(hours=max(1, min(validity_hours, 720))),
        recorded_by=principal.subject_id)
    db.add(row); db.flush(); return row
