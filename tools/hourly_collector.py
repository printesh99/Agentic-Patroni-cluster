#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_json(cmd: list[str], execute: bool) -> tuple[dict[str, Any], str, int]:
    if not execute:
        return {}, "PLAN ONLY: " + " ".join(cmd), 0
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        return {}, proc.stderr.strip() or proc.stdout.strip(), proc.returncode
    try:
        return json.loads(proc.stdout), proc.stdout, 0
    except json.JSONDecodeError:
        return {}, proc.stdout, 1


def k8s_request(path: str, execute: bool) -> tuple[dict[str, Any], str, int]:
    if not execute:
        return {}, "PLAN ONLY: GET " + path, 0
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ca_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    if not host or not token_path.exists():
        return {}, "Kubernetes service account token is not available", 1
    token = token_path.read_text(encoding="utf-8").strip()
    url = f"https://{host}:{port}{path}"
    ctx = ssl.create_default_context(cafile=str(ca_path)) if ca_path.exists() else ssl.create_default_context()
    req = urllib.request.Request(url, headers={"authorization": "Bearer " + token, "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text), text, 0
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return {}, text or str(exc), exc.code
    except Exception as exc:
        return {}, str(exc), 1


def get_resource(args: argparse.Namespace, kind: str) -> tuple[dict[str, Any], str, int]:
    transport = args.transport
    if transport == "auto":
        transport = "oc" if shutil.which(args.oc) else "k8s"
    if transport == "oc":
        if kind == "namespace":
            return run_json([args.oc, "get", "namespace", args.namespace, "-o", "json"], args.execute)
        if kind == "postgrescluster":
            return run_json([args.oc, "get", "postgrescluster", args.cluster, "-n", args.namespace, "-o", "json"], args.execute)
        return run_json([args.oc, "get", kind, "-n", args.namespace, "-o", "json"], args.execute)
    if kind == "namespace":
        return k8s_request(f"/api/v1/namespaces/{args.namespace}", args.execute)
    if kind == "postgrescluster":
        return k8s_request(
            f"/apis/postgres-operator.crunchydata.com/v1beta1/namespaces/{args.namespace}/postgresclusters/{args.cluster}",
            args.execute,
        )
    plural = {"pods": "pods", "pvc": "persistentvolumeclaims", "events": "events"}[kind]
    return k8s_request(f"/api/v1/namespaces/{args.namespace}/{plural}", args.execute)


def status_rank(values: list[str]) -> str:
    if "critical" in values or "failed" in values:
        return "critical"
    if "warn" in values or "warning" in values:
        return "warn"
    return "ok"


def check(name: str, category: str, status: str, message: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "check_name": name,
        "category": category,
        "status": status,
        "message": message,
        "evidence": evidence or {},
    }


def finding(args: argparse.Namespace, severity: str, component: str, finding_type: str, title: str, detail: str, evidence: dict[str, Any]) -> dict[str, Any]:
    raw = "|".join([args.cluster, component, finding_type, title])
    return {
        "fingerprint": raw.lower().replace(" ", "_")[:180],
        "severity": severity,
        "region": args.region,
        "namespace": args.namespace,
        "cluster_name": args.cluster,
        "component": component,
        "finding_type": finding_type,
        "title": title,
        "detail": detail,
        "evidence": evidence,
    }


def collect(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    checks: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    rollups: list[dict[str, Any]] = []

    ns_doc, ns_raw, ns_rc = get_resource(args, "namespace")
    checks.append(check(
        "namespace_present",
        "openshift",
        "ok" if ns_rc == 0 else "failed",
        "namespace is readable" if ns_rc == 0 else ns_raw[:300],
        {"namespace": args.namespace},
    ))

    cluster_doc, cluster_raw, cluster_rc = get_resource(args, "postgrescluster")
    cluster_status = cluster_doc.get("status", {}) if cluster_doc else {}
    ready_instances = int(cluster_status.get("instancesReady") or cluster_status.get("readyReplicas") or 0)
    desired_instances = int(cluster_doc.get("spec", {}).get("instances", [{}])[0].get("replicas") or ready_instances or 0) if cluster_doc else 0
    pc_status = "ok" if cluster_rc == 0 and (desired_instances == 0 or ready_instances >= desired_instances) else ("failed" if cluster_rc else "warn")
    checks.append(check(
        "postgrescluster_ready",
        "pgo",
        pc_status,
        f"{ready_instances}/{desired_instances or ready_instances} instances ready" if cluster_rc == 0 else cluster_raw[:300],
        {"ready_instances": ready_instances, "desired_instances": desired_instances},
    ))
    if pc_status != "ok":
        findings.append(finding(args, "critical" if pc_status == "failed" else "warning", "pgo", "postgrescluster_ready", "PostgresCluster is not fully ready", "Ready instance count is below desired state.", checks[-1]["evidence"]))

    pods_doc, pods_raw, pods_rc = get_resource(args, "pods")
    pods = pods_doc.get("items", []) if pods_doc else []
    restart_total = 0
    not_ready = 0
    for pod in pods:
        phase = pod.get("status", {}).get("phase")
        if phase not in {"Running", "Succeeded"}:
            not_ready += 1
        for cs in pod.get("status", {}).get("containerStatuses", []) or []:
            restart_total += int(cs.get("restartCount") or 0)
            if not cs.get("ready") and phase == "Running":
                not_ready += 1
    pod_status = "failed" if pods_rc else ("warn" if not_ready or restart_total >= args.restart_warn else "ok")
    checks.append(check(
        "pod_readiness",
        "openshift",
        pod_status,
        f"{len(pods)} pods observed, {not_ready} not ready, {restart_total} restarts",
        {"pods": len(pods), "not_ready": not_ready, "restart_total": restart_total},
    ))
    if not_ready:
        findings.append(finding(args, "warning", "openshift", "pod_readiness", "Pods are not ready", "One or more pods or containers are not ready.", checks[-1]["evidence"]))

    pvc_doc, pvc_raw, pvc_rc = get_resource(args, "pvc")
    pvcs = pvc_doc.get("items", []) if pvc_doc else []
    bad_pvcs = [p.get("metadata", {}).get("name", "") for p in pvcs if p.get("status", {}).get("phase") != "Bound"]
    checks.append(check(
        "pvc_bound",
        "storage",
        "failed" if pvc_rc else ("warn" if bad_pvcs else "ok"),
        f"{len(pvcs)} PVCs observed, {len(bad_pvcs)} not bound",
        {"pvcs": len(pvcs), "not_bound": bad_pvcs[:20]},
    ))
    if bad_pvcs:
        findings.append(finding(args, "critical", "storage", "pvc_bound", "PVCs are not bound", "One or more PostgreSQL PVCs are not Bound.", checks[-1]["evidence"]))

    events_doc, events_raw, events_rc = get_resource(args, "events")
    events = events_doc.get("items", []) if events_doc else []
    warnings: dict[str, int] = {}
    for event in events:
        if event.get("type") == "Warning":
            reason = str(event.get("reason") or "Warning")
            warnings[reason] = warnings.get(reason, 0) + 1
    checks.append(check(
        "recent_warning_events",
        "openshift",
        "failed" if events_rc else ("warn" if warnings else "ok"),
        f"{sum(warnings.values())} warning events by reason",
        {"warning_reasons": warnings},
    ))
    for reason, count in sorted(warnings.items(), key=lambda item: item[1], reverse=True)[:5]:
        findings.append(finding(args, "warning", "openshift", "warning_events", f"Recent OpenShift warning events: {reason}", f"{count} warning event(s) observed in namespace events.", {"reason": reason, "count": count}))

    for reason, count in sorted(warnings.items(), key=lambda item: item[1], reverse=True)[:10]:
        rollups.append({
            "region": args.region,
            "namespace": args.namespace,
            "cluster_name": args.cluster,
            "severity": "warning",
            "pattern": "event:" + reason,
            "count": count,
            "loki_query": args.loki_query_template.format(namespace=args.namespace, cluster=args.cluster, reason=reason) if args.loki_query_template else None,
            "sample_redacted": f"{count} namespace warning events with reason {reason}",
        })

    statuses = [item["status"] for item in checks]
    return {
        "run_uuid": str(uuid.uuid4()),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "region": args.region,
        "dc": args.dc,
        "namespace": args.namespace,
        "cluster_name": args.cluster,
        "collector_mode": args.mode,
        "issue_id": args.issue,
        "status": status_rank(statuses),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "command_count": 4,
        "failed_command_count": len([item for item in checks if item["status"] == "failed"]),
        "summary": {"execute": args.execute, "source": "hourly_collector.py"},
        "checks": checks,
        "findings": findings,
        "log_error_rollups": rollups,
    }


def push_payload(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if not args.push_url:
        return
    token = os.environ.get(args.push_token_env, "")
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = "Bearer " + token
    req = urllib.request.Request(args.push_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=args.push_timeout) as resp:
        sys.stdout.write(resp.read().decode("utf-8") + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect lightweight hourly PostgreSQL/OpenShift health facts and optionally push them to the console API.")
    parser.add_argument("--mode", choices=["hourly", "incident", "manual"], default=os.environ.get("COLLECTOR_MODE", "hourly"))
    parser.add_argument("--issue", default=os.environ.get("ISSUE_ID", "ISSUE-500"))
    parser.add_argument("--region", required=True)
    parser.add_argument("--dc", choices=["dc1", "dc2", "unknown"], default=os.environ.get("DC", "unknown"))
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--oc", default=os.environ.get("OC_BIN", "oc"))
    parser.add_argument("--transport", choices=["auto", "k8s", "oc"], default=os.environ.get("COLLECTOR_TRANSPORT", "auto"))
    parser.add_argument("--execute", action="store_true", help="Run live read-only oc commands. Default is plan-only.")
    parser.add_argument("--summary-json", help="Write compact summary payload to this path.")
    parser.add_argument("--push-url", default=os.environ.get("COLLECTOR_PUSH_URL"))
    parser.add_argument("--push-token-env", default=os.environ.get("COLLECTOR_PUSH_TOKEN_ENV", "COLLECTOR_INGEST_TOKEN"))
    parser.add_argument("--push-timeout", type=int, default=20)
    parser.add_argument("--restart-warn", type=int, default=5)
    parser.add_argument("--loki-query-template", default=os.environ.get("LOKI_QUERY_TEMPLATE", ""))
    args = parser.parse_args()
    if args.mode == "hourly" and not args.execute:
        # Plan-only is intentionally allowed for UAT dry runs.
        pass
    return args


def main() -> int:
    args = parse_args()
    payload = collect(args)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.summary_json:
        path = Path(args.summary_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    push_payload(args, payload)
    return 0 if payload["status"] != "critical" else 1


if __name__ == "__main__":
    raise SystemExit(main())
