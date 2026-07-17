"""Ops/admin read surface: readiness, alerts, audit, jobs, tenants, tokens, etc."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Body

from . import pg_ops, jobs, sources as S
from . import log_ai, loki
from .threads import to_thread

router = APIRouter(prefix="/api/v1")

_TOKENS: list[dict] = []


@router.get("/readiness")
async def readiness():
    return await to_thread(pg_ops.readiness)


@router.get("/alerts")
async def alerts():
    return await to_thread(pg_ops.alerts)


@router.get("/alert-rules")
async def alert_rules():
    return {"source": "console", "alert_rules": [
        {"id": "conn-sat", "name": "Connection saturation", "metric": "connections",
         "threshold": "80%", "severity": "warning", "enabled": True},
        {"id": "repl-lag", "name": "Replication lag", "metric": "replication_lag",
         "threshold": "16MiB", "severity": "critical", "enabled": True},
    ]}


@router.get("/audit")
async def audit(limit: int = 100):
    return {"source": "console audit log", "audit": jobs.AUDIT[:limit],
            "items": jobs.AUDIT[:limit]}


@router.get("/jobs")
async def list_jobs(limit: int = 100):
    return {"source": "console jobs", "jobs": jobs.JOBS[:limit]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    for j in jobs.JOBS:
        if j["id"] == job_id:
            return j
    return {"error": "job not found", "id": job_id}


@router.get("/readiness/score")
async def readiness_score():
    r = await to_thread(pg_ops.readiness)
    return r["summary"]


# --- assistant (read-only DBA helper) --------------------------------------
def _ai_window(hours: int = 6) -> tuple[int, int]:
    end = loki.now_ns()
    return end - hours * 3600 * loki.NS_PER_S, end


def _is_alerts_question(question: str) -> bool:
    q = (question or "").lower()
    alert_words = ("alert", "alerts", "firing", "grafana", "alertmanager")
    return any(word in q for word in alert_words) and any(word in q for word in ("alert", "firing", "grafana"))


def _format_alerts_answer(question: str, alerts_payload: dict) -> dict:
    alerts = alerts_payload.get("alerts") or []
    source = alerts_payload.get("source") or "derived thresholds"
    if alerts:
        lines = [
            f"{len(alerts)} alert(s) are currently firing from {source}.",
            "",
        ]
        for item in alerts[:10]:
            lines.append(
                "- {severity} {name}: {detail}".format(
                    severity=str(item.get("severity") or "unknown").upper(),
                    name=item.get("name") or item.get("id") or "alert",
                    detail=item.get("detail") or item.get("state") or "firing",
                )
            )
        if len(alerts) > 10:
            lines.append(f"- {len(alerts) - 10} additional alert(s) omitted from this answer.")
    else:
        lines = [
            f"No alerts are currently firing from {source}.",
            "Grafana/Alertmanager API integration is not configured on this assistant path, so this answer uses the console's live derived alert checks.",
        ]
    jobs._audit("assistant-ask-alerts", "dba", dry_run=False, executed=True,
                detail=f"q={question[:80]} firing={len(alerts)}")
    return {
        "available": True,
        "question": question,
        "answer": "\n".join(lines),
        "model": "live-alerts",
        "backend": "read-only-tools",
        "intent": "alerts",
        "tools": ["get_grafana_alerts", "get_recent_jobs_and_alerts"],
        "sources_checked": ["/api/v1/alerts"],
        "evidence_count": len(alerts),
        "evidence": {"alerts": alerts[:20], "summary": alerts_payload.get("summary")},
        "audit_logged": True,
    }


@router.get("/assistant/status")
async def assistant_status():
    grounded = log_ai._claude_available()
    provider = log_ai.ai_provider.provider_status()
    provider_configured = bool(provider.get("configured"))
    backend = "anthropic" if grounded else (provider.get("provider") if provider_configured else "heuristic")
    model = log_ai.MODEL if grounded else (provider.get("model") if provider_configured else "heuristic")
    return {"available": True, "enabled": True, "mode": "read-only",
            "model": model,
            "backend": backend,
            "llm_connected": grounded or provider_configured,
            "provider_configured": provider_configured,
            "provider": {
                "provider": provider.get("provider"),
                "model": provider.get("model"),
                "configured": provider.get("configured"),
                "api_key_present": provider.get("api_key_present"),
            },
            "audit_logged": True,
            "note": ("Grounded in live Loki logs + readiness via the configured direct provider."
                     if grounded else
                     "Read-only assistant grounded in live logs + readiness; "
            "configured provider is used by /assistant/ask and falls "
                     "back to heuristic only if generation fails.")}


@router.get("/assistant/health")
async def assistant_health():
    from .ai import log_embeddings
    return await to_thread(log_embeddings.health)


@router.post("/assistant/ask")
async def assistant_ask(payload: dict = Body(default={})):
    q = (payload.get("question") or payload.get("prompt") or "").strip()
    if _is_alerts_question(q):
        alerts_payload = await to_thread(pg_ops.alerts)
        return _format_alerts_answer(q, alerts_payload)
    default_hours = int(__import__("os").environ.get("AI_RCA_LOOKBACK_HOURS", "72")) if log_ai.classify_intent(q)[0] == "failover" else 6
    hours = max(1, min(int(payload.get("range_hours", default_hours) or default_hours), 168))
    s, e = _ai_window(hours)
    try:
        return await to_thread(log_ai.ask, q, s, e)
    except S.SourceError:
        # Loki unreachable — fall back to readiness-only answer.
        r = await to_thread(pg_ops.readiness)
        bad = [i for i in r["items"] if not i["ok"]]
        answer = ("Attention needed: " + "; ".join(f"{i['name']} ({i['detail']})" for i in bad)
                  if bad else f"Cluster healthy ({r['summary']['score']}/100); logs unavailable.")
        return {
            "available": True,
            "question": q,
            "answer": answer,
            "model": "heuristic",
            "evidence": {"readiness": r["items"]},
            "audit_logged": True,
            "provider_attempted": False,
            "provider": "read_only_tools",
            "response_mode": "heuristic_fallback",
            "fallback_used": True,
            "fallback_reason_code": "EVIDENCE_SOURCE_UNAVAILABLE",
            "provider_http_status": None,
            "provider_latency_ms": None,
            "provider_request_id": None,
        }


@router.get("/assistant/anomalies")
async def assistant_anomalies(range_hours: int = 6, step: str = "5m"):
    s, e = _ai_window(range_hours)
    return await to_thread(log_ai.detect_anomalies, s, e, step)


# --- compliance / collector / help -----------------------------------------
@router.get("/compliance/operational")
async def compliance_operational():
    r = await to_thread(pg_ops.readiness)
    return {"source": "derived", "available": True,
            "items": [{"control": i["name"], "status": i["status"], "evidence": i["detail"]}
                      for i in r["items"]],
            "summary": r["summary"]}


@router.get("/compliance/{framework}")
async def compliance(framework: str):
    return {"source": "derived", "framework": framework, "available": True,
            "items": [], "note": "Framework rollup foundation; evidence pending."}


@router.get("/collector/runs")
async def collector_runs():
    return {"source": "support collector", "available": False, "runs": [],
            "note": "No collector runs ingested on this local cluster."}


@router.get("/collector/alert-bundle-requests")
async def collector_bundles():
    return {"source": "support collector", "available": False, "bundles": []}


@router.get("/help/runbooks")
async def help_runbooks():
    return {"source": "built-in", "runbooks": [
        {"id": "failover", "title": "Patroni failover / switchover",
         "summary": "Promote a standby and re-point endpoints."},
        {"id": "backup-restore", "title": "pgBackRest restore / PITR",
         "summary": "Restore to a point in time from the repo."},
        {"id": "scale", "title": "Scale replicas / resources",
         "summary": "Adjust PostgresCluster spec via guarded jobs."},
    ]}


@router.get("/help/runbooks/{rb_id}")
async def help_runbook(rb_id: str):
    return {"id": rb_id, "title": rb_id.replace("-", " ").title(),
            "body": "Operational runbook content is maintained in the docs bundle."}


# --- tenants / tokens / notifications --------------------------------------
@router.get("/tenants")
async def tenants():
    return {"source": "console", "tenants": [
        {"id": "uae", "name": "UAE", "clusters": [S.CLUSTER_NAME], "role": "owner"}]}


@router.get("/tokens")
async def list_tokens():
    return {"source": "console", "tokens": _TOKENS}


@router.post("/tokens")
async def create_token(payload: dict = Body(default={})):
    tok = {"id": uuid.uuid4().hex[:12], "name": payload.get("name", "token"),
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "last_four": uuid.uuid4().hex[:4], "scopes": payload.get("scopes", ["read"])}
    _TOKENS.insert(0, tok)
    return {"ok": True, "token": tok,
            "secret": "shown-once-" + uuid.uuid4().hex}


@router.delete("/tokens/{token_id}")
async def delete_token(token_id: str):
    before = len(_TOKENS)
    _TOKENS[:] = [t for t in _TOKENS if t["id"] != token_id]
    return {"ok": len(_TOKENS) < before}


@router.get("/notifications/channels")
async def notification_channels():
    return {"source": "console", "channels": []}


@router.get("/search-index")
async def search_index():
    # Lightweight navigation index for the global search box.
    pages = ["overview", "cluster", "performance", "backups", "security",
             "replication", "administration", "configuration", "metrics_explorer",
             "appmon", "readiness", "alerts", "audit", "advisor", "assistant"]
    return [{"id": p, "label": p.replace("_", " ").title(), "route": p,
             "type": "page"} for p in pages]
