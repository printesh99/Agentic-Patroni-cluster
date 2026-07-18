"""AI log intelligence (Phase 5) — RCA + anomaly detection over the log pillar.

Grounds the read-only assistant in actual Loki slices. Uses the configured
provider when available and otherwise falls back to a deterministic heuristic
so the console works fully offline. Only redacted log excerpts are sent to a
configured model.

Guardrails: read-only; token-budgeted excerpts; grounded-only prompt; every
assistant query is audited via ``jobs._audit``.
"""
from __future__ import annotations

import os
import json
import statistics
import threading
import time
import uuid
from typing import Any

from . import loki, log_parse, log_signatures as sig, pg_log_analytics as A, sources as S
from . import jobs
from .services import ai_provider

# Legacy direct-Anthropic model override; env-driven providers use AI_MODEL.
MODEL = os.environ.get("PGC_AI_MODEL", "claude-opus-4-8")
MAX_EVIDENCE_LINES = 40

# Intent → the log components most relevant to that kind of question.
_LIFECYCLE_RE = r"(?i)promoted|demoted|lock owner|i am the leader|new leader|shutting down|postmaster|starting"
_INTENTS: list[tuple[str, set[str], list[str], str | None]] = [
    ("failover", {"failover", "fail over", "failed over", "switchover", "switch over",
                  "promote", "promotion", "leader", "patroni", "election", "restart", "restarted"}, ["database"], _LIFECYCLE_RE),
    ("replication", {"replication", "standby", "replica", "lag", "wal sender", "streaming", "slot"}, ["database"], r"(?i)replication|standby|wal|timeline|slot"),
    ("authentication", {"auth", "login", "password", "permission", "denied", "pg_hba", "role"}, ["database", "pgbouncer"], r"(?i)auth|login|password|permission|denied|pg_hba"),
    ("connection", {"connection", "connect", "too many", "timeout", "pool", "pgbouncer", "client"}, ["database", "pgbouncer"], r"(?i)connection|connect|timeout|pool|client"),
    ("backup", {"backup", "pgbackrest", "restore", "archive", "wal", "pitr"}, ["database"], r"(?i)backup|pgbackrest|restore|archive|wal"),
    ("slow_query", {"slow", "duration", "query", "performance", "latency"}, ["database"], r"(?i)duration|statement|slow"),
    ("errors", {"error", "fatal", "crash", "fail", "exception", "down"}, ["database", "pgbouncer"], r"(?i)error|fatal|panic|warning|critical|exception"),
]


def classify_intent(question: str) -> tuple[str, list[str]]:
    q = (question or "").lower()
    for name, kws, comps, _line_regex in _INTENTS:
        if any(k in q for k in kws):
            return name, comps
    return "errors", ["database", "pgbouncer"]


def _intent_regex(intent: str) -> str | None:
    return next((line_regex for name, _kws, _comps, line_regex in _INTENTS if name == intent), None)


def gather_context(question: str, start_ns: int, end_ns: int) -> dict[str, Any]:
    """Use fresh semantic evidence first; query live Loki only as fallback."""
    intent, components = classify_intent(question)
    from .ai import log_embeddings
    store = log_embeddings.search(question, S.CLUSTER_NAME, start_ns, end_ns,
                                  log_types=components, limit=MAX_EVIDENCE_LINES)
    if store.get("fresh") and store.get("entries"):
        entries = store["entries"]
        query = "pgvector cosine search (cluster/source/time scoped)"
        evidence_source = "store"
    else:
        query = log_parse.build_query(q=None, components=components, levels=None,
                                      line_regex=_intent_regex(intent))
        streams = loki.query_range(query, start_ns, end_ns, limit=300, direction="backward")
        entries = log_parse.flatten(streams, limit=MAX_EVIDENCE_LINES)
        evidence_source = "live_loki"
    signatures = sig.aggregate_signatures(entries)[:8]
    category_rows: dict[str, dict[str, Any]] = {}
    for entry in entries:
        category = sig.categorize(entry["message"])
        row = category_rows.setdefault(category, {"category": category, "count": 0, "error": 0, "warn": 0})
        row["count"] += 1
        row["error"] += int(entry["severity"] in {"error", "fatal"})
        row["warn"] += int(entry["severity"] == "warn")
    categories = sorted(category_rows.values(), key=lambda row: row["count"], reverse=True)
    return {
        "intent": intent, "components": components,
        "query": query, "entries": entries,
        "signatures": signatures, "categories": categories,
        "evidence_source": evidence_source, "store": store,
        "start_ns": str(start_ns), "end_ns": str(end_ns),
    }


# --------------------------------------------------------------------------
# Anomaly detection — z-score on per-(level) count_over_time buckets.
# --------------------------------------------------------------------------
_STEP_SECONDS = {"1m": 60, "2m": 120, "5m": 300, "10m": 600, "30m": 1800, "1h": 3600}


def detect_anomalies(start_ns: int, end_ns: int, step: str = "5m",
                     z_threshold: float = 3.0) -> dict[str, Any]:
    step_s = _STEP_SECONDS.get(step, 300)
    # Step-aligned bucket grid so we can fill implicit zeros — Loki returns a
    # sparse matrix (empty buckets omitted), which would inflate the baseline
    # and hide spikes against quiet periods.
    g0 = (start_ns // loki.NS_PER_S // step_s) * step_s
    g1 = end_ns // loki.NS_PER_S
    grid = list(range(g0, g1 + 1, step_s)) or [g1]
    streams = loki.query_range(log_parse.build_selector(), start_ns, end_ns,
                               limit=5000, direction="backward")
    counts_by_level: dict[str, dict[int, int]] = {}
    for entry in log_parse.flatten(streams):
        bucket = (int(entry["ts_ns"]) // loki.NS_PER_S // step_s) * step_s
        seen = counts_by_level.setdefault(entry["level"], {})
        seen[bucket] = seen.get(bucket, 0) + 1
    out: list[dict[str, Any]] = []
    for level, seen in counts_by_level.items():
        counts = [seen.get(ts, 0) for ts in grid]
        if len(counts) < 4:
            continue
        mean = statistics.fmean(counts)
        stdev = statistics.pstdev(counts)
        if stdev == 0:
            continue
        for ts in grid:
            c = seen.get(ts, 0)
            z = (c - mean) / stdev
            # require a meaningful absolute count too, so a 1-line blip against a
            # near-zero baseline doesn't register as a high-z "anomaly".
            if z >= z_threshold and c > mean and c >= 5:
                out.append({
                    "level": level, "severity": log_parse.severity(level),
                    "ts": log_parse._iso(ts * loki.NS_PER_S),
                    "count": c, "baseline": round(mean, 1), "z": round(z, 1),
                    "ratio": round(c / mean, 1) if mean else None,
                })
    out.sort(key=lambda a: a["z"], reverse=True)
    return {"available": True, "source": "loki", "step": step,
            "count": len(out), "anomalies": out[:20]}


# --------------------------------------------------------------------------
# Summarize — configured provider if available, else heuristic.
# --------------------------------------------------------------------------
def _claude_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


_SYSTEM = (
    "You are a read-only PostgreSQL/Patroni SRE assistant for the HBZ Enterprise "
    "Console. Answer ONLY from the provided evidence (readiness signals and "
    "redacted log excerpts). Do an RCA-style answer: state the likely cause, cite "
    "specific log lines by timestamp and component as evidence, and suggest a safe "
    "next step. If the evidence is insufficient, say so. Never invent log lines, "
    "metrics, or actions. Keep it concise."
)


def _evidence_text(readiness: dict[str, Any], ctx: dict[str, Any]) -> str:
    lines = ["## Readiness"]
    for i in readiness.get("items", []):
        lines.append(f"- [{i['status']}] {i['name']}: {i['detail']}")
    lines.append(f"\n## Top error signatures (intent={ctx['intent']})")
    for s in ctx["signatures"]:
        lines.append(f"- x{s['count']} [{s['severity']}/{s['category']}] {s['pattern'][:120]}")
    lines.append("\n## Categories")
    for c in ctx["categories"][:8]:
        lines.append(f"- {c['category']}: {c['count']} (err {c['error']}, warn {c['warn']})")
    lines.append("\n## Recent log lines (redacted)")
    for e in ctx["entries"][:MAX_EVIDENCE_LINES]:
        lines.append(f"- {e['ts']} [{e['component']}/{e['level']}] {e['message'][:160]}")
    structured = ctx.get("patroni_restart")
    if structured:
        lines.append("\n## Authoritative Patroni restart/failover evidence")
        lines.append(f"- status: {structured.get('status')}")
        for item in structured.get("history", [])[-10:]:
            lines.append(f"- history: {item}")
        lines.append("\n## Kubernetes restart evidence")
        for pod in structured.get("pods", []):
            lines.append(f"- pod: {log_parse.redact(str(pod))}")
        for event in structured.get("events", [])[:20]:
            lines.append(f"- event: {log_parse.redact(str(event))}")
    return "\n".join(lines)


def _restart_evidence() -> dict[str, Any]:
    evidence: dict[str, Any] = {"history": [], "status": {}, "pods": [], "events": [], "errors": []}
    try:
        evidence["history"] = S.patroni_history()
    except Exception as exc:
        evidence["errors"].append(f"history unavailable: {type(exc).__name__}")
    try:
        status = S.patroni_status()
        evidence["status"] = {key: status.get(key) for key in
                              ("role", "state", "timeline", "postmaster_start_time")}
    except Exception as exc:
        evidence["errors"].append(f"status unavailable: {type(exc).__name__}")
    try:
        evidence["pods"] = [{
            "name": pod.get("name"), "role": pod.get("role"), "phase": pod.get("phase"),
            "start_time": pod.get("start_time"), "restarts": pod.get("restarts"),
            "containers": pod.get("containers", []),
        } for pod in S.pods(ttl=0)]
    except Exception as exc:
        evidence["errors"].append(f"pods unavailable: {type(exc).__name__}")
    try:
        evidence["events"] = S.kubernetes_events(limit=30)
    except Exception as exc:
        evidence["errors"].append(f"events unavailable: {type(exc).__name__}")
    return evidence


def _failover_timeline_answer(question: str) -> dict[str, Any] | None:
    q = (question or "").lower()
    explicit_failover = (("failover" in q or "failovr" in q)
                         and ("patroni" in q or "history" in q or "kubernetes" in q))
    explicit_correlation = ("patroni" in q and "history" in q and "kubernetes" in q)
    if not (explicit_failover or explicit_correlation):
        return None
    ev = _restart_evidence()
    history = ev.get("history") or []
    events = ev.get("events") or []
    pods = ev.get("pods") or []
    timeline = []
    for item in history[-3:]:
        timeline.append(f"Patroni history: {item}")
    for item in events[:3]:
        timestamp = item.get("timestamp") or item.get("lastTimestamp") or "time unavailable"
        timeline.append(f"Kubernetes {timestamp}: {item.get('reason') or 'event'} "
                        f"{item.get('message') or item.get('name') or ''}".strip())
    evidence_text = "; ".join(timeline) or "No timestamped failover events were returned."
    answer = (
        f"Evidence timeline: {evidence_text}. Current pod evidence covers {len(pods)} pod(s). "
        "These facts establish the promotion timeline but do not by themselves prove the initiating "
        "cause. Hypothesis: a pod or leader-lock disruption may have preceded promotion; missing "
        "Patroni decision logs and node events are required to confirm it. Safe next step: correlate "
        "the archived Patroni, Kubernetes, database, and Loki evidence for that exact interval."
    )
    return {"answer": answer, "model": "live-data (Patroni + Kubernetes evidence store)",
            "intent": "failover", "evidence": {"patroni_restart": ev}}


def _persist_evidence_turn(question: str, ctx: dict[str, Any], result: dict[str, Any],
                           started: float) -> bool:
    """Persist a redacted audit trail; answering remains available if audit fails."""
    if os.environ.get("AI_EVIDENCE_AUDIT_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return False
    from sqlalchemy import text
    from .db.session import SessionLocal
    session_id = uuid.uuid4()
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    entries = ctx.get("entries", [])[:MAX_EVIDENCE_LINES]
    try:
        with SessionLocal() as db:
            db.execute(text("INSERT INTO ai_assistant_sessions (id,cluster_id,namespace,environment,user_email,question,detected_intent,time_range,created_at,model_name,backend,status,duration_ms) VALUES (:id,:cluster,:namespace,:environment,:user_email,:question,:intent,:time_range,now(),:model,:backend,:status,:duration)"), {
                "id": session_id, "cluster": S.CLUSTER_NAME, "namespace": S.NS,
                "environment": "live", "user_email": None, "question": question[:4000],
                "intent": ctx.get("intent"),
                "time_range": f"{ctx.get('start_ns')}..{ctx.get('end_ns')}",
                "model": result.get("model"), "backend": result.get("provider"),
                "status": "completed", "duration": duration_ms,
            })
            for entry in entries:
                db.execute(text("INSERT INTO ai_evidence_items (session_id,source_type,component,timestamp,cluster_id,namespace,pod_name,container_name,evidence_text,query_used,severity,created_at) VALUES (:session_id,:source_type,:component,:timestamp,:cluster,:namespace,:pod,:container,:evidence,:query,:severity,now())"), {
                    "session_id": session_id, "component": entry.get("component"),
                    "source_type": entry.get("evidence_source") or ctx.get("evidence_source") or "loki",
                    "timestamp": entry.get("ts"), "cluster": S.CLUSTER_NAME, "namespace": S.NS,
                    "pod": entry.get("pod"), "container": entry.get("container"),
                    "evidence": entry.get("message", "")[:2000], "query": ctx.get("query", "")[:2000],
                    "severity": entry.get("severity"),
                })
            restart = ctx.get("patroni_restart") or {}
            if restart:
                db.execute(text("INSERT INTO ai_evidence_items (session_id,source_type,component,timestamp,cluster_id,namespace,pod_name,container_name,evidence_text,query_used,severity,created_at) VALUES (:session_id,'patroni','patroni',:timestamp,:cluster,:namespace,NULL,'database',:evidence,'GET /history + /patroni','info',now())"), {
                    "session_id": session_id,
                    "timestamp": str((restart.get("status") or {}).get("postmaster_start_time") or ""),
                    "cluster": S.CLUSTER_NAME, "namespace": S.NS,
                    "evidence": log_parse.redact(str({"status": restart.get("status"), "history": restart.get("history", [])[-10:]}))[:4000],
                })
                db.execute(text("INSERT INTO ai_evidence_items (session_id,source_type,component,timestamp,cluster_id,namespace,pod_name,container_name,evidence_text,query_used,severity,created_at) VALUES (:session_id,'kubernetes','kubernetes',:timestamp,:cluster,:namespace,NULL,NULL,:evidence,'GET pods + events','info',now())"), {
                    "session_id": session_id, "timestamp": str((restart.get("events") or [{}])[0].get("timestamp") or ""),
                    "cluster": S.CLUSTER_NAME, "namespace": S.NS,
                    "evidence": log_parse.redact(str({"pods": restart.get("pods", []), "events": restart.get("events", [])[:20]}))[:8000],
                })
            for call in ctx.get("tool_calls", []):
                db.execute(text("INSERT INTO ai_tool_calls (session_id,tool_name,input_json,output_summary_json,duration_ms,status,error_message,created_at) VALUES (:session_id,:tool,CAST(:input AS jsonb),CAST(:output AS jsonb),:duration,:status,:error,now())"), {
                    "session_id": session_id, "tool": call.get("tool_name", "read_only_tools"),
                    "input": json.dumps(call.get("input", {})),
                    "output": json.dumps(call.get("output_summary", {})),
                    "duration": int(call.get("duration_ms") or duration_ms),
                    "status": call.get("status", "completed"), "error": call.get("error"),
                })
            if ctx.get("intent") in {"failover", "errors"}:
                missing = (restart.get("errors", []) if restart else [])
                db.execute(text("INSERT INTO ai_incident_reports (session_id,root_cause,confidence,summary,missing_evidence,final_answer,created_at) VALUES (:session_id,:root_cause,:confidence,:summary,CAST(:missing AS jsonb),:answer,now()) ON CONFLICT (session_id) DO NOTHING"), {
                    "session_id": session_id, "root_cause": "evidence-grounded assistant analysis",
                    "confidence": "unknown", "summary": str(result.get("answer", ""))[:2000],
                    "missing": json.dumps(missing), "answer": str(result.get("answer", ""))[:8000],
                })
            db.commit()
        return True
    except Exception:
        return False


def summarize(question: str, readiness: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    evidence = _evidence_text(readiness, ctx)
    question_text = question or "Summarize current cluster log health."
    if _claude_available():
        try:
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=MODEL, max_tokens=1024,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                system=_SYSTEM,
                messages=[{"role": "user", "content":
                           f"Question: {question_text}\n\n{evidence}"}],
            )
            answer = "".join(b.text for b in msg.content if b.type == "text").strip()
            return {
                "answer": answer,
                "model": f"anthropic:{MODEL}",
                "grounded": True,
                "provider_attempted": True,
                "provider": "anthropic",
                "response_mode": "llm",
                "fallback_used": False,
                "fallback_reason_code": None,
                "provider_http_status": 200,
                "provider_latency_ms": None,
                "provider_request_id": None,
            }
        except Exception:  # pragma: no cover - fall back on any SDK/API error
            return {"answer": _heuristic(question, readiness, ctx),
                    "model": "heuristic", "grounded": True,
                    "provider_attempted": True,
                    "provider": "anthropic",
                    "response_mode": "heuristic_fallback",
                    "fallback_used": True,
                    "fallback_reason_code": "PROVIDER_ERROR",
                    "provider_http_status": None,
                    "provider_latency_ms": None,
                    "provider_request_id": None}
    # No Anthropic key — try the env-driven local/OpenAI-compatible provider
    # (e.g. AI_PROVIDER=local pointed at an in-cluster Ollama service). Bound the
    # call so a slow CPU model degrades to the heuristic below instead of hanging
    # the request past the route timeout (504); falls through to the heuristic if
    # no provider is configured or the call fails/times out.
    result = ai_provider.generate_rca(
        f"{_SYSTEM}\n\nQuestion: {question_text}\n\n{evidence}",
        timeout_s=_interactive_timeout_s(),
    )
    if result.available:
        return {
            "answer": result.content,
            "model": f"{result.provider}:{result.model}",
            "grounded": True,
            "provider_attempted": True,
            "provider": result.provider,
            "response_mode": "llm",
            "fallback_used": False,
            "fallback_reason_code": None,
            "provider_http_status": result.http_status,
            "provider_latency_ms": result.latency_ms,
            "provider_request_id": result.request_id,
        }
    fallback = _heuristic(question, readiness, ctx)
    if result.provider not in {"disabled", "", None}:
        fallback = (
            f"The configured AI provider did not return this answer; a safe "
            f"evidence-only fallback was used ({result.error_category or 'PROVIDER_ERROR'}). "
            f"{fallback}"
        )
    return {
        "answer": fallback,
        "model": "heuristic",
        "grounded": True,
        "provider_attempted": result.provider not in {"disabled", "", None},
        "provider": result.provider,
        "response_mode": "heuristic_fallback",
        "fallback_used": True,
        "fallback_reason_code": result.error_category or "PROVIDER_ERROR",
        "provider_http_status": result.http_status,
        "provider_latency_ms": result.latency_ms,
        "provider_request_id": result.request_id,
    }


def _heuristic(question: str, readiness: dict[str, Any], ctx: dict[str, Any]) -> str:
    bad = [i for i in readiness.get("items", []) if not i["ok"]]
    sigs = ctx["signatures"]
    parts: list[str] = []
    if bad:
        parts.append("Readiness attention: " + "; ".join(f"{i['name']} ({i['detail']})" for i in bad) + ".")
    else:
        parts.append(f"Readiness all-clear ({readiness['summary']['score']}/100).")
    if sigs:
        top = sigs[0]
        parts.append(
            f"Most frequent issue ({ctx['intent']}): {top['count']}× "
            f"[{top['severity']}/{top['category']}] \"{top['pattern'][:100]}\", "
            f"last seen {top['last_seen']}.")
        if len(sigs) > 1:
            parts.append("Other signatures: " +
                         "; ".join(f"{s['count']}× {s['category']}" for s in sigs[1:4]) + ".")
    else:
        parts.append("No error/warning signatures in the window.")
    parts.append("(Heuristic evidence-only fallback.)")
    return " ".join(parts)


def _interactive_timeout_s() -> float:
    """Wall-clock bound (seconds) for the interactive assistant's LLM call, so a
    slow local model degrades to the heuristic instead of hanging past the route
    timeout. Env-tunable via AI_ASSISTANT_TIMEOUT_S; distinct from the long
    AI_REQUEST_TIMEOUT_S used by the background agent."""
    try:
        return float(os.environ.get("AI_ASSISTANT_TIMEOUT_S", "60"))
    except (TypeError, ValueError):
        return 60.0


# Questions about *current* cluster state (leader identity, replica sync) are
# answered directly from live Patroni/pg_stat_replication — instant and exact —
# instead of being routed to the slow local LLM. RCA-style "why did X happen"
# questions still fall through to the log+LLM path.
#
# Matched by whole-word TOKENS (via assistant_tools), never substring — so
# "synchronous_commit" no longer trips "sync" and "reasonable" no longer trips
# "reason" (the two false-positives found in the 495-question eval). Vocabulary
# widened to cover the cluster-state phrasings that used to miss (streaming,
# node, quorum, timeline, maintenance, healthy, version, ...).
_LIVE_STATE_TOPICS = {
    "leader", "primary", "replica", "replicas", "replication", "standby", "standbys",
    "sync", "synchronous", "failover", "switchover", "patroni", "lag", "streaming",
    "member", "members", "node", "nodes", "quorum", "timeline", "maintenance",
    "paused", "healthy", "health", "promoted", "promote", "topology", "dcs",
    "walsender", "walsenders", "senders", "reinitialization", "reinitializations",
}
_LIVE_CLUSTER_CACHE: tuple[float, dict[str, Any]] | None = None
_LIVE_CLUSTER_LOCK = threading.Lock()
_LIVE_CLUSTER_TTL_S = 5.0


def _wants_live_state(question: str) -> bool:
    from . import assistant_tools as T
    if T.is_rca(question):
        return False
    return bool(T.tokens(question) & _LIVE_STATE_TOPICS)


def _live_cluster_answer(question: str) -> dict[str, Any] | None:
    global _LIVE_CLUSTER_CACHE
    if not _wants_live_state(question):
        return None
    now = time.monotonic()
    if _LIVE_CLUSTER_CACHE and now - _LIVE_CLUSTER_CACHE[0] <= _LIVE_CLUSTER_TTL_S:
        return _LIVE_CLUSTER_CACHE[1]
    with _LIVE_CLUSTER_LOCK:
        now = time.monotonic()
        if _LIVE_CLUSTER_CACHE and now - _LIVE_CLUSTER_CACHE[0] <= _LIVE_CLUSTER_TTL_S:
            return _LIVE_CLUSTER_CACHE[1]
        result = _live_cluster_answer_uncached(question)
        if result is not None:
            _LIVE_CLUSTER_CACHE = (time.monotonic(), result)
        return result


def _live_cluster_answer_uncached(question: str) -> dict[str, Any] | None:
    """Deterministic answer for current-state questions, straight from live
    Patroni + pg_stat_replication. Returns None to fall through to the LLM path."""
    try:
        from . import pg_replication
        topo = pg_replication.build_topology()
        sync = pg_replication.build_sync()
    except Exception:
        return None
    summary = topo.get("summary", {})
    leader = summary.get("leader") or "unknown"
    members = topo.get("members", [])
    member_names = {m.get("name") for m in members}
    repl = topo.get("replication", [])
    # pg_stat_replication mixes PHYSICAL HA standbys with LOGICAL replication
    # walsenders (one row per active subscription). The old answer dumped every
    # row as "replay lag", so logical subscribers showed as ~31GB-lagged replicas
    # (a pg_wal_lsn_diff-vs-current-WAL artifact) and buried the real HA answer.
    # Split them by application_name: physical standbys match a Patroni member.
    phys = [r for r in repl if r.get("application_name") in member_names]
    logical = [r for r in repl if r.get("application_name") not in member_names]
    parts = [f"Current Patroni leader: {leader}."]
    if members:
        parts.append(f"{len(members)} member(s): " + ", ".join(
            f"{m.get('name')} [{m.get('role')}/{m.get('state')}]" for m in members) + ".")
    if phys:
        parts.append(f"{len(phys)} physical standby(s) streaming "
                     f"({sync['summary'].get('sync_mode', 'asynchronous')}, "
                     f"{sync['summary'].get('sync_standbys', 0)} sync standby(s)).")
        laggy = [r for r in phys if r.get("replay_lag_bytes", 0) > 0]
        parts.append(("Physical replay lag: " + ", ".join(
            f"{r['application_name']} {r['replay_lag_bytes']}B ({r['sync_state']})" for r in laggy) + ".")
            if laggy else "All physical standbys report 0 bytes replay lag — HA replicas in sync.")
    else:
        parts.append("No physical standby currently streaming.")
    if logical:
        parts.append(f"({len(logical)} logical replication walsender(s) also connected — "
                     f"these are pub/sub subscriptions, not HA replicas; ask about logical "
                     f"replication for their slot lag.)")
    if not summary.get("patroni_ok", True):
        parts.append("Warning: Patroni DCS query did not fully succeed; data may be partial.")
    return {"answer": " ".join(parts),
            "model": "live-data (Patroni + pg_stat_replication)",
            "evidence": {"topology": topo, "sync": sync}}


def ask(question: str, start_ns: int, end_ns: int) -> dict[str, Any]:
    """Full assistant turn: gather log context, summarize, return with evidence."""
    started = time.monotonic()
    # Typed multi-intent pipeline owns composed physical-replication/WAL
    # questions and the fixed safety contract. Other intents continue through
    # the compatibility paths below during the phased rollout.
    try:
        from .assistant import try_answer
        planned = try_answer(question)
    except Exception:
        planned = None
    if planned is not None:
        planned.update({
            "audit_logged": True,
            "evidence_count": len(planned.get("evidence_items") or []),
            "provider_attempted": False, "provider": "read_only_tools",
            "response_mode": "deterministic_composed", "fallback_used": False,
            "fallback_reason_code": None, "provider_http_status": None,
            "provider_latency_ms": None, "provider_request_id": None,
        })
        jobs._audit("assistant-ask", "dba", dry_run=False, executed=True,
                    detail=f"intents={','.join(planned.get('intents') or [])} q={question[:80]}")
        audit_ctx = {
            "intent": planned.get("intent", "composed"), "start_ns": str(start_ns),
            "end_ns": str(end_ns), "entries": [],
            "tool_calls": [{"tool_name": s.get("source"), "input": {"intent": s.get("intent")},
                            "output_summary": {"status": s.get("status")}}
                           for s in planned.get("sections", [])],
        }
        planned["evidence_audit_persisted"] = _persist_evidence_turn(
            question, audit_ctx, planned, started)
        return planned
    rca_timeline = _failover_timeline_answer(question)
    if rca_timeline is not None:
        jobs._audit("assistant-ask", "dba", dry_run=False, executed=True,
                    detail=f"intent=failover q={question[:80]}")
        response = {
            "available": True, "question": question, **rca_timeline,
            "evidence_count": len((rca_timeline["evidence"]["patroni_restart"].get("history") or [])),
            "audit_logged": True, "provider_attempted": False,
            "provider": "read_only_tools", "response_mode": "deterministic",
            "fallback_used": False, "fallback_reason_code": None,
            "provider_http_status": None, "provider_latency_ms": None,
            "provider_request_id": None,
        }
        return response
    # Fast-path 1: deterministic tools (config/metrics/sessions/locks/vacuum/
    # storage/slow-queries/logical-repl/roles) answer factual questions straight
    # from live SQL/Prometheus — instant and exact, never touching the slow LLM.
    # RCA/"why" questions and anything unmatched return None and fall through.
    try:
        from . import assistant_tools
        tooled = assistant_tools.route(question)
    except Exception:
        tooled = None
    if tooled is not None:
        jobs._audit("assistant-ask", "dba", dry_run=False, executed=True,
                    detail=f"intent={tooled.get('intent','tool')} q={question[:80]}")
        response = {
            "available": True, "question": question,
            "answer": tooled["answer"], "model": tooled["model"],
            "intent": tooled.get("intent", "tool"),
            "evidence_count": len(tooled.get("evidence", {})),
            "evidence": tooled.get("evidence", {}), "audit_logged": True,
            "provider_attempted": False, "provider": "read_only_tools",
            "response_mode": "deterministic", "fallback_used": False,
            "fallback_reason_code": None, "provider_http_status": None,
            "provider_latency_ms": None, "provider_request_id": None,
        }
        audit_ctx = {
            "intent": tooled.get("intent", "tool"), "start_ns": str(start_ns), "end_ns": str(end_ns),
            "entries": [], "tool_calls": [{"tool_name": tooled.get("intent", "read_only_tools"),
                "input": {"question": question[:500]},
                "output_summary": {"evidence_keys": sorted((tooled.get("evidence") or {}).keys())}}],
        }
        response["evidence_audit_persisted"] = _persist_evidence_turn(question, audit_ctx, response, started)
        return response
    # Fast-path 2: current-state questions are answered from live cluster data
    # (sub-second, exact) without touching Loki or the LLM.
    live = _live_cluster_answer(question)
    if live is not None:
        jobs._audit("assistant-ask", "dba", dry_run=False, executed=True,
                    detail=f"intent=cluster-state q={question[:80]}")
        response = {
            "available": True, "question": question,
            "answer": live["answer"], "model": live["model"], "intent": "cluster-state",
            "evidence_count": len(live["evidence"]["topology"].get("members", [])),
            "evidence": live["evidence"], "audit_logged": True,
            "provider_attempted": False, "provider": "read_only_tools",
            "response_mode": "deterministic", "fallback_used": False,
            "fallback_reason_code": None, "provider_http_status": None,
            "provider_latency_ms": None, "provider_request_id": None,
        }
        audit_ctx = {
            "intent": "cluster-state", "start_ns": str(start_ns), "end_ns": str(end_ns),
            "entries": [], "tool_calls": [{"tool_name": "patroni_and_pg_stat_replication",
                "input": {"question": question[:500]},
                "output_summary": {"member_count": response["evidence_count"]}}],
        }
        response["evidence_audit_persisted"] = _persist_evidence_turn(question, audit_ctx, response, started)
        return response
    ctx = gather_context(question, start_ns, end_ns)
    if ctx["intent"] == "failover":
        ctx["patroni_restart"] = _restart_evidence()
    from . import pg_ops
    readiness = pg_ops.readiness()
    result = summarize(question, readiness, ctx)
    jobs._audit("assistant-ask", "dba", dry_run=False, executed=True,
                detail=f"intent={ctx['intent']} q={question[:80]}")
    response = {
        "available": True, "question": question,
        "answer": result["answer"], "model": result["model"],
        "intent": ctx["intent"],
        "evidence_count": len(ctx["entries"][:15]) + len(readiness["items"]),
        "evidence": {
            "readiness": readiness["items"],
            "signatures": ctx["signatures"],
            "log_lines": ctx["entries"][:15],
            "patroni_restart": ctx.get("patroni_restart"),
            "source": ctx.get("evidence_source"),
            "store_freshness": {key: ctx.get("store", {}).get(key)
                                for key in ("fresh", "lag_seconds", "last_indexed_at", "status")},
        },
        "audit_logged": True,
        "provider_attempted": result.get("provider_attempted", False),
        "provider": result.get("provider"),
        "response_mode": result.get("response_mode"),
        "fallback_used": result.get("fallback_used", False),
        "fallback_reason_code": result.get("fallback_reason_code"),
        "provider_http_status": result.get("provider_http_status"),
        "provider_latency_ms": result.get("provider_latency_ms"),
        "provider_request_id": result.get("provider_request_id"),
    }
    response["evidence_audit_persisted"] = _persist_evidence_turn(question, ctx, result, started)
    return response
