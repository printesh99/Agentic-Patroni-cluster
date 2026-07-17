"""Append-only, redacted, canonical evidence persistence."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from typing import Any

from .. import sources as S
from ..db.models import AiEvidenceBundle, AiEvidenceItem
from . import inventory_service

_SECRET_KEYS = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|cookie|credential)")
_DSN = re.compile(r"(?i)(postgres(?:ql)?://)[^@\s]+@")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): ("<REDACTED>" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return [redact(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return _DSN.sub(r"\1<REDACTED>@", value)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(redact(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def create_bundle(db, *, incident_id: int | None = None, window_start=None, window_end=None,
                  warnings: list[str] | None = None) -> AiEvidenceBundle:
    inv = inventory_service.resolve(db)
    cfg = S.resolve_cluster_or_raise(S.CLUSTER_ID)
    row = AiEvidenceBundle(
        bundle_id=str(uuid.uuid4()), inventory_id=inv.id, incident_id=incident_id,
        cluster_id=cfg.cluster_id, cluster_name=inv.cluster_name, namespace=inv.namespace,
        window_start=window_start, window_end=window_end, warnings=warnings or [], action_ready=False,
    )
    db.add(row); db.flush()
    return row


def append_item(db, bundle: AiEvidenceBundle, *, source_type: str, source_name: str,
                collector_name: str, collector_version: str, payload: dict[str, Any],
                source_timestamp: datetime | None = None, trust_tier: str = "VERIFIED",
                max_age_seconds: int = 300, warnings: list[str] | None = None,
                partial: bool = False) -> AiEvidenceItem:
    inv = inventory_service.resolve(db)
    if bundle.inventory_id != inv.id:
        raise inventory_service.InventoryResolutionError("evidence bundle belongs to another inventory")
    now = datetime.now(timezone.utc)
    age = max(0, int((now - source_timestamp).total_seconds())) if source_timestamp else None
    freshness = "STALE" if age is not None and age > max_age_seconds else "FRESH"
    clean = redact(payload)
    row = AiEvidenceItem(
        evidence_id=str(uuid.uuid4()), bundle_id=bundle.bundle_id, inventory_id=inv.id,
        incident_id=bundle.incident_id, source_type=source_type, source_name=source_name,
        collector_name=collector_name, collector_version=collector_version,
        source_timestamp=source_timestamp, freshness_seconds=age, trust_tier=trust_tier,
        freshness_status=freshness, quality_status="PARTIAL" if partial else "COMPLETE",
        partial=partial, warnings=warnings or [], payload=clean,
        payload_sha256=canonical_sha256(clean),
    )
    db.add(row); db.flush()
    return row


def mark_contradictory(bundle: AiEvidenceBundle, warning: str) -> None:
    bundle.quality_status = "CONTRADICTORY"
    bundle.action_ready = False
    bundle.warnings = list(bundle.warnings or []) + [warning]
