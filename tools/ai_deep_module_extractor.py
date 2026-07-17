#!/usr/bin/env python3
"""Deep post-deploy AI/ML/RAG/LLM extractor for object-monitor.

The existing scripts each check one slice of the system. This one is intended
for post-deploy evidence collection after the LLM model is loaded:

* discovers the running object-monitor pod and queries endpoints from inside it
* checks AI Platform, AI Agent, ML, forecast, RAG, incident, assistant, logs,
  log analytics, scheduler, and frontend panel wiring
* checks the local Ollama/OpenAI-compatible LLM service and model list
* optionally runs active checks that can create audit/incident/agent/ML rows
* writes uploadable JSON and plain-text reports

Read-only by default. Use --run-active-checks, --run-agent, or --run-ml-jobs
only when you intentionally want to validate generation/write paths.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_NAMESPACE = "monitoring"
DEFAULT_DEPLOYMENT = "object-monitor"
DEFAULT_PORT = 8080
DEFAULT_LLM_SERVICE = "http://object-monitor-llm.monitoring.svc:11434"

AI_ENV_NAMES = {
    "AI_PROVIDER",
    "AI_BASE_URL",
    "AI_MODEL",
    "AI_LOCAL_API_MODE",
    "AI_AGENT_LLM_ENABLED",
    "AI_REQUEST_TIMEOUT_S",
    "AI_AGENT_SCHEDULER_ENABLED",
    "AI_AGENT_RUN_ON_START",
    "AI_AGENT_INTERVAL_MINUTES",
    "AI_AGENT_LOOKBACK_MINUTES",
    "AI_SCHEDULER_INCIDENTS_ENABLED",
    "ANTHROPIC_MODEL",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_ENDPOINT",
    "PGC_CLUSTER_ID",
    "LOCAL_CLUSTER_ID",
    "PGC_CLUSTER",
    "CLUSTER_NAME",
    "PGC_NAMESPACE",
    "K8S_NAMESPACE",
    "NAMESPACE",
}

SECRET_HINTS = ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "KEY")
SENSITIVE_REPORT_KEY = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|authorization|bearer)"
)

SECRET_VALUE_REDACTIONS = [
    (re.compile(r"(?i)(password\s*[:=]\s*)[^ \t\n\r'\";]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(PGPASSWORD=)[^ \t\n\r]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)((?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?key|private[_-]?key|token)\s*[:=]\s*)[^ \t\n\r'\";]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+/\-=]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/\-=]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(postgres(?:ql)?://[^:\s]+:)[^@\s]+(@)"), r"\1***REDACTED***\2"),
]

SEED_MARKERS = [
    "REC-2041",
    "Autovacuum starvation",
    "local-llama-70b",
    "representative sample",
    "representative defaults",
    "fall back to sample",
    "fallback to sample",
    "falls back to sample",
    "shipped uat mock",
    "mock time-series",
    "mock data",
    "sample data",
    "seeded entry",
    "seededrand",
    "function seeded",
    "var r = seeded",
    "const seeded",
    "dummy data",
    "fake data",
    "local mock",
    "var q = [",
    "var members = [",
    "const top_sql = {",
    "const sessions = {",
]

SUSPECT_SOURCE_SUBSTRINGS = (
    "seed",
    "mock",
    "sample",
    "demo",
    "static",
    "hardcoded",
    "placeholder",
    "local-empty",
    "fallback",
    "fixture",
    "stub",
    "dummy",
    "test data",
)

PANEL_SCRIPT_FILES = [
    "index.html",
    "data.jsx",
    "live-identity.js",
    "dist/data.js",
    "dist/overview_charts.js",
    "dist/appmon_charts.js",
    "dist/module_charts.js",
    "dist/module_charts2.js",
    "dist/module_charts3.js",
    "dist/module_charts4.js",
    "dist/memory_sga.js",
    "dist/sql_insight.js",
    "dist/sql_insight2.js",
    "dist/health_grid.js",
    "dist/drilldowns.js",
    "dist/ai_platform.js",
    "dist/ai_platform2.js",
    "dist/ai_ops.js",
    "dist/ai_agent.js",
    "ai-ui.js",
]

REQUIRED_AI_LIVE_REFS = [
    "/api/v1/ai/recommendations",
    "/api/v1/ai/audit",
    "/api/v1/ai/rag/kb",
    "/api/v1/ai/model-gateway/status",
    "/api/ai-agent/status",
]

AI_EVIDENCE_ENDPOINT_KEYS = {
    "ai_overview",
    "ai_model_gateway",
    "ai_agents",
    "ai_recommendations",
    "ai_audit",
    "rag_kb",
    "ai_agent_status",
    "ai_agent_runs",
    "ai_agent_recs",
    "assistant_status",
    "assistant_anomalies",
    "ml_models",
    "ml_anomalies",
    "ml_forecasts",
    "ai_incidents",
}

LIVE_STATES = {"LIVE_DATA", "LIVE_EMPTY"}
LLM_WORKING_STATES = {"LLM_ACTIVE_RESPONSE", "LOCAL_LLM_MODEL_VISIBLE", "LLM_CONNECTED"}
LLM_GENERATION_STATES = {"LLM_ACTIVE_RESPONSE"}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_VALUE_REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_for_report(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_for_report(item) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if SENSITIVE_REPORT_KEY.search(str(key)):
                if isinstance(item, str):
                    out[key] = "***REDACTED***"
                elif item is None:
                    out[key] = None
                else:
                    out[key] = {"present": True, "redacted": True}
            else:
                out[key] = redact_for_report(item)
        return out
    return value


def run_cmd(args: list[str], input_text: str | None = None, timeout: int = 90) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "elapsed_ms": int((time.time() - started) * 1000),
            "cmd": args,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"timeout after {timeout}s",
            "elapsed_ms": int((time.time() - started) * 1000),
            "cmd": args,
        }
    except Exception as exc:  # pragma: no cover - defensive for bastion envs
        return {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
            "elapsed_ms": int((time.time() - started) * 1000),
            "cmd": args,
        }


def oc_json(namespace: str, args: list[str], timeout: int = 90) -> dict[str, Any] | None:
    result = run_cmd(["oc", "-n", namespace, *args], timeout=timeout)
    if not result["ok"]:
        return None
    try:
        return json.loads(result["stdout"])
    except Exception:
        return None


def safe_env_value(name: str, env_item: dict[str, Any]) -> dict[str, Any]:
    if "value" in env_item:
        value = env_item.get("value") or ""
        if any(hint in name.upper() for hint in SECRET_HINTS):
            return {"source": "literal", "present": bool(value), "value": "***REDACTED***", "length": len(value)}
        return {"source": "literal", "present": bool(value), "value": value}

    ref = env_item.get("valueFrom") or {}
    if "secretKeyRef" in ref:
        secret = ref["secretKeyRef"]
        return {
            "source": "secretKeyRef",
            "present": True,
            "secret": secret.get("name"),
            "key": secret.get("key"),
        }
    if "configMapKeyRef" in ref:
        cm = ref["configMapKeyRef"]
        return {
            "source": "configMapKeyRef",
            "present": True,
            "configMap": cm.get("name"),
            "key": cm.get("key"),
        }
    if "fieldRef" in ref:
        return {"source": "fieldRef", "present": True, "fieldPath": ref["fieldRef"].get("fieldPath")}
    return {"source": "unknown", "present": True}


def deployment_env_summary(deploy_json: dict[str, Any] | None) -> dict[str, Any]:
    if not deploy_json:
        return {"available": False, "env": {}, "envFrom": [], "containers": []}
    pod_spec = deploy_json.get("spec", {}).get("template", {}).get("spec", {})
    containers = pod_spec.get("containers") or []
    out: dict[str, Any] = {"available": True, "env": {}, "envFrom": [], "containers": []}
    for container in containers:
        cname = container.get("name")
        out["containers"].append(
            {
                "name": cname,
                "image": container.get("image"),
                "ports": container.get("ports") or [],
            }
        )
        for env_item in container.get("env") or []:
            name = env_item.get("name")
            if not name:
                continue
            if name in AI_ENV_NAMES or name.startswith(("AI_", "ANTHROPIC_", "AZURE_OPENAI_", "PGC_", "LOCAL_CLUSTER")):
                out["env"][name] = safe_env_value(name, env_item)
        for env_from in container.get("envFrom") or []:
            out["envFrom"].append({"container": cname, **env_from})
    return out


def value_from_env_summary(env_summary: dict[str, Any], names: list[str]) -> str | None:
    env = env_summary.get("env") or {}
    for name in names:
        item = env.get(name)
        if item and item.get("source") == "literal" and item.get("value"):
            return str(item["value"])
    return None


def selector_from_deployment(deploy_json: dict[str, Any] | None) -> str:
    if not deploy_json:
        return ""
    labels = deploy_json.get("spec", {}).get("selector", {}).get("matchLabels") or {}
    return ",".join(f"{key}={value}" for key, value in sorted(labels.items()))


def find_running_pod(namespace: str, deployment: str, deploy_json: dict[str, Any] | None) -> dict[str, Any]:
    selector = selector_from_deployment(deploy_json)
    pods_json = None
    if selector:
        pods_json = oc_json(
            namespace,
            ["get", "pods", "-l", selector, "--field-selector=status.phase=Running", "-o", "json"],
        )
    if not pods_json:
        pods_json = oc_json(namespace, ["get", "pods", "--field-selector=status.phase=Running", "-o", "json"])
    candidates = (pods_json or {}).get("items") or []
    filtered = []
    for pod in candidates:
        name = pod.get("metadata", {}).get("name") or ""
        if name.startswith(f"{deployment}-") and "-build" not in name:
            filtered.append(pod)
    if not filtered and selector:
        filtered = candidates
    if not filtered:
        return {"available": False, "pod": None, "selector": selector}
    pod = sorted(filtered, key=lambda p: p.get("metadata", {}).get("creationTimestamp") or "", reverse=True)[0]
    return {
        "available": True,
        "pod": pod.get("metadata", {}).get("name"),
        "selector": selector,
        "phase": pod.get("status", {}).get("phase"),
        "pod_ip": pod.get("status", {}).get("podIP"),
        "node": pod.get("spec", {}).get("nodeName"),
    }


def find_llm_workloads(namespace: str) -> dict[str, Any]:
    out: dict[str, Any] = {"deployments": [], "pods": [], "services": []}
    deploys = oc_json(namespace, ["get", "deployments", "-o", "json"]) or {}
    for item in deploys.get("items") or []:
        name = item.get("metadata", {}).get("name") or ""
        if "llm" in name or "ollama" in name:
            out["deployments"].append(
                {
                    "name": name,
                    "replicas": item.get("status", {}).get("replicas", 0),
                    "readyReplicas": item.get("status", {}).get("readyReplicas", 0),
                    "image": [
                        c.get("image")
                        for c in item.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
                    ],
                }
            )
    pods = oc_json(namespace, ["get", "pods", "--field-selector=status.phase=Running", "-o", "json"]) or {}
    for item in pods.get("items") or []:
        name = item.get("metadata", {}).get("name") or ""
        if "llm" in name or "ollama" in name:
            out["pods"].append(
                {
                    "name": name,
                    "phase": item.get("status", {}).get("phase"),
                    "podIP": item.get("status", {}).get("podIP"),
                    "node": item.get("spec", {}).get("nodeName"),
                    "containers": [c.get("name") for c in item.get("spec", {}).get("containers", [])],
                }
            )
    services = oc_json(namespace, ["get", "services", "-o", "json"]) or {}
    for item in services.get("items") or []:
        name = item.get("metadata", {}).get("name") or ""
        if "llm" in name or "ollama" in name:
            out["services"].append(
                {
                    "name": name,
                    "clusterIP": item.get("spec", {}).get("clusterIP"),
                    "ports": item.get("spec", {}).get("ports") or [],
                }
            )
    return out


def query(params: dict[str, Any]) -> str:
    clean = {key: value for key, value in params.items() if value is not None}
    return urllib.parse.urlencode(clean, doseq=True)


def ep(key: str, panel: str, method: str, path: str, kind: str = "data",
       critical: bool = True, payload: dict[str, Any] | None = None,
       mutating: bool = False) -> dict[str, Any]:
    return {
        "key": key,
        "panel": panel,
        "method": method.upper(),
        "path": path,
        "kind": kind,
        "critical": critical,
        "payload": payload,
        "mutating": mutating,
    }


def build_base_endpoints(cluster_id: str) -> list[dict[str, Any]]:
    cid = urllib.parse.quote(cluster_id, safe="")
    return [
        ep("readiness", "Core readiness", "GET", "/api/v1/readiness", "status"),
        ep("tenants", "Cluster selector", "GET", "/api/v1/tenants", "list", critical=False),
        ep("ui_overview", "Overview panel", "GET", f"/api/v1/ui/overview/{cid}?{query({'range': '1h'})}", "status"),
        ep("ui_cluster", "Cluster panel", "GET", f"/api/v1/ui/cluster/{cid}", "status"),
        ep("ai_overview", "AI Platform overview", "GET", f"/api/v1/ai/overview?{query({'cluster_id': cluster_id})}", "status"),
        ep("ai_model_gateway", "AI Platform model gateway", "GET", "/api/v1/ai/model-gateway/status", "status"),
        ep("ai_agents", "AI Platform agent runs", "GET", "/api/v1/ai/agents?limit=10", "list"),
        ep("ai_recommendations", "AI Platform recommendations", "GET", f"/api/v1/ai/recommendations?{query({'cluster_id': cluster_id, 'limit': 20})}", "list"),
        ep("ai_audit", "AI Platform audit", "GET", f"/api/v1/ai/audit?{query({'cluster_id': cluster_id, 'limit': 20})}", "list"),
        ep("rag_kb", "RAG knowledge base", "GET", "/api/v1/ai/rag/kb?limit=10", "list"),
        ep("ai_agent_status", "AI Agent status", "GET", "/api/ai-agent/status", "status"),
        ep("ai_agent_runs", "AI Agent run history", "GET", "/api/ai-agent/runs?limit=10", "list"),
        ep("ai_agent_recs", "AI Agent recommendations", "GET", "/api/ai-agent/recommendations?limit=20", "list"),
        ep("assistant_status", "LLM assistant status", "GET", "/api/v1/assistant/status", "status"),
        ep("assistant_anomalies", "LLM assistant anomalies", "GET", "/api/v1/assistant/anomalies?range_hours=6&step=5m", "data", critical=False),
        ep("ml_models", "ML models", "GET", "/api/v1/ml/models", "list"),
        ep("ml_anomalies", "ML anomalies", "GET", "/api/v1/ml/anomalies?limit=20", "list"),
        ep("ml_forecasts", "ML forecasts", "GET", "/api/v1/ml/forecasts?limit=20", "list"),
        ep("ai_incidents", "AI incidents", "GET", "/api/v1/ai/incidents?limit=20", "list"),
        ep("scheduler_status", "Scheduler status", "GET", "/api/v1/scheduler/status", "status", critical=False),
        ep("alert_notifications", "AI alert notifications", "GET", "/api/v1/alerts/notifications?limit=20", "list", critical=False),
        ep("logs_diag", "Logs panel diagnostics", "GET", f"/api/v1/clusters/{cid}/logs/diag?window_h=2", "status"),
        ep("logs_labels", "Logs panel filters", "GET", f"/api/v1/clusters/{cid}/logs/labels?range=1h", "list"),
        ep("logs_histogram", "Logs panel histogram", "GET", f"/api/v1/clusters/{cid}/logs/histogram?range=1h&step=5m", "data", critical=False),
        ep("logs_search", "Logs panel search", "GET", f"/api/v1/clusters/{cid}/logs/search?range=1h&limit=5", "list"),
        ep("log_analytics_summary", "Log Analytics summary", "GET", f"/api/v1/clusters/{cid}/log-analytics/summary?range=1h&step=5m", "status"),
        ep("log_analytics_signatures", "Log Analytics signatures", "GET", f"/api/v1/clusters/{cid}/log-analytics/signatures?range=1h&limit=10", "list"),
        ep("log_analytics_categories", "Log Analytics categories", "GET", f"/api/v1/clusters/{cid}/log-analytics/categories?range=1h", "list", critical=False),
        ep("log_analytics_findings", "Log Analytics findings", "GET", f"/api/v1/clusters/{cid}/log-analytics/findings?range=1h", "list"),
    ]


def build_active_endpoints(args: argparse.Namespace, cluster_name: str) -> list[dict[str, Any]]:
    endpoints = []
    if args.run_active_checks:
        endpoints.append(
            ep(
                "assistant_ask_active",
                "LLM assistant active RCA",
                "POST",
                "/api/v1/assistant/ask",
                "active",
                payload={"question": args.ask_question, "range_hours": args.range_hours},
                mutating=True,
            )
        )
        endpoints.append(
            ep(
                "incident_evaluate_active",
                "AI incident generation",
                "POST",
                "/api/v1/ai/incidents/evaluate",
                "active",
                payload={},
                mutating=True,
            )
        )
    if args.run_agent:
        endpoints.append(
            ep(
                "ai_agent_run_active",
                "AI Agent recommendation generation",
                "POST",
                "/api/v1/ai/agents/run-now",
                "active",
                payload={
                    "category": "ALL",
                    "lookback_minutes": args.lookback_minutes,
                    "cluster_name": cluster_name,
                    "triggered_by": "ai_deep_module_extractor",
                },
                mutating=True,
            )
        )
    if args.run_ml_jobs:
        encoded = urllib.parse.quote(cluster_name, safe="")
        endpoints.append(ep("ml_score_active", "ML scoring job", "POST", f"/api/v1/ml/score/{encoded}", "active", payload={}, mutating=True))
        endpoints.append(ep("ml_forecast_active", "ML forecast job", "POST", f"/api/v1/ml/forecast/{encoded}", "active", payload={}, mutating=True))
    return endpoints


def build_llm_service_endpoints(model: str, prompt: str, run_generation: bool) -> list[dict[str, Any]]:
    endpoints = [
        ep("llm_openai_models", "Local LLM OpenAI model list", "GET", "/v1/models", "list", critical=False),
        ep("llm_ollama_tags", "Local LLM Ollama tags", "GET", "/api/tags", "list", critical=False),
    ]
    if run_generation:
        endpoints.append(
            ep(
                "llm_openai_chat_active",
                "Local LLM OpenAI chat generation",
                "POST",
                "/v1/chat/completions",
                "active",
                critical=False,
                payload={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Reply with one short sentence."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": 32,
                },
                mutating=False,
            )
        )
        endpoints.append(
            ep(
                "llm_ollama_generate_active",
                "Local LLM Ollama generation",
                "POST",
                "/api/generate",
                "active",
                critical=False,
                payload={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0, "num_predict": 32},
                },
                mutating=False,
            )
        )
    return endpoints


REMOTE_PROBE_BODY = r'''
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

def one(endpoint):
    base_url = REQUEST["base_url"].rstrip("/")
    timeout = REQUEST["timeout"]
    max_body = REQUEST["max_body_bytes"]
    url = base_url + endpoint["path"]
    data = None
    headers = {"accept": "application/json"}
    if endpoint.get("payload") is not None and endpoint["method"] != "GET":
        data = json.dumps(endpoint.get("payload") or {}).encode("utf-8")
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=endpoint["method"])
    started = time.time()
    result = {k: endpoint.get(k) for k in ("key", "panel", "method", "path", "kind", "critical", "mutating")}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_body + 1)
            result.update({
                "ok": 200 <= resp.status < 400,
                "status": resp.status,
                "elapsed_ms": int((time.time() - started) * 1000),
                "content_type": resp.headers.get("content-type"),
                "truncated": len(raw) > max_body,
            })
    except urllib.error.HTTPError as exc:
        raw = exc.read(max_body + 1)
        result.update({
            "ok": False,
            "status": exc.code,
            "elapsed_ms": int((time.time() - started) * 1000),
            "content_type": exc.headers.get("content-type") if exc.headers else None,
            "error": str(exc),
            "truncated": len(raw) > max_body,
        })
    except Exception as exc:
        result.update({
            "ok": False,
            "status": None,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "error_type": type(exc).__name__,
        })
        return result

    if len(raw) > max_body:
        raw = raw[:max_body]
    text = raw.decode("utf-8", errors="replace")
    result["body_bytes"] = len(raw)
    try:
        result["json"] = json.loads(text) if text else None
    except Exception:
        result["text_preview"] = text[:4000]
    return result

print(json.dumps({"results": [one(ep) for ep in REQUEST["endpoints"]]}, default=str))
'''


def bulk_probe_via_pod(namespace: str, pod: str, base_url: str, endpoints: list[dict[str, Any]],
                       timeout: int, max_body_bytes: int) -> list[dict[str, Any]]:
    if not endpoints:
        return []
    request = {
        "base_url": base_url,
        "timeout": timeout,
        "max_body_bytes": max_body_bytes,
        "endpoints": endpoints,
    }
    code = "REQUEST = " + repr(request) + "\n" + REMOTE_PROBE_BODY
    result = run_cmd(["oc", "-n", namespace, "exec", "-i", pod, "--", "python3", "-"], input_text=code, timeout=max(90, timeout * len(endpoints)))
    if not result["ok"]:
        return [
            {
                **endpoint,
                "ok": False,
                "status": None,
                "state": "ERROR",
                "error": "oc exec probe failed",
                "stderr": result["stderr"][-4000:],
            }
            for endpoint in endpoints
        ]
    try:
        return json.loads(result["stdout"]).get("results") or []
    except Exception as exc:
        return [
            {
                **endpoint,
                "ok": False,
                "status": None,
                "state": "ERROR",
                "error": f"could not parse remote probe JSON: {exc}",
                "stdout": result["stdout"][-4000:],
                "stderr": result["stderr"][-4000:],
            }
            for endpoint in endpoints
        ]


def direct_request(base_url: str, endpoint: dict[str, Any], timeout: int,
                   max_body_bytes: int, insecure: bool) -> dict[str, Any]:
    url = base_url.rstrip("/") + endpoint["path"]
    data = None
    headers = {"accept": "application/json"}
    if endpoint.get("payload") is not None and endpoint["method"] != "GET":
        data = json.dumps(endpoint.get("payload") or {}).encode("utf-8")
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=endpoint["method"])
    context = ssl._create_unverified_context() if insecure else None
    started = time.time()
    result = {k: endpoint.get(k) for k in ("key", "panel", "method", "path", "kind", "critical", "mutating")}
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            raw = resp.read(max_body_bytes + 1)
            result.update(
                {
                    "ok": 200 <= resp.status < 400,
                    "status": resp.status,
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "content_type": resp.headers.get("content-type"),
                    "truncated": len(raw) > max_body_bytes,
                }
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read(max_body_bytes + 1)
        result.update(
            {
                "ok": False,
                "status": exc.code,
                "elapsed_ms": int((time.time() - started) * 1000),
                "content_type": exc.headers.get("content-type") if exc.headers else None,
                "error": str(exc),
                "truncated": len(raw) > max_body_bytes,
            }
        )
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "status": None,
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        )
        return result

    if len(raw) > max_body_bytes:
        raw = raw[:max_body_bytes]
    text = raw.decode("utf-8", errors="replace")
    result["body_bytes"] = len(raw)
    try:
        result["json"] = json.loads(text) if text else None
    except Exception:
        result["text_preview"] = text[:4000]
    return result


def bulk_probe_direct(base_url: str, endpoints: list[dict[str, Any]], timeout: int,
                      max_body_bytes: int, insecure: bool) -> list[dict[str, Any]]:
    return [direct_request(base_url, endpoint, timeout, max_body_bytes, insecure) for endpoint in endpoints]


def all_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, list):
        for item in value:
            found.extend(all_strings(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(all_strings(item))
    return found


def marker_hits(value: Any) -> list[str]:
    text = "\n".join(all_strings(value)).lower()
    return [marker for marker in SEED_MARKERS if marker.lower() in text]


def source_suspect(value: Any) -> list[str]:
    hits = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() == "source" and isinstance(item, str):
                low = item.lower()
                for word in SUSPECT_SOURCE_SUBSTRINGS:
                    if word in low:
                        hits.append(item)
            else:
                hits.extend(source_suspect(item))
    elif isinstance(value, list):
        for item in value:
            hits.extend(source_suspect(item))
    return sorted(set(hits))


def count_lists(value: Any, prefix: str = "") -> dict[str, int]:
    counts: dict[str, int] = {}
    if isinstance(value, list):
        counts[prefix or "$"] = len(value)
        for idx, item in enumerate(value[:3]):
            counts.update(count_lists(item, f"{prefix}[{idx}]"))
    elif isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(item, list):
                counts[path] = len(item)
            counts.update(count_lists(item, path))
    return counts


def has_nonempty_payload(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, list) and item:
                return True
            if isinstance(item, dict) and has_nonempty_payload(item):
                return True
    return False


def classify_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("state") == "ERROR":
        return result
    status = result.get("status")
    body = result.get("json")
    if not status:
        result["state"] = "ERROR"
        return result
    if status == 404:
        result["state"] = "MISSING_ENDPOINT"
        return result
    if status >= 400:
        result["state"] = "ERROR"
        return result
    if body is None:
        result["state"] = "LIVE_UNCONFIRMED"
        return result

    seed_hits = marker_hits(body)
    suspect_sources = source_suspect(body)
    if seed_hits or suspect_sources:
        result["state"] = "SEED_SUSPECT"
        result["seed_markers"] = seed_hits
        result["suspect_sources"] = suspect_sources
        return result

    if isinstance(body, dict) and body.get("available") is False:
        result["state"] = "UNAVAILABLE"
        return result

    result["list_counts"] = count_lists(body)
    kind = result.get("kind")
    if kind == "status":
        result["state"] = "LIVE_DATA"
    elif has_nonempty_payload(body):
        result["state"] = "LIVE_DATA"
    elif isinstance(body, dict) and body.get("available") is True:
        result["state"] = "LIVE_EMPTY"
    elif isinstance(body, (dict, list)):
        result["state"] = "LIVE_EMPTY"
    else:
        result["state"] = "LIVE_UNCONFIRMED"
    return result


def classify_all(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [classify_result(result) for result in results]


def first_id_from_list(body: Any, list_key: str, id_fields: tuple[str, ...]) -> Any:
    if not isinstance(body, dict):
        return None
    rows = body.get(list_key)
    if not isinstance(rows, list) or not rows:
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field in id_fields:
            if row.get(field) is not None:
                return row[field]
    return None


def result_by_key(results: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for result in results:
        if result.get("key") == key:
            return result
    return None


def build_dynamic_endpoints(results: list[dict[str, Any]], cluster_id: str) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    rec_id = first_id_from_list((result_by_key(results, "ai_recommendations") or {}).get("json"), "recommendations", ("id", "recommendation_id"))
    if rec_id is not None:
        endpoints.append(ep("ai_recommendation_detail", "AI recommendation detail", "GET", f"/api/v1/ai/recommendations/{rec_id}", "data", critical=False))
        endpoints.append(ep("ai_evidence_pack", "AI evidence pack", "GET", f"/api/v1/ai/evidence-packs/{rec_id}", "data", critical=False))

    run_id = first_id_from_list((result_by_key(results, "ai_agents") or {}).get("json"), "runs", ("id", "run_id"))
    if run_id is not None:
        endpoints.append(ep("ai_agent_run_detail", "AI Platform agent run detail", "GET", f"/api/v1/ai/agents/{run_id}", "data", critical=False))

    legacy_run_id = first_id_from_list((result_by_key(results, "ai_agent_runs") or {}).get("json"), "runs", ("id", "run_id"))
    if legacy_run_id is not None:
        endpoints.append(ep("ai_agent_legacy_run_detail", "AI Agent run detail", "GET", f"/api/ai-agent/runs/{legacy_run_id}", "data", critical=False))

    legacy_rec_id = first_id_from_list((result_by_key(results, "ai_agent_recs") or {}).get("json"), "recommendations", ("id", "recommendation_id"))
    if legacy_rec_id is not None:
        endpoints.append(ep("ai_agent_rec_detail", "AI Agent recommendation detail", "GET", f"/api/ai-agent/recommendations/{legacy_rec_id}", "data", critical=False))

    model_id = first_id_from_list((result_by_key(results, "ml_models") or {}).get("json"), "models", ("id", "model_id"))
    if model_id is not None:
        endpoints.append(ep("ml_model_detail", "ML model detail", "GET", f"/api/v1/ml/models/{model_id}", "data", critical=False))

    snapshot_id = first_id_from_list((result_by_key(results, "ml_anomalies") or {}).get("json"), "anomalies", ("snapshot_id",))
    if snapshot_id is not None:
        endpoints.append(ep("ml_anomaly_snapshot", "ML anomaly snapshot detail", "GET", f"/api/v1/ml/anomalies/{snapshot_id}", "data", critical=False))

    incident_id = first_id_from_list((result_by_key(results, "ai_incidents") or {}).get("json"), "incidents", ("id", "incident_id"))
    if incident_id is not None:
        endpoints.append(ep("ai_incident_detail", "AI incident detail", "GET", f"/api/v1/ai/incidents/{incident_id}", "data", critical=False))

    signature_id = first_id_from_list((result_by_key(results, "log_analytics_signatures") or {}).get("json"), "signatures", ("id", "sid", "signature_id"))
    if signature_id is not None:
        cid = urllib.parse.quote(cluster_id, safe="")
        endpoints.append(
            ep(
                "log_analytics_signature_detail",
                "Log Analytics signature detail",
                "GET",
                f"/api/v1/clusters/{cid}/log-analytics/signatures/{urllib.parse.quote(str(signature_id), safe='')}?range=1h&step=5m",
                "data",
                critical=False,
            )
        )
    return endpoints


REMOTE_FRONTEND_SCAN = r'''
import json
import os
import re

PANEL_SCRIPT_FILES = REQUEST["panel_script_files"]
SEED_MARKERS = [m.lower() for m in REQUEST["seed_markers"]]
REQUIRED_AI_LIVE_REFS = REQUEST["required_ai_live_refs"]
ROOTS = ["/app/static", "/opt/app-root/src/static", "/workspace/static", "/code/static", "/srv/app/static", "static"]

def read_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except Exception as exc:
        return None

def find_root():
    for root in ROOTS:
        if os.path.isdir(root):
            return root
    return None

root = find_root()
out = {"available": bool(root), "root": root, "files": [], "blocking_markers": [], "missing_live_refs": []}
if root:
    for rel in PANEL_SCRIPT_FILES:
        path = os.path.join(root, rel)
        text = read_file(path)
        item = {"file": rel, "exists": text is not None}
        if text is not None:
            low = text.lower()
            item["bytes"] = len(text.encode("utf-8", errors="replace"))
            item["markers"] = [marker for marker in SEED_MARKERS if marker in low]
            if rel != "index.html":
                item["live_refs"] = [ref for ref in REQUIRED_AI_LIVE_REFS if ref in text]
            if item.get("markers"):
                out["blocking_markers"].append(item)
        out["files"].append(item)
    index_text = read_file(os.path.join(root, "index.html")) or ""
    out["script_tags"] = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', index_text)
    ai_platform = read_file(os.path.join(root, "dist/ai_platform.js")) or ""
    out["missing_live_refs"] = [ref for ref in REQUIRED_AI_LIVE_REFS if ref not in ai_platform]
print(json.dumps(out, default=str))
'''


def frontend_scan_via_pod(namespace: str, pod: str) -> dict[str, Any]:
    request = {
        "panel_script_files": PANEL_SCRIPT_FILES,
        "seed_markers": SEED_MARKERS,
        "required_ai_live_refs": REQUIRED_AI_LIVE_REFS,
    }
    code = "REQUEST = " + repr(request) + "\n" + REMOTE_FRONTEND_SCAN
    result = run_cmd(["oc", "-n", namespace, "exec", "-i", pod, "--", "python3", "-"], input_text=code, timeout=60)
    if not result["ok"]:
        return {"available": False, "error": "frontend scan oc exec failed", "stderr": result["stderr"][-4000:]}
    try:
        return json.loads(result["stdout"])
    except Exception as exc:
        return {"available": False, "error": f"could not parse frontend scan JSON: {exc}", "stdout": result["stdout"][-4000:]}


def frontend_scan_local(static_dir: str | None) -> dict[str, Any]:
    if not static_dir:
        return {"available": False, "reason": "no --static-dir passed"}
    root = Path(static_dir)
    if not root.is_dir():
        return {"available": False, "reason": f"{static_dir} is not a directory"}
    out: dict[str, Any] = {"available": True, "root": str(root), "files": [], "blocking_markers": [], "missing_live_refs": []}
    for rel in PANEL_SCRIPT_FILES:
        path = root / rel
        item: dict[str, Any] = {"file": rel, "exists": path.is_file()}
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            low = text.lower()
            item["bytes"] = len(text.encode("utf-8", errors="replace"))
            item["markers"] = [marker for marker in SEED_MARKERS if marker.lower() in low]
            if rel != "index.html":
                item["live_refs"] = [ref for ref in REQUIRED_AI_LIVE_REFS if ref in text]
            if item.get("markers"):
                out["blocking_markers"].append(item)
        out["files"].append(item)
    index_path = root / "index.html"
    index_text = index_path.read_text(encoding="utf-8", errors="replace") if index_path.is_file() else ""
    out["script_tags"] = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', index_text)
    ai_platform_path = root / "dist/ai_platform.js"
    ai_platform = ai_platform_path.read_text(encoding="utf-8", errors="replace") if ai_platform_path.is_file() else ""
    out["missing_live_refs"] = [ref for ref in REQUIRED_AI_LIVE_REFS if ref not in ai_platform]
    return out


def llm_ollama_list(namespace: str, llm_workloads: dict[str, Any]) -> dict[str, Any]:
    pods = llm_workloads.get("pods") or []
    if not pods:
        return {"available": False, "reason": "no running LLM/Ollama pod found"}
    pod = pods[0]["name"]
    result = run_cmd(["oc", "-n", namespace, "exec", pod, "--", "ollama", "list"], timeout=60)
    return {
        "available": result["ok"],
        "pod": pod,
        "stdout": result["stdout"],
        "stderr": result["stderr"][-2000:],
        "returncode": result["returncode"],
    }


def llm_generation_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    if isinstance(body.get("response"), str):
        return body["response"].strip()
    choices = body.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
            if isinstance(choice.get("text"), str):
                return choice["text"].strip()
    return ""


def analyze_llm(report: dict[str, Any]) -> dict[str, Any]:
    endpoints = report.get("endpoint_results") or []
    llm_endpoints = report.get("llm_service_results") or []
    model_gateway = (result_by_key(endpoints, "ai_model_gateway") or {}).get("json") or {}
    assistant_status = (result_by_key(endpoints, "assistant_status") or {}).get("json") or {}
    assistant_ask = (result_by_key(report.get("active_results") or [], "assistant_ask_active") or {}).get("json") or {}

    provider = model_gateway.get("provider")
    model = model_gateway.get("model")
    configured = bool(model_gateway.get("configured"))
    base_url = model_gateway.get("base_url")
    assistant_model = assistant_status.get("model")
    assistant_backend = assistant_status.get("backend")
    llm_connected = bool(assistant_status.get("llm_connected"))

    service_models: list[str] = []
    generation_sources: list[dict[str, Any]] = []
    for result in llm_endpoints:
        body = result.get("json")
        if not isinstance(body, dict):
            continue
        generated = llm_generation_text(body)
        if result.get("ok") and generated:
            generation_sources.append(
                {
                    "key": result.get("key"),
                    "model": body.get("model") or model,
                    "text_bytes": len(generated.encode("utf-8", errors="replace")),
                }
            )
        rows = []
        if isinstance(body.get("models"), list):
            rows.extend(body["models"])
        if isinstance(body.get("data"), list):
            rows.extend(body["data"])
        for row in rows:
            if isinstance(row, dict):
                name = row.get("name") or row.get("model") or row.get("id")
                if name:
                    service_models.append(str(name))

    ollama_list = report.get("llm_ollama_list") or {}
    if ollama_list.get("available") and isinstance(ollama_list.get("stdout"), str):
        for line in ollama_list["stdout"].splitlines()[1:]:
            fields = line.split()
            if fields:
                service_models.append(fields[0])

    ask_model = assistant_ask.get("model") or assistant_ask.get("provider")
    if assistant_ask and ask_model and "heuristic" not in str(ask_model).lower():
        state = "LLM_ACTIVE_RESPONSE"
        generation_sources.append(
            {
                "key": "assistant_ask_active",
                "model": ask_model,
                "text_bytes": len(str(assistant_ask.get("answer") or "").encode("utf-8", errors="replace")),
            }
        )
    elif generation_sources:
        state = "LLM_ACTIVE_RESPONSE"
    elif configured and provider == "local" and service_models:
        state = "LOCAL_LLM_MODEL_VISIBLE"
    elif configured and provider == "local":
        state = "LOCAL_LLM_CONFIGURED_MODEL_NOT_VISIBLE"
    elif llm_connected:
        state = "LLM_CONNECTED"
    elif assistant_backend == "heuristic" or assistant_model == "heuristic":
        state = "HEURISTIC_ONLY"
    elif configured:
        state = "LLM_CONFIGURED_UNCONFIRMED"
    else:
        state = "LLM_DISABLED_OR_NOT_CONFIGURED"

    return {
        "state": state,
        "provider": provider,
        "model": model,
        "configured": configured,
        "base_url": base_url,
        "assistant_model": assistant_model,
        "assistant_backend": assistant_backend,
        "assistant_llm_connected": llm_connected,
        "active_ask_model": ask_model,
        "generation_sources": generation_sources,
        "service_models": sorted(set(service_models)),
    }


def summarize(report: dict[str, Any], expect_llm_live: bool) -> dict[str, Any]:
    endpoint_results = report.get("endpoint_results") or []
    active_results = report.get("active_results") or []
    all_results = endpoint_results + active_results
    by_state: dict[str, int] = {}
    for result in all_results:
        state = result.get("state") or "UNKNOWN"
        by_state[state] = by_state.get(state, 0) + 1

    critical_bad = [
        result
        for result in all_results
        if result.get("critical") and result.get("state") in {"ERROR", "MISSING_ENDPOINT", "SEED_SUSPECT"}
    ]
    unavailable = [result for result in all_results if result.get("critical") and result.get("state") == "UNAVAILABLE"]
    frontend = report.get("frontend_scan") or {}
    frontend_bad = bool(frontend.get("blocking_markers") or frontend.get("missing_live_refs"))
    llm = analyze_llm(report)
    llm_bad = expect_llm_live and llm["state"] not in LLM_GENERATION_STATES
    seed_suspects = [result for result in all_results if result.get("state") == "SEED_SUSPECT"]
    ai_endpoint_results = [result for result in endpoint_results if result.get("key") in AI_EVIDENCE_ENDPOINT_KEYS]
    ai_live = [result for result in ai_endpoint_results if result.get("state") in LIVE_STATES]
    ai_live_nonempty = [result for result in ai_endpoint_results if result.get("state") == "LIVE_DATA"]
    llm_local_working = llm["state"] in LLM_WORKING_STATES

    findings = []
    if critical_bad:
        findings.append(f"{len(critical_bad)} critical endpoint(s) failed/missing/seed-suspect")
    if unavailable:
        findings.append(f"{len(unavailable)} critical endpoint(s) returned available=false")
    if frontend.get("blocking_markers"):
        findings.append("AI frontend bundle still contains seed/mock/sample fallback markers")
    if frontend.get("missing_live_refs"):
        findings.append("AI Platform frontend bundle is missing required live endpoint references")
    if llm_bad:
        findings.append(f"LLM was expected live but extractor classified it as {llm['state']}")
    if not findings:
        findings.append("No blocking AI/ML/RAG/LLM endpoint or frontend issue detected by this extractor")

    overall = "PASS"
    if critical_bad or frontend_bad or llm_bad:
        overall = "FAIL"
    elif unavailable or llm["state"] in {"HEURISTIC_ONLY", "LOCAL_LLM_CONFIGURED_MODEL_NOT_VISIBLE", "LLM_DISABLED_OR_NOT_CONFIGURED"}:
        overall = "WARN"

    return {
        "overall": overall,
        "by_state": by_state,
        "seeded_ai_exposed": bool(seed_suspects or frontend.get("blocking_markers")),
        "seed_suspect_count": len(seed_suspects) + len(frontend.get("blocking_markers") or []),
        "live_ai_endpoint_count": len(ai_live),
        "live_ai_nonempty_endpoint_count": len(ai_live_nonempty),
        "live_ai_data_confirmed": bool(ai_live_nonempty),
        "local_llm_working": llm_local_working,
        "local_llm_generation_confirmed": llm["state"] in LLM_GENERATION_STATES,
        "llm_required": bool(expect_llm_live),
        "critical_bad": [{"key": r.get("key"), "state": r.get("state"), "status": r.get("status"), "path": r.get("path")} for r in critical_bad],
        "critical_unavailable": [{"key": r.get("key"), "state": r.get("state"), "status": r.get("status"), "path": r.get("path")} for r in unavailable],
        "llm": llm,
        "findings": findings,
    }


def compact_endpoint_row(result: dict[str, Any]) -> str:
    state = result.get("state", "UNKNOWN")
    status = result.get("status")
    method = result.get("method")
    path = result.get("path")
    panel = result.get("panel")
    counts = result.get("list_counts") or {}
    count_bits = []
    for key in ("models", "data", "anomalies", "forecasts", "incidents", "recommendations", "runs", "audit", "documents", "entries", "signatures", "findings"):
        if key in counts:
            count_bits.append(f"{key}={counts[key]}")
    count_text = ", ".join(count_bits[:4])
    if result.get("error") and not count_text:
        count_text = str(result["error"])[:120]
    return f"{state:18} {str(status or '-'):>4} {method:4} {panel:34} {path} {count_text}"


def write_text_report(report: dict[str, Any], path: str) -> None:
    summary = report["summary"]
    lines = [
        "Object Monitor AI Deep Module Extractor",
        f"timestamp_utc: {report['metadata']['timestamp_utc']}",
        f"namespace: {report['metadata'].get('namespace')}",
        f"deployment: {report['metadata'].get('deployment')}",
        f"app_pod: {report.get('app_pod', {}).get('pod')}",
        f"cluster_id: {report['metadata'].get('cluster_id')}",
        f"cluster_name: {report['metadata'].get('cluster_name')}",
        f"overall: {summary['overall']}",
        f"seeded_ai_exposed: {'YES' if summary.get('seeded_ai_exposed') else 'NO'}",
        f"live_ai_data_confirmed: {'YES' if summary.get('live_ai_data_confirmed') else 'NO'}",
        f"live_ai_endpoint_count: {summary.get('live_ai_endpoint_count', 0)}",
        f"live_ai_nonempty_endpoint_count: {summary.get('live_ai_nonempty_endpoint_count', 0)}",
        f"local_llm_working: {'YES' if summary.get('local_llm_working') else 'NO'}",
        f"local_llm_generation_confirmed: {'YES' if summary.get('local_llm_generation_confirmed') else 'NO'}",
        f"llm_required: {'YES' if summary.get('llm_required') else 'NO'}",
        "",
        "Findings:",
    ]
    lines.extend(f"- {finding}" for finding in summary["findings"])
    lines.extend(
        [
            "",
            "LLM:",
            json.dumps(summary["llm"], indent=2, sort_keys=True),
            "",
            "Endpoint matrix:",
            "STATE              HTTP METH PANEL                              PATH COUNTS/ERROR",
        ]
    )
    for result in report.get("endpoint_results") or []:
        lines.append(compact_endpoint_row(result))
    if report.get("llm_service_results"):
        lines.extend(["", "LLM service checks:"])
        for result in report["llm_service_results"]:
            lines.append(compact_endpoint_row(result))
    if report.get("active_results"):
        lines.extend(["", "Active check results:"])
        for result in report["active_results"]:
            lines.append(compact_endpoint_row(result))
    lines.extend(["", "Frontend scan:"])
    frontend = report.get("frontend_scan") or {}
    lines.append(json.dumps({
        "available": frontend.get("available"),
        "root": frontend.get("root"),
        "blocking_markers": frontend.get("blocking_markers"),
        "missing_live_refs": frontend.get("missing_live_refs"),
        "script_tags": frontend.get("script_tags"),
    }, indent=2, sort_keys=True))
    lines.extend(["", "LLM workloads:"])
    lines.append(json.dumps(report.get("llm_workloads"), indent=2, sort_keys=True))
    lines.extend(["", f"JSON report: {report['metadata'].get('json_out')}"])
    Path(path).write_text(redact_text("\n".join(lines) + "\n"), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deep AI/ML/RAG/LLM post-deploy extractor for object-monitor.")
    parser.add_argument("--namespace", default=os.environ.get("CNS", DEFAULT_NAMESPACE))
    parser.add_argument("--deployment", default=os.environ.get("DEPLOY", DEFAULT_DEPLOYMENT))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--cluster-id", default="auto", help="cluster_id for cluster-scoped endpoints, or auto")
    parser.add_argument("--cluster-name", default="", help="cluster name for active ML/agent jobs; defaults from env or cluster_id")
    parser.add_argument("--base-url", default="", help="Optional direct route/base URL. If omitted, probes run inside the app pod.")
    parser.add_argument("--llm-service", default=DEFAULT_LLM_SERVICE)
    parser.add_argument("--static-dir", default="", help="Optional local static/ path for frontend scan when not using oc exec.")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--text-out", default="")
    parser.add_argument("--out-dir", default="/tmp")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-body-bytes", type=int, default=2_000_000)
    parser.add_argument("--range-hours", type=int, default=6)
    parser.add_argument("--lookback-minutes", type=int, default=30)
    parser.add_argument("--ask-question", default="After local LLM deployment, summarize current PostgreSQL AI incident risk from live evidence and say whether generation is working.")
    parser.add_argument("--llm-generation-prompt", default="Reply with exactly: LOCAL_LLM_LIVE")
    parser.add_argument("--run-active-checks", action="store_true", help="Run assistant ask and incident evaluate checks; can write audit/incident rows.")
    parser.add_argument("--run-agent", action="store_true", help="Run AI agent recommendation generation; creates an agent run and recommendation rows.")
    parser.add_argument("--run-ml-jobs", action="store_true", help="Run ML score and forecast jobs; creates ML/forecast rows.")
    parser.add_argument("--expect-llm-live", action="store_true", help="Fail summary when the LLM is still heuristic/unavailable.")
    parser.add_argument("--strict-exit", action="store_true", help="Exit 1 when summary overall is FAIL.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for --base-url HTTPS routes.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    stamp = utc_stamp()
    json_out = args.json_out or str(Path(args.out_dir) / f"objmon_ai_deep_report_{stamp}.json")
    text_out = args.text_out or str(Path(args.out_dir) / f"objmon_ai_deep_report_{stamp}.txt")

    oc_available = shutil.which("oc") is not None
    if not oc_available and not args.base_url:
        print("FATAL: oc is not on PATH and --base-url was not provided.", file=sys.stderr)
        return 2

    deploy_json = oc_json(args.namespace, ["get", "deployment", args.deployment, "-o", "json"]) if oc_available else None
    env_summary = deployment_env_summary(deploy_json)
    cluster_id = args.cluster_id
    if cluster_id == "auto":
        cluster_id = (
            value_from_env_summary(env_summary, ["PGC_CLUSTER_ID", "LOCAL_CLUSTER_ID", "CLUSTER_ID"])
            or "prod"
        )
    cluster_name = args.cluster_name or value_from_env_summary(env_summary, ["PGC_CLUSTER", "CLUSTER_NAME"]) or cluster_id

    app_pod = find_running_pod(args.namespace, args.deployment, deploy_json) if oc_available else {"available": False}
    base_url = args.base_url.rstrip("/") if args.base_url else f"http://127.0.0.1:{args.port}"

    metadata = {
        "timestamp_utc": stamp,
        "namespace": args.namespace,
        "deployment": args.deployment,
        "cluster_id": cluster_id,
        "cluster_name": cluster_name,
        "base_url": base_url,
        "probe_mode": "direct" if args.base_url else "in_pod",
        "json_out": json_out,
        "text_out": text_out,
        "active_checks_enabled": bool(args.run_active_checks),
        "agent_run_enabled": bool(args.run_agent),
        "ml_jobs_enabled": bool(args.run_ml_jobs),
    }

    endpoints = build_base_endpoints(cluster_id)
    if args.base_url:
        endpoint_results = bulk_probe_direct(base_url, endpoints, args.timeout, args.max_body_bytes, args.insecure)
    elif app_pod.get("pod"):
        endpoint_results = bulk_probe_via_pod(args.namespace, app_pod["pod"], base_url, endpoints, args.timeout, args.max_body_bytes)
    else:
        endpoint_results = [
            {**endpoint, "ok": False, "status": None, "state": "ERROR", "error": "no running app pod found"}
            for endpoint in endpoints
        ]
    endpoint_results = classify_all(endpoint_results)

    dynamic_endpoints = build_dynamic_endpoints(endpoint_results, cluster_id)
    if dynamic_endpoints:
        if args.base_url:
            dynamic_results = bulk_probe_direct(base_url, dynamic_endpoints, args.timeout, args.max_body_bytes, args.insecure)
        elif app_pod.get("pod"):
            dynamic_results = bulk_probe_via_pod(args.namespace, app_pod["pod"], base_url, dynamic_endpoints, args.timeout, args.max_body_bytes)
        else:
            dynamic_results = []
        endpoint_results.extend(classify_all(dynamic_results))

    active_endpoints = build_active_endpoints(args, cluster_name)
    if active_endpoints:
        if args.base_url:
            active_results = bulk_probe_direct(base_url, active_endpoints, args.timeout, args.max_body_bytes, args.insecure)
        elif app_pod.get("pod"):
            active_results = bulk_probe_via_pod(args.namespace, app_pod["pod"], base_url, active_endpoints, args.timeout, args.max_body_bytes)
        else:
            active_results = [
                {**endpoint, "ok": False, "status": None, "state": "ERROR", "error": "no running app pod found"}
                for endpoint in active_endpoints
            ]
        active_results = classify_all(active_results)
    else:
        active_results = []

    llm_workloads = find_llm_workloads(args.namespace) if oc_available else {"deployments": [], "pods": [], "services": []}
    llm_model = value_from_env_summary(env_summary, ["AI_MODEL", "ANTHROPIC_MODEL", "AZURE_OPENAI_DEPLOYMENT"]) or "object-monitor-llm"
    llm_endpoints = build_llm_service_endpoints(
        llm_model,
        args.llm_generation_prompt,
        run_generation=bool(args.expect_llm_live),
    )
    if app_pod.get("pod"):
        llm_service_results = bulk_probe_via_pod(args.namespace, app_pod["pod"], args.llm_service, llm_endpoints, args.timeout, args.max_body_bytes)
    elif args.base_url:
        llm_service_results = bulk_probe_direct(args.llm_service, llm_endpoints, args.timeout, args.max_body_bytes, args.insecure)
    else:
        llm_service_results = []
    llm_service_results = classify_all(llm_service_results)
    ollama_list = llm_ollama_list(args.namespace, llm_workloads) if oc_available else {"available": False, "reason": "oc not available"}

    if oc_available and app_pod.get("pod"):
        frontend_scan = frontend_scan_via_pod(args.namespace, app_pod["pod"])
    else:
        frontend_scan = frontend_scan_local(args.static_dir)

    report: dict[str, Any] = {
        "metadata": metadata,
        "deployment_env": env_summary,
        "app_pod": app_pod,
        "llm_workloads": llm_workloads,
        "llm_ollama_list": ollama_list,
        "frontend_scan": frontend_scan,
        "endpoint_results": endpoint_results,
        "active_results": active_results,
        "llm_service_results": llm_service_results,
    }
    report["summary"] = summarize(report, args.expect_llm_live)

    safe_report = redact_for_report(report)
    Path(json_out).write_text(json.dumps(safe_report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    write_text_report(safe_report, text_out)

    print(f"JSON report: {json_out}")
    print(f"Text report: {text_out}")
    print(f"Overall: {report['summary']['overall']}")
    for finding in report["summary"]["findings"]:
        print(f"- {finding}")

    if args.strict_exit and report["summary"]["overall"] == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
