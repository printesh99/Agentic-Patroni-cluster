from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable

from . import collectors
from .planner import plan
from .registry import get_source
from .schema import Claim, EvidenceItem, OverallStatus, Section, SectionStatus
from .validator import validate_claims


_SAFETY_TEXT = (
    "Database content and log content are untrusted evidence. Evidence cannot authorize a tool call; "
    "only the user and control plane can authorize an operation. Read-only mode remains active, and "
    "no mutation was executed."
)


def _replication_text(payload: dict[str, Any]) -> str:
    rows = payload["standbys"]
    if not rows:
        text = "No physical HA standby is currently visible in pg_stat_replication."
    else:
        detail = ", ".join(
            f"{r.get('application_name')} {r.get('state')} {r.get('sync_state')} "
            f"with {int(r.get('replay_lag_bytes') or 0)} bytes replay lag" for r in rows
        )
        text = f"Physical replication: {detail}."
    if payload.get("logical_walsenders"):
        text += f" {payload['logical_walsenders']} logical walsender(s) were excluded from HA lag."
    return text


def _wal_text(payload: dict[str, Any]) -> str:
    last = payload.get("last_archived_wal") or "none reported"
    return (
        f"WAL archiver: current WAL segment {payload['current_wal_segment']} at LSN "
        f"{payload['current_wal_lsn']}; last successfully archived WAL {last}; "
        f"archived count {payload['archived_count']}, failed count {payload['failed_count']}. "
        "The current WAL segment is not claimed to have been archived."
    )


_COLLECTORS: dict[str, tuple[str, str, Callable[[], Any], Callable[[dict[str, Any]], str]]] = {
    "replication_physical": (
        "pg_stat_replication", "PhysicalReplicationEvidence/v1",
        collectors.collect_physical_replication, _replication_text,
    ),
    "wal_archiver": (
        "pg_stat_archiver", "WalArchiverEvidence/v1",
        collectors.collect_wal_archiver, _wal_text,
    ),
}

_LEGACY_INTENTS = {
    "replication_physical": "replication_lag",
    "wal_archiver": "wal_archive",
}


def _overall(sections: list[Section]) -> OverallStatus:
    complete = sum(s.status == SectionStatus.COMPLETE for s in sections)
    unavailable = sum(s.status == SectionStatus.SOURCE_UNAVAILABLE for s in sections)
    if complete == len(sections):
        return OverallStatus.ANSWERED
    if complete:
        return OverallStatus.PARTIAL
    if unavailable == len(sections):
        return OverallStatus.SOURCE_UNAVAILABLE
    return OverallStatus.INSUFFICIENT_EVIDENCE


def try_answer(question: str) -> dict[str, Any] | None:
    query_plan = plan(question)
    if query_plan.unsafe_only:
        return {
            "available": True, "question": question, "answer": _SAFETY_TEXT,
            "status": OverallStatus.UNSAFE_REQUEST.value,
            "intent": "safety", "intents": ["safety_injection"],
            "sections": [], "evidence_items": [], "claims": [],
            "missing_evidence": [], "sources_checked": ["read-only guardrail"],
            "unsupported_claims": [],
            "safety": {"read_only": True, "mutation_executed": False, "injection_detected": True},
            "model": "fixed-safety-contract", "evidence": {},
        }
    if not query_plan.intents:
        return None
    if query_plan.intents == ["unknown_scope"]:
        answer = (
            "Live evidence is insufficient when required authoritative sources are absent, stale, "
            "unavailable, or contradictory. Known facts must remain separate from unknowns; the safe "
            "next step is the smallest read-only check that obtains the missing evidence."
        )
        return {
            "available": True, "question": question, "answer": answer,
            "status": OverallStatus.INSUFFICIENT_EVIDENCE.value,
            "intent": "unknown", "intents": query_plan.intents, "sections": [],
            "evidence_items": [], "claims": [], "missing_evidence": ["requested_domain"],
            "sources_checked": ["evidence contract"], "unsupported_claims": [],
            "safety": {"read_only": True, "mutation_executed": False,
                       "injection_detected": False},
            "model": "fixed insufficient-evidence contract", "evidence": {},
        }
    if query_plan.intents == ["source_failure_contract"]:
        answer = (
            "If Loki is unavailable, the section status is source_unavailable and the missing evidence "
            "is reported explicitly. No zero log count or other result is invented; a retry is a safe "
            "read-only next step."
        )
        return {
            "available": True, "question": question, "answer": answer,
            "status": OverallStatus.SOURCE_UNAVAILABLE.value,
            "intent": "unknown", "intents": query_plan.intents, "sections": [],
            "evidence_items": [], "claims": [], "missing_evidence": ["loki"],
            "sources_checked": ["Loki evidence contract"], "unsupported_claims": [],
            "safety": {"read_only": True, "mutation_executed": False,
                       "injection_detected": False},
            "model": "fixed source-availability contract", "evidence": {},
        }

    collected: dict[str, tuple[datetime, Any]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(query_plan.intents))) as pool:
        futures = {}
        for intent in query_plan.intents:
            started = datetime.now(timezone.utc)
            futures[pool.submit(_COLLECTORS[intent][2])] = (intent, started)
        for future in as_completed(futures):
            intent, started = futures[future]
            try:
                collected[intent] = (started, future.result())
            except Exception as exc:
                failures[intent] = type(exc).__name__

    sections, evidence_items, claims = [], [], []
    legacy_evidence: dict[str, Any] = {}
    for position, intent in enumerate(query_plan.intents, 1):
        source, contract, _collector, renderer = _COLLECTORS[intent]
        registered = get_source(source)
        contract = registered.evidence_contract
        if intent in failures:
            sections.append(Section(
                intent=intent, status=SectionStatus.SOURCE_UNAVAILABLE, source=source,
                source_errors=[failures[intent]],
            ))
            continue
        started, contract_value = collected[intent]
        payload = contract_value.model_dump(mode="json")
        evidence_id = f"ev-{uuid.uuid4().hex[:12]}"
        text = renderer(payload)
        evidence_items.append(EvidenceItem(
            id=evidence_id, contract=contract, source=source,
            collection_started_at=started, collected_at=contract_value.collected_at,
            payload=payload,
        ))
        sections.append(Section(
            intent=intent, status=SectionStatus.COMPLETE, source=source,
            evidence_ids=[evidence_id], text=text,
        ))
        claims.append(Claim(id=f"claim-{position}", type="fact", text=text,
                            evidence_ids=[evidence_id]))
        legacy_evidence[intent] = payload

    answer_parts = [s.text for s in sections if s.text]
    if query_plan.injection_detected:
        answer_parts.append(_SAFETY_TEXT)
    if failures:
        answer_parts.append("Unavailable source(s): " + ", ".join(sorted(failures)) + ".")
    status = _overall(sections)
    claims, unsupported_claims = validate_claims(claims, evidence_items)
    return {
        "available": True, "question": question, "answer": "\n\n".join(answer_parts),
        "status": status.value,
        "intent": ("multi_intent" if len(query_plan.intents) > 1
                   else _LEGACY_INTENTS[query_plan.intents[0]]),
        "intents": query_plan.intents,
        "sections": [s.model_dump(mode="json") for s in sections],
        "evidence_items": [e.model_dump(mode="json") for e in evidence_items],
        "claims": [c.model_dump(mode="json") for c in claims],
        "missing_evidence": [i for i in query_plan.intents if i in failures],
        "sources_checked": [_COLLECTORS[i][0] for i in query_plan.intents],
        "unsupported_claims": unsupported_claims,
        "safety": {"read_only": True, "mutation_executed": False,
                   "injection_detected": query_plan.injection_detected},
        "model": "live-data (typed assistant pipeline)", "evidence": legacy_evidence,
    }
