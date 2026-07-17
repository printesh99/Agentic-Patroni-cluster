"""Grounded RCA generator for incidents."""
from __future__ import annotations

from typing import Any

from .. import log_ai
from . import rag_retriever
from .prompt_templates import rca_prompt


def explain_incident(incident: dict[str, Any]) -> dict[str, Any]:
    import time
    started = time.perf_counter()
    runbook_id = ((incident.get("rag_context") or {}).get("recommended_runbook_id")
                  or ((incident.get("evidence") or {}).get("risk") or {}).get("recommended_runbook_id"))
    snippets = rag_retriever.retrieve(runbook_id=runbook_id, query=incident.get("incident_type"))
    prompt = rca_prompt(incident, snippets)
    answer = _heuristic_rca(incident, snippets)
    mode = "heuristic"
    # Reuse Claude availability but keep this deterministic unless configured.
    if log_ai._claude_available():
        try:
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=log_ai.MODEL,
                max_tokens=1200,
                system=log_ai._SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = "".join(b.text for b in msg.content if b.type == "text").strip()
            mode = "llm"
        except Exception:
            pass
    try:
        from .. import metrics
        metrics.RCA_CALLS.labels(mode=mode).inc()
        metrics.RCA_LATENCY.labels(mode=mode).observe(time.perf_counter() - started)
    except Exception:
        pass
    return {"summary": answer, "runbook_snippets": snippets, "runbook_id": runbook_id, "mode": mode}


def _heuristic_rca(incident: dict[str, Any], snippets: list[dict]) -> str:
    evidence = incident.get("evidence") or {}
    risk = evidence.get("risk") or {}
    reasons = risk.get("reasons") or []
    rule_count = len(incident.get("rule_findings") or [])
    log_count = len(evidence.get("log_findings") or [])
    runbook_id = risk.get("recommended_runbook_id") or (snippets[0]["runbook_id"] if snippets else "runbook_general_dba_triage")
    approval = "Approval-required actions: failover/switchover, pod restart, slot drop, restore, destructive SQL, or config changes."
    return (
        f"Severity: {incident.get('severity')}. Incident category: {incident.get('incident_type')}. "
        f"What happened: risk score {risk.get('risk_score')} was calculated for {incident.get('cluster_name')}. "
        f"Evidence: {rule_count} rule finding(s), {log_count} log finding(s), ML and forecast context were attached. "
        f"Key reasons: {'; '.join(reasons[:4]) or 'No specific reasons available'}. "
        f"Likely root cause: validate the top rule/log evidence before action; current evidence points to {incident.get('incident_type')} risk. "
        f"Immediate safe checks: inspect readiness, logs, pg_stat views, and runbook {runbook_id}. "
        f"Recommended runbook: {runbook_id}. {approval} "
        f"Business impact: possible service degradation if the condition persists. Confidence score: {incident.get('confidence')}."
    )
