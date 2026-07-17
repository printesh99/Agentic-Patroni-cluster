#!/usr/bin/env python3
"""
UAT AI Evidence Extractor
=========================
Collects all evidence needed by the AI DBA Assistant evidence builder
from the UAT PostgreSQL / Patroni cluster on OpenShift.

Designed for air-gapped environments:
  - No pip dependencies (stdlib only)
  - Uses oc CLI (already available on operator machines)
  - Never reads or prints Secret values
  - Every section fails independently — one failure never aborts others
  - Produces a single structured JSON bundle + tar.gz

Usage:
  python3 uat_ai_evidence_extractor.py [OPTIONS]

Options:
  --namespace       OpenShift namespace            [default: uat-pgcluster-uae]
  --cluster         PostgresCluster / Patroni name [default: uat-pgcluster-uae]
  --patroni-cluster Patroni cluster scope name     [default: uat-pgcluster-uae-ha]
  --context         oc context (leave blank to use current)
  --pg-port         PostgreSQL port in cluster     [default: 5555]
  --pgbouncer-port  PgBouncer admin port           [default: 5432]
  --prometheus-url  Prometheus URL (if accessible) [default: auto-detect svc]
  --patroni-url     Patroni API URL (if accessible)[default: via pod exec]
  --log-lines       Log lines to collect per source[default: 300]
  --out-dir         Output directory               [default: ./uat_evidence_TIMESTAMP]
  --dry-run         Print commands, don't execute

What this script collects (in order):
  1.  Cluster metadata    — namespace, PostgresCluster CR, pod list, PVCs
  2.  OpenShift events    — Warning events in namespace (last 1h)
  3.  Patroni API state   — /patroni + /cluster + /history + /config (via oc exec)
  4.  Patroni logs        — last N lines from each Patroni pod
  5.  PostgreSQL state    — pg_stat_replication, slots, activity, settings, locks,
                            database sizes, bloat indicators, WAL position, bgwriter
  6.  PostgreSQL logs     — last N ERROR/FATAL/CHECKPOINT lines per pod
  7.  pgBackRest status   — pgbackrest info --output=json + archive status
  8.  pgBackRest logs     — recent lines from the pgBackRest repo/sidecar pod
  9.  PgBouncer stats     — SHOW POOLS; SHOW STATS; SHOW CLIENTS; SHOW CONFIG;
  10. PgBouncer logs      — recent lines from PgBouncer pods
  11. Prometheus metrics  — key PromQL queries for replication lag, CPU, memory,
                            connections, disk, pod restarts, WAL rate
  12. Resource usage      — pod CPU/memory from oc top pods
  13. Cluster config      — key pg_settings, patroni dynamic config

Output:
  ./uat_evidence_YYYYMMDDTHHMMSSZ/
    evidence_bundle.json        ← structured bundle (feed to AI assistant)
    patroni_logs/               ← raw log files per pod
    postgresql_logs/            ← raw log files per pod
    pgbackrest_logs/
    pgbouncer_logs/
    openshift/
    prometheus/
  ./uat_evidence_YYYYMMDDTHHMMSSZ.tar.gz
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Globals ───────────────────────────────────────────────────────────────────

COLLECTED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ERRORS: list[dict[str, str]] = []


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", file=sys.stderr, flush=True)


def err(section: str, msg: str) -> None:
    entry = {"section": section, "error": msg, "ts": ts()}
    ERRORS.append(entry)
    print(f"[{ts()}] WARN {section}: {msg}", file=sys.stderr, flush=True)


# ── Command execution helpers ─────────────────────────────────────────────────

def run(
    args: list[str],
    dry_run: bool = False,
    timeout: int = 45,
    stdin_text: str | None = None,
) -> tuple[str, str, int]:
    """Run a command. Returns (stdout, stderr, returncode). Never raises."""
    if dry_run:
        return "", f"DRY-RUN: {' '.join(args)}", 0
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input=stdin_text.encode() if stdin_text else None,
            timeout=timeout,
        )
        return (
            result.stdout.decode("utf-8", errors="replace"),
            result.stderr.decode("utf-8", errors="replace"),
            result.returncode,
        )
    except subprocess.TimeoutExpired:
        return "", f"TIMEOUT after {timeout}s: {' '.join(args)}", 124
    except FileNotFoundError:
        return "", f"Command not found: {args[0]}", 127
    except Exception as exc:
        return "", f"Exception: {exc}", 1


def oc(ctx: str | None, *args: str, dry_run: bool = False, timeout: int = 45) -> tuple[str, str, int]:
    cmd = ["oc"]
    if ctx:
        cmd += ["--context", ctx]
    cmd += list(args)
    return run(cmd, dry_run=dry_run, timeout=timeout)


def oc_exec(
    ctx: str | None,
    namespace: str,
    pod: str,
    container: str | None,
    command: str,
    dry_run: bool = False,
    timeout: int = 60,
) -> tuple[str, str, int]:
    cmd = ["oc"]
    if ctx:
        cmd += ["--context", ctx]
    cmd += ["exec", "-n", namespace, pod]
    if container:
        cmd += ["-c", container]
    cmd += ["--", "bash", "-c", command]
    return run(cmd, dry_run=dry_run, timeout=timeout)


def oc_exec_stdin(
    ctx: str | None,
    namespace: str,
    pod: str,
    container: str | None,
    stdin_text: str,
    dry_run: bool = False,
    timeout: int = 60,
) -> tuple[str, str, int]:
    cmd = ["oc"]
    if ctx:
        cmd += ["--context", ctx]
    cmd += ["exec", "-i", "-n", namespace, pod]
    if container:
        cmd += ["-c", container]
    cmd += ["--"]
    return run(cmd, dry_run=dry_run, stdin_text=stdin_text, timeout=timeout)


def try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return text.strip()


def save_raw(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── Section 1: Cluster metadata ───────────────────────────────────────────────

def collect_cluster_metadata(cfg: dict, dry_run: bool) -> dict:
    log("Section 1: Cluster metadata")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    result: dict[str, Any] = {}

    # Namespace
    stdout, stderr, rc = oc(ctx, "get", "namespace", ns, "-o", "json", dry_run=dry_run)
    if rc == 0:
        d = try_json(stdout)
        if isinstance(d, dict):
            result["namespace_status"] = d.get("status", {})
            result["namespace_labels"] = d.get("metadata", {}).get("labels", {})
    else:
        err("cluster_metadata/namespace", stderr[:300])

    # PostgresCluster CR
    stdout, stderr, rc = oc(
        ctx, "get", "postgrescluster", cfg["cluster"], "-n", ns, "-o", "json",
        dry_run=dry_run, timeout=30,
    )
    if rc == 0:
        d = try_json(stdout)
        if isinstance(d, dict):
            spec = d.get("spec", {})
            result["postgrescluster_cr"] = {
                "pg_version": spec.get("postgresVersion"),
                "instances": [
                    {
                        "name": inst.get("name"),
                        "replicas": inst.get("replicas"),
                        "resources": inst.get("resources", {}),
                        "affinity": "set" if inst.get("affinity") else "not_set",
                    }
                    for inst in spec.get("instances", [])
                ],
                "patroni": spec.get("patroni", {}),
                "standby": spec.get("standby"),
                "status": {
                    "postgres_version": d.get("status", {}).get("postgresVersion"),
                    "conditions": [
                        {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason"), "message": c.get("message", "")[:200]}
                        for c in d.get("status", {}).get("conditions", [])
                    ],
                },
            }
    else:
        err("cluster_metadata/postgrescluster", stderr[:300])

    # Pods
    stdout, stderr, rc = oc(ctx, "get", "pods", "-n", ns, "-o", "json", dry_run=dry_run)
    if rc == 0:
        d = try_json(stdout)
        pods = []
        if isinstance(d, dict):
            for item in d.get("items", []):
                meta = item.get("metadata", {})
                spec = item.get("spec", {})
                status = item.get("status", {})
                containers = []
                for cs in status.get("containerStatuses", []):
                    state = cs.get("state", {})
                    last_state = cs.get("lastState", {})
                    state_key = next(iter(state), "unknown")
                    containers.append({
                        "name": cs.get("name"),
                        "ready": cs.get("ready"),
                        "restart_count": cs.get("restartCount", 0),
                        "state": state_key,
                        "image": cs.get("image", "").split("/")[-1][:60],
                        "last_terminated_reason": (
                            last_state.get("terminated", {}).get("reason")
                            if last_state.get("terminated") else None
                        ),
                    })
                pods.append({
                    "name": meta.get("name"),
                    "phase": status.get("phase"),
                    "node": spec.get("nodeName"),
                    "start_time": status.get("startTime"),
                    "labels": {
                        k: v for k, v in meta.get("labels", {}).items()
                        if k in ("postgres-operator.crunchydata.com/role",
                                 "postgres-operator.crunchydata.com/cluster",
                                 "app.kubernetes.io/name",
                                 "app.kubernetes.io/component")
                    },
                    "containers": containers,
                    "conditions": [
                        {"type": c.get("type"), "status": c.get("status")}
                        for c in status.get("conditions", [])
                    ],
                })
        result["pods"] = pods
        cfg["_pods"] = pods  # cache for later sections
    else:
        err("cluster_metadata/pods", stderr[:300])
        cfg["_pods"] = []

    # PVCs
    stdout, stderr, rc = oc(ctx, "get", "pvc", "-n", ns, "-o", "json", dry_run=dry_run)
    if rc == 0:
        d = try_json(stdout)
        pvcs = []
        if isinstance(d, dict):
            for item in d.get("items", []):
                meta = item.get("metadata", {})
                spec = item.get("spec", {})
                status = item.get("status", {})
                pvcs.append({
                    "name": meta.get("name"),
                    "phase": status.get("phase"),
                    "capacity": status.get("capacity", {}).get("storage"),
                    "requested": spec.get("resources", {}).get("requests", {}).get("storage"),
                    "storageclass": spec.get("storageClassName"),
                    "access_modes": spec.get("accessModes", []),
                })
        result["pvcs"] = pvcs
    else:
        err("cluster_metadata/pvcs", stderr[:300])

    # Services
    stdout, stderr, rc = oc(ctx, "get", "svc", "-n", ns, "-o", "json", dry_run=dry_run)
    if rc == 0:
        d = try_json(stdout)
        svcs = []
        if isinstance(d, dict):
            for item in d.get("items", []):
                meta = item.get("metadata", {})
                spec = item.get("spec", {})
                svcs.append({
                    "name": meta.get("name"),
                    "type": spec.get("type"),
                    "cluster_ip": spec.get("clusterIP"),
                    "ports": [{"port": p.get("port"), "target": p.get("targetPort"), "name": p.get("name")} for p in spec.get("ports", [])],
                })
        result["services"] = svcs
    else:
        err("cluster_metadata/services", stderr[:300])

    return result


# ── Section 2: OpenShift events ───────────────────────────────────────────────

def collect_openshift_events(cfg: dict, out_dir: Path, dry_run: bool) -> list[dict]:
    log("Section 2: OpenShift Warning events")
    ns = cfg["namespace"]
    ctx = cfg["context"]

    stdout, stderr, rc = oc(ctx, "get", "events", "-n", ns, "-o", "json", dry_run=dry_run, timeout=30)
    if rc != 0:
        err("openshift_events", stderr[:300])
        return []

    save_raw(out_dir / "openshift" / "events.json", stdout)
    d = try_json(stdout)
    events = []
    if isinstance(d, dict):
        for item in d.get("items", []):
            if item.get("type") != "Warning":
                continue
            events.append({
                "timestamp": item.get("lastTimestamp") or item.get("eventTime"),
                "reason": item.get("reason"),
                "message": item.get("message", "")[:300],
                "object_kind": item.get("involvedObject", {}).get("kind"),
                "object_name": item.get("involvedObject", {}).get("name"),
                "count": item.get("count", 1),
                "source": item.get("source", {}).get("component"),
            })
    events.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return events[:50]  # latest 50 Warning events


# ── Section 3: Patroni API state ──────────────────────────────────────────────

def _patroni_pods(cfg: dict) -> list[str]:
    """Return pod names that are Patroni/PostgreSQL instance pods."""
    cluster = cfg["cluster"]
    pods = cfg.get("_pods", [])
    result = []
    for pod in pods:
        name = pod.get("name", "")
        labels = pod.get("labels", {})
        role = labels.get("postgres-operator.crunchydata.com/role", "")
        is_cluster = labels.get("postgres-operator.crunchydata.com/cluster") == cluster
        # PGO instance pods have role=primary or role=replica
        if is_cluster and role in ("primary", "replica", ""):
            if any(seg in name for seg in ["-dc1-", "-dc2-", cluster]):
                result.append(name)
    # fallback: any pod with cluster name prefix that looks like an instance
    if not result:
        for pod in pods:
            name = pod.get("name", "")
            if cluster in name and not any(
                skip in name for skip in ["pgbackrest", "pgbouncer", "monitor", "prometheus", "inspector"]
            ):
                result.append(name)
    return result


def collect_patroni_api(cfg: dict, out_dir: Path, dry_run: bool) -> dict:
    log("Section 3: Patroni API state (via oc exec)")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    patroni_pods = _patroni_pods(cfg)

    if not patroni_pods:
        err("patroni_api", "No Patroni instance pods found — check _pods detection logic")
        return {}

    # Try each pod until one responds
    primary_pod = patroni_pods[0]
    result: dict[str, Any] = {"pods_tried": patroni_pods}

    patroni_endpoints = ["/patroni", "/cluster", "/history", "/config", "/liveness", "/readiness"]

    for pod in patroni_pods:
        log(f"  Querying Patroni API on pod {pod}")
        pod_data: dict[str, Any] = {}
        any_success = False

        for endpoint in patroni_endpoints:
            script = f"curl -s --max-time 5 http://localhost:8008{endpoint}"
            stdout, stderr, rc = oc_exec(ctx, ns, pod, "database", script, dry_run=dry_run, timeout=20)
            if rc == 0 and stdout.strip():
                pod_data[endpoint] = try_json(stdout)
                any_success = True
            else:
                pod_data[endpoint] = {"error": stderr[:200] or f"rc={rc}"}

        result[pod] = pod_data

        # If /cluster worked, save as primary Patroni state
        if any_success and isinstance(pod_data.get("/patroni"), dict):
            result["_primary_pod"] = pod
            cfg["_patroni_leader_pod"] = pod
            primary_pod = pod
            break

    # Save raw
    save_raw(out_dir / "patroni" / "api_state.json", json.dumps(result, indent=2, default=str))

    # Extract compact summary
    leader_info = result.get(primary_pod, {}).get("/cluster", {})
    patroni_info = result.get(primary_pod, {}).get("/patroni", {})

    members = []
    if isinstance(leader_info, dict):
        for m in leader_info.get("members", []):
            members.append({
                "name": m.get("name"),
                "role": m.get("role"),
                "state": m.get("state"),
                "lag_in_mb": m.get("lag_in_mb"),
                "timeline": m.get("timeline"),
                "host": m.get("host"),
                "port": m.get("port"),
            })

    compact = {
        "scope": patroni_info.get("patroni", {}).get("scope") if isinstance(patroni_info, dict) else None,
        "state": patroni_info.get("state") if isinstance(patroni_info, dict) else None,
        "role": patroni_info.get("role") if isinstance(patroni_info, dict) else None,
        "timeline": patroni_info.get("timeline") if isinstance(patroni_info, dict) else None,
        "server_version": patroni_info.get("server_version") if isinstance(patroni_info, dict) else None,
        "members": members,
        "history": result.get(primary_pod, {}).get("/history"),
        "config_version": (
            result.get(primary_pod, {}).get("/config", {}).get("loop_wait")
            if isinstance(result.get(primary_pod, {}).get("/config"), dict) else None
        ),
        "queried_pod": primary_pod,
    }
    return compact


# ── Section 4: Patroni logs ───────────────────────────────────────────────────

def collect_patroni_logs(cfg: dict, out_dir: Path, dry_run: bool) -> list[dict]:
    log("Section 4: Patroni logs")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    lines = cfg["log_lines"]
    patroni_pods = _patroni_pods(cfg)
    evidence = []

    for pod in patroni_pods[:4]:  # max 4 pods
        log(f"  Fetching Patroni logs from {pod}")
        stdout, stderr, rc = oc(
            ctx, "logs", pod, "-n", ns, "-c", "database",
            f"--tail={lines}", "--timestamps",
            dry_run=dry_run, timeout=60,
        )
        raw = stdout or stderr
        save_raw(out_dir / "patroni_logs" / f"{pod}.log", raw)

        # Extract key events (leader changes, errors, restarts)
        key_patterns = [
            "promoted self", "demoting self", "became leader", "starting as",
            "failover", "switchover", "dcs lost", "cannot take the leader lock",
            "timeline", "reload", "restart", "error", "exception", "traceback",
            "no master", "member", "raft", "etcd", "lost connection",
        ]
        key_lines = []
        for line in raw.splitlines():
            lower = line.lower()
            if any(pat in lower for pat in key_patterns):
                key_lines.append(line[:300])

        evidence.append({
            "pod": pod,
            "total_lines": len(raw.splitlines()),
            "key_event_lines": key_lines[:50],
            "log_file": f"patroni_logs/{pod}.log",
        })

    return evidence


# ── Section 5: PostgreSQL state ───────────────────────────────────────────────

_PG_QUERIES = {
    "pg_stat_replication": r"""
SELECT application_name,
       client_addr::text,
       state,
       sync_state,
       CASE WHEN pg_is_in_recovery() THEN NULL
            ELSE pg_wal_lsn_diff(pg_current_wal_lsn(), sent_lsn) END AS lag_sent_bytes,
       CASE WHEN pg_is_in_recovery() THEN NULL
            ELSE pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) END AS lag_replay_bytes,
       write_lag::text,
       flush_lag::text,
       replay_lag::text
FROM pg_stat_replication
ORDER BY application_name;
""",
    "pg_replication_slots": r"""
SELECT slot_name,
       plugin,
       slot_type,
       active,
       pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS lag_bytes,
       restart_lsn::text,
       confirmed_flush_lsn::text
FROM pg_replication_slots
ORDER BY slot_name;
""",
    "pg_stat_activity_summary": r"""
SELECT state,
       wait_event_type,
       wait_event,
       count(*) AS count,
       max(EXTRACT(EPOCH FROM (now() - state_change))) AS max_duration_sec
FROM pg_stat_activity
WHERE pid != pg_backend_pid()
GROUP BY state, wait_event_type, wait_event
ORDER BY count DESC
LIMIT 30;
""",
    "blocking_locks": r"""
SELECT
    blocked.pid AS blocked_pid,
    blocked.query AS blocked_query,
    blocking.pid AS blocking_pid,
    blocking.query AS blocking_query,
    blocked_locks.locktype,
    now() - blocked_activity.query_start AS blocked_duration
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks
    ON blocking_locks.locktype = blocked_locks.locktype
    AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
    AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
    AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
    AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
    AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
    AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
    AND blocking_locks.pid != blocked_locks.pid
JOIN pg_catalog.pg_stat_activity blocking ON blocking.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted
ORDER BY blocked_duration DESC NULLS LAST
LIMIT 20;
""",
    "pg_stat_database": r"""
SELECT datname,
       numbackends,
       xact_commit,
       xact_rollback,
       blks_hit,
       blks_read,
       tup_returned,
       tup_fetched,
       tup_inserted,
       tup_updated,
       tup_deleted,
       conflicts,
       deadlocks,
       temp_files,
       temp_bytes
FROM pg_stat_database
WHERE datname NOT IN ('template0', 'template1')
ORDER BY numbackends DESC
LIMIT 15;
""",
    "key_pg_settings": r"""
SELECT name,
       setting,
       unit,
       context,
       pending_restart,
       source
FROM pg_settings
WHERE name = ANY(ARRAY[
    'max_connections','shared_buffers','work_mem','effective_cache_size',
    'maintenance_work_mem','wal_level','max_wal_senders','max_replication_slots',
    'wal_keep_size','max_wal_size','min_wal_size','checkpoint_completion_target',
    'archive_mode','archive_command','hot_standby','hot_standby_feedback',
    'log_min_duration_statement','log_lock_waits','log_checkpoints',
    'log_autovacuum_min_duration','autovacuum','autovacuum_max_workers',
    'autovacuum_vacuum_cost_delay','synchronous_commit','synchronous_standby_names',
    'recovery_min_apply_delay','wal_receiver_timeout','wal_sender_timeout',
    'max_standby_streaming_delay','max_standby_archive_delay',
    'statement_timeout','idle_in_transaction_session_timeout',
    'track_commit_timestamp','track_activity_query_size'
])
ORDER BY name;
""",
    "wal_position": r"""
SELECT pg_is_in_recovery() AS is_replica,
       CASE WHEN pg_is_in_recovery()
            THEN pg_last_wal_receive_lsn()::text
            ELSE pg_current_wal_lsn()::text
       END AS current_lsn,
       CASE WHEN pg_is_in_recovery()
            THEN pg_last_wal_replay_lsn()::text
            ELSE NULL
       END AS replay_lsn,
       CASE WHEN pg_is_in_recovery()
            THEN EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))
            ELSE NULL
       END AS replica_lag_seconds,
       pg_postmaster_start_time()::text AS postmaster_start,
       version() AS pg_version;
""",
    "pg_stat_bgwriter": r"""
SELECT checkpoints_timed,
       checkpoints_req,
       checkpoint_write_time,
       checkpoint_sync_time,
       buffers_checkpoint,
       buffers_clean,
       buffers_backend,
       buffers_alloc,
       stats_reset::text
FROM pg_stat_bgwriter;
""",
    "table_bloat_top10": r"""
SELECT schemaname,
       relname AS tablename,
       n_dead_tup,
       n_live_tup,
       CASE WHEN n_live_tup > 0
            THEN round(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 1)
            ELSE 0 END AS dead_pct,
       last_vacuum::text,
       last_autovacuum::text,
       last_analyze::text,
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||relname)) AS total_size
FROM pg_stat_user_tables
WHERE n_live_tup + n_dead_tup > 1000
ORDER BY n_dead_tup DESC
LIMIT 15;
""",
    "database_sizes": r"""
SELECT datname,
       pg_size_pretty(pg_database_size(datname)) AS size_pretty,
       pg_database_size(datname) AS size_bytes
FROM pg_database
WHERE datname NOT IN ('template0', 'template1')
ORDER BY pg_database_size(datname) DESC;
""",
    "long_running_queries": r"""
SELECT pid,
       usename,
       state,
       wait_event_type,
       wait_event,
       EXTRACT(EPOCH FROM (now() - query_start)) AS duration_sec,
       LEFT(query, 200) AS query_truncated
FROM pg_stat_activity
WHERE state != 'idle'
  AND query_start < now() - interval '60 seconds'
  AND pid != pg_backend_pid()
ORDER BY duration_sec DESC
LIMIT 20;
""",
    "autovacuum_running": r"""
SELECT pid,
       usename,
       EXTRACT(EPOCH FROM (now() - query_start)) AS duration_sec,
       LEFT(query, 300) AS query
FROM pg_stat_activity
WHERE query LIKE 'autovacuum:%'
ORDER BY duration_sec DESC
LIMIT 10;
""",
}


def collect_postgresql_state(cfg: dict, out_dir: Path, dry_run: bool) -> dict:
    log("Section 5: PostgreSQL read-only queries")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    pg_port = cfg["pg_port"]
    results: dict[str, Any] = {}

    # Find the primary pod (look for Patroni leader pod set in section 3)
    leader_pod = cfg.get("_patroni_leader_pod")
    if not leader_pod:
        # Fallback: find primary by label
        patroni_pods = _patroni_pods(cfg)
        leader_pod = patroni_pods[0] if patroni_pods else None

    if not leader_pod:
        err("postgresql_state", "No primary pod found")
        return results

    log(f"  Running pg queries on pod {leader_pod} port {pg_port}")

    # Build one big SQL block to run in a single psql call
    parts = []
    for qname, sql in _PG_QUERIES.items():
        parts.append(f"\\echo ===BEGIN:{qname}===")
        parts.append(sql.strip())
        parts.append(f"\\echo ===END:{qname}===")
    full_sql = "\n".join(parts)

    psql_cmd = (
        f"psql -p {pg_port} -U postgres "
        f"-c 'SET statement_timeout=15000;' "
        f"--pset=format=json --no-psqlrc -f -"
    )
    stdout, stderr, rc = oc_exec(
        ctx, ns, leader_pod, "database",
        f"bash -c \"{psql_cmd}\"",
        dry_run=dry_run,
        timeout=90,
    )

    if rc != 0:
        # Try alternative: use psql with -c for each query
        log(f"  Single-call psql failed (rc={rc}), trying per-query approach")
        for qname, sql in _PG_QUERIES.items():
            clean_sql = sql.strip().replace("'", "'\\''")
            cmd = f"psql -p {pg_port} -U postgres --pset=format=json --no-psqlrc -c $'{clean_sql}' 2>&1 | head -200"
            qstdout, qstderr, qrc = oc_exec(ctx, ns, leader_pod, "database", cmd, dry_run=dry_run, timeout=20)
            results[qname] = try_json(qstdout) if qrc == 0 else {"error": qstderr[:200]}
    else:
        # Parse combined output
        current_query = None
        current_lines: list[str] = []
        for line in (stdout + "\n" + stderr).splitlines():
            begin_m = re.match(r"===BEGIN:(\w+)===", line)
            end_m = re.match(r"===END:(\w+)===", line)
            if begin_m:
                current_query = begin_m.group(1)
                current_lines = []
            elif end_m and current_query:
                block = "\n".join(current_lines)
                results[current_query] = try_json(block) if block.strip() else {"error": "no output"}
                current_query = None
            elif current_query:
                current_lines.append(line)

    save_raw(out_dir / "postgresql" / "queries.json", json.dumps(results, indent=2, default=str))
    return results


# ── Section 6: PostgreSQL logs ────────────────────────────────────────────────

def collect_postgresql_logs(cfg: dict, out_dir: Path, dry_run: bool) -> list[dict]:
    log("Section 6: PostgreSQL log evidence")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    lines = cfg["log_lines"]
    patroni_pods = _patroni_pods(cfg)
    evidence = []

    interesting_patterns = [
        "ERROR", "FATAL", "PANIC",
        "DETAIL:", "HINT:",
        "duration:", "checkpoint",
        "autovacuum:", "lock waits",
        "recovery conflict",
        "replication", "standby",
        "archive", "archive_status",
        "out of memory", "OOM",
        "deadlock",
        "terminating connection",
        "canceling statement",
    ]

    for pod in patroni_pods[:4]:
        log(f"  Fetching PostgreSQL logs from {pod}")
        # Try container name "database" (standard PGO naming)
        for container in ["database", "postgres"]:
            stdout, stderr, rc = oc(
                ctx, "logs", pod, "-n", ns, "-c", container,
                f"--tail={lines}", "--timestamps", "--since=24h",
                dry_run=dry_run, timeout=90,
            )
            if rc == 0 and stdout.strip():
                break
        else:
            stdout, stderr = "", stderr

        raw = stdout or stderr
        save_raw(out_dir / "postgresql_logs" / f"{pod}.log", raw)

        key_lines = []
        for line in raw.splitlines():
            if any(pat.lower() in line.lower() for pat in interesting_patterns):
                key_lines.append(line[:300])

        evidence.append({
            "pod": pod,
            "total_lines": len(raw.splitlines()),
            "key_event_lines": key_lines[:60],
            "log_file": f"postgresql_logs/{pod}.log",
        })

    return evidence


# ── Section 7: pgBackRest status ──────────────────────────────────────────────

def collect_pgbackrest_status(cfg: dict, out_dir: Path, dry_run: bool) -> dict:
    log("Section 7: pgBackRest status")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    stanza = cfg.get("pgbackrest_stanza", "db")
    result: dict[str, Any] = {}

    # Find pgBackRest repo pod
    pgbackrest_pods = [
        p["name"] for p in cfg.get("_pods", [])
        if "pgbackrest" in p.get("name", "").lower() or
           "pgbackrest" in str(p.get("labels", {})).lower()
    ]

    # Also try running pgbackrest from the primary postgres pod
    target_pod = pgbackrest_pods[0] if pgbackrest_pods else cfg.get("_patroni_leader_pod")
    if not target_pod:
        err("pgbackrest_status", "No pgBackRest or primary pod found")
        return {}

    log(f"  Running pgbackrest info on pod {target_pod}")

    # pgbackrest info
    cmd = f"pgbackrest info --stanza={stanza} --output=json 2>&1 || pgbackrest info --output=json 2>&1"
    stdout, stderr, rc = oc_exec(ctx, ns, target_pod, None, cmd, dry_run=dry_run, timeout=60)
    raw = stdout or stderr
    save_raw(out_dir / "pgbackrest" / "info.json", raw)
    pgbr_data = try_json(raw.strip()) if raw.strip() else {"error": "no output"}

    if isinstance(pgbr_data, list) and pgbr_data:
        stanza_info = pgbr_data[0]
        backups = stanza_info.get("backup", [])

        last_full = last_diff = last_incr = None
        for b in reversed(backups):
            btype = b.get("type")
            btime = b.get("timestamp", {}).get("stop")
            if btype == "full" and not last_full:
                last_full = btime
            elif btype == "diff" and not last_diff:
                last_diff = btime
            elif btype == "incr" and not last_incr:
                last_incr = btime

        # Check staleness (>48h = stale)
        stale = False
        if last_full:
            try:
                age_hours = (time.time() - last_full) / 3600
                stale = age_hours > 48
            except Exception:
                pass

        result = {
            "stanza": stanza_info.get("name"),
            "status": stanza_info.get("status", {}).get("message"),
            "repo_count": len(stanza_info.get("repo", [])),
            "repos": [
                {
                    "key": r.get("key"),
                    "cipher": r.get("cipher"),
                    "status": r.get("status", {}).get("message"),
                }
                for r in stanza_info.get("repo", [])
            ],
            "backup_count": len(backups),
            "last_full_backup_ts": last_full,
            "last_diff_backup_ts": last_diff,
            "last_incr_backup_ts": last_incr,
            "stale_full_backup": stale,
            "last_5_backups": [
                {
                    "label": b.get("label"),
                    "type": b.get("type"),
                    "start": b.get("timestamp", {}).get("start"),
                    "stop": b.get("timestamp", {}).get("stop"),
                    "error": b.get("error"),
                    "size": b.get("info", {}).get("size"),
                    "delta": b.get("info", {}).get("delta"),
                }
                for b in list(reversed(backups))[:5]
            ],
        }

    # Archive check
    cmd2 = f"pgbackrest check --stanza={stanza} 2>&1 | tail -20"
    stdout2, _, _ = oc_exec(ctx, ns, target_pod, None, cmd2, dry_run=dry_run, timeout=30)
    result["archive_check"] = stdout2.strip()[:500]

    return result


# ── Section 8: pgBackRest logs ────────────────────────────────────────────────

def collect_pgbackrest_logs(cfg: dict, out_dir: Path, dry_run: bool) -> list[dict]:
    log("Section 8: pgBackRest logs")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    lines = cfg["log_lines"]
    evidence = []

    pgbackrest_pods = [
        p["name"] for p in cfg.get("_pods", [])
        if "pgbackrest" in p.get("name", "").lower()
    ]

    for pod in pgbackrest_pods[:2]:
        log(f"  Fetching pgBackRest logs from {pod}")
        stdout, stderr, rc = oc(
            ctx, "logs", pod, "-n", ns, f"--tail={lines}", "--timestamps", "--since=48h",
            dry_run=dry_run, timeout=60,
        )
        raw = stdout or stderr
        save_raw(out_dir / "pgbackrest_logs" / f"{pod}.log", raw)

        key_lines = [l[:300] for l in raw.splitlines() if
                     any(p in l.lower() for p in ["error", "fatal", "warn", "failed", "backup", "archive", "expire"])]
        evidence.append({
            "pod": pod,
            "total_lines": len(raw.splitlines()),
            "key_event_lines": key_lines[:40],
            "log_file": f"pgbackrest_logs/{pod}.log",
        })

    return evidence


# ── Section 9: PgBouncer stats ────────────────────────────────────────────────

_PGBOUNCER_QUERIES = [
    "SHOW POOLS;",
    "SHOW STATS;",
    "SHOW CLIENTS;",
    "SHOW CONFIG;",
    "SHOW SERVERS;",
    "SHOW LISTS;",
]


def collect_pgbouncer_stats(cfg: dict, out_dir: Path, dry_run: bool) -> dict:
    log("Section 9: PgBouncer stats")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    pgbouncer_port = cfg["pgbouncer_port"]
    results: dict[str, Any] = {}

    pgbouncer_pods = [
        p["name"] for p in cfg.get("_pods", [])
        if "pgbouncer" in p.get("name", "").lower() or
           "pgbouncer" in str(p.get("labels", {})).lower()
    ]

    if not pgbouncer_pods:
        err("pgbouncer_stats", "No PgBouncer pods found")
        return {"error": "No PgBouncer pods found in namespace"}

    pod = pgbouncer_pods[0]
    log(f"  Querying PgBouncer on pod {pod}")

    for query in _PGBOUNCER_QUERIES:
        qname = query.replace("SHOW ", "").replace(";", "").lower()
        cmd = f"psql -p {pgbouncer_port} -U pgbouncer pgbouncer -c '{query}' 2>&1"
        stdout, stderr, rc = oc_exec(ctx, ns, pod, "pgbouncer", cmd, dry_run=dry_run, timeout=15)
        results[qname] = stdout.strip()[:2000] if rc == 0 else {"error": stderr[:200]}

    save_raw(out_dir / "pgbouncer" / "stats.json", json.dumps(results, indent=2, default=str))
    return results


# ── Section 10: PgBouncer logs ────────────────────────────────────────────────

def collect_pgbouncer_logs(cfg: dict, out_dir: Path, dry_run: bool) -> list[dict]:
    log("Section 10: PgBouncer logs")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    lines = cfg["log_lines"]
    evidence = []

    pgbouncer_pods = [
        p["name"] for p in cfg.get("_pods", [])
        if "pgbouncer" in p.get("name", "").lower()
    ]

    for pod in pgbouncer_pods[:2]:
        stdout, stderr, rc = oc(
            ctx, "logs", pod, "-n", ns, "-c", "pgbouncer",
            f"--tail={lines}", "--timestamps", "--since=24h",
            dry_run=dry_run, timeout=60,
        )
        raw = stdout or stderr
        save_raw(out_dir / "pgbouncer_logs" / f"{pod}.log", raw)
        key_lines = [l[:300] for l in raw.splitlines() if
                     any(p in l.lower() for p in ["error", "fatal", "closing", "timeout", "too many"])]
        evidence.append({
            "pod": pod,
            "key_event_lines": key_lines[:30],
            "log_file": f"pgbouncer_logs/{pod}.log",
        })

    return evidence


# ── Section 11: Prometheus metrics ───────────────────────────────────────────

_PROMQL_QUERIES: dict[str, str] = {
    "replication_lag_bytes":    'pg_replication_lag_bytes{{namespace="{ns}"}}',
    "replication_lag_seconds":  'pg_replication_lag{{namespace="{ns}"}}',
    "connection_count":         'pg_stat_activity_count{{namespace="{ns}"}}',
    "max_connections":          'pg_settings_max_connections{{namespace="{ns}"}}',
    "connection_utilization_pct": '100 * pg_stat_activity_count{{namespace="{ns}"}} / pg_settings_max_connections{{namespace="{ns}"}}',
    "cpu_usage_cores":          'rate(container_cpu_usage_seconds_total{{namespace="{ns}"}}[5m])',
    "memory_bytes":             'container_memory_working_set_bytes{{namespace="{ns}",container!=""}}',
    "pod_restart_count":        'kube_pod_container_status_restarts_total{{namespace="{ns}"}}',
    "wal_generation_rate":      'rate(pg_xlog_current_lsn{{namespace="{ns}"}}[5m])',
    "checkpoints_timed":        'increase(pg_stat_bgwriter_checkpoints_timed_total{{namespace="{ns}"}}[1h])',
    "checkpoints_requested":    'increase(pg_stat_bgwriter_checkpoints_req_total{{namespace="{ns}"}}[1h])',
    "deadlocks_per_min":        'rate(pg_stat_database_deadlocks_total{{namespace="{ns}"}}[5m]) * 60',
    "temp_bytes":               'pg_stat_database_temp_bytes{{namespace="{ns}"}}',
    "disk_usage_bytes":         'kubelet_volume_stats_used_bytes{{namespace="{ns}"}}',
    "pgbouncer_active_clients": 'pgbouncer_pools_sv_active{{namespace="{ns}"}}',
    "pgbouncer_waiting_clients":'pgbouncer_pools_cl_waiting{{namespace="{ns}"}}',
}


def _http_get(url: str, token: str = "", timeout: int = 10) -> tuple[dict, str]:
    import urllib.request
    import urllib.parse
    import ssl
    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            body = resp.read(500_000).decode("utf-8", errors="replace")
            return json.loads(body), ""
    except Exception as exc:
        return {}, str(exc)[:300]


def collect_prometheus_metrics(cfg: dict, out_dir: Path, dry_run: bool) -> dict:
    log("Section 11: Prometheus metrics")
    ns = cfg["namespace"]
    prom_url = cfg.get("prometheus_url", "").rstrip("/")
    results: dict[str, Any] = {}

    if not prom_url:
        log("  No --prometheus-url provided; trying default svc URL")
        prom_url = f"http://uat-pgo18-prometheus.{ns}.svc:9090"

    if dry_run:
        return {"dry_run": True, "prometheus_url": prom_url}

    # Readiness check
    ready_data, ready_err = _http_get(f"{prom_url}/-/ready", timeout=8)
    if ready_err:
        err("prometheus", f"Prometheus not reachable at {prom_url}: {ready_err}")
        # Try cluster-internal via oc exec on app pod
        return {"error": f"Prometheus unreachable: {ready_err}", "url_tried": prom_url}

    results["prometheus_url"] = prom_url
    results["metrics"] = {}

    for metric_name, promql_template in _PROMQL_QUERIES.items():
        promql = promql_template.format(ns=ns)
        url = f"{prom_url}/api/v1/query?query={urllib.parse.quote(promql)}" if "urllib" in dir() else f"{prom_url}/api/v1/query?query={promql}"
        # Use urllib safely
        import urllib.parse as _urlparse
        url = f"{prom_url}/api/v1/query?query={_urlparse.quote(promql)}"
        data, err_msg = _http_get(url, timeout=10)
        if err_msg:
            results["metrics"][metric_name] = {"error": err_msg}
            continue
        result_list = data.get("data", {}).get("result", [])
        results["metrics"][metric_name] = [
            {
                "labels": r.get("metric", {}),
                "value": r.get("value", [None, None])[1],
            }
            for r in result_list[:20]
        ]

    save_raw(out_dir / "prometheus" / "metrics.json", json.dumps(results, indent=2, default=str))
    return results


# ── Section 12: Resource usage ────────────────────────────────────────────────

def collect_resource_usage(cfg: dict, out_dir: Path, dry_run: bool) -> dict:
    log("Section 12: oc top pods")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    result: dict[str, Any] = {}

    stdout, stderr, rc = oc(ctx, "top", "pods", "-n", ns, "--no-headers", dry_run=dry_run, timeout=30)
    raw = stdout or stderr
    save_raw(out_dir / "openshift" / "top_pods.txt", raw)

    rows = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            rows.append({"pod": parts[0], "cpu": parts[1], "memory": parts[2]})
    result["top_pods"] = rows

    # Node-level if accessible
    stdout2, _, rc2 = oc(ctx, "top", "nodes", "--no-headers", dry_run=dry_run, timeout=30)
    save_raw(out_dir / "openshift" / "top_nodes.txt", stdout2)
    result["top_nodes"] = stdout2.strip()[:1000]

    return result


# ── Section 13: Cluster configuration ────────────────────────────────────────

def collect_cluster_config(cfg: dict, out_dir: Path, dry_run: bool) -> dict:
    log("Section 13: Cluster configuration (Patroni dynamic config + critical pg_settings)")
    ns = cfg["namespace"]
    ctx = cfg["context"]
    result: dict[str, Any] = {}

    # Patroni config from DCS (via pod exec /config endpoint)
    leader_pod = cfg.get("_patroni_leader_pod")
    if leader_pod:
        stdout, stderr, rc = oc_exec(
            ctx, ns, leader_pod, "database",
            "curl -s http://localhost:8008/config 2>&1",
            dry_run=dry_run, timeout=15,
        )
        result["patroni_dynamic_config"] = try_json(stdout) if rc == 0 else {"error": stderr[:200]}

    # Patroni configmap (static config)
    stdout, stderr, rc = oc(
        ctx, "get", "configmap", "-n", ns,
        "-l", f"postgres-operator.crunchydata.com/cluster={cfg['cluster']}",
        "-o", "json", dry_run=dry_run, timeout=20,
    )
    if rc == 0:
        d = try_json(stdout)
        cms = []
        if isinstance(d, dict):
            for item in d.get("items", []):
                cms.append({
                    "name": item.get("metadata", {}).get("name"),
                    "data_keys": list((item.get("data") or {}).keys()),
                })
        result["patroni_configmaps"] = cms

    save_raw(out_dir / "openshift" / "cluster_config.json", json.dumps(result, indent=2, default=str))
    return result


# ── Bundle assembly ───────────────────────────────────────────────────────────

def assemble_bundle(cfg: dict, sections: dict) -> dict:
    """Assemble all collected data into the EvidencePack-compatible JSON structure."""
    return {
        "schema_version": "1.0",
        "extractor_version": "2026-06-21",
        "collected_at": COLLECTED_AT,
        "cluster": {
            "cluster_id": cfg["cluster_id"],
            "cluster_name": cfg["cluster"],
            "namespace": cfg["namespace"],
            "patroni_cluster": cfg["patroni_cluster"],
            "environment": cfg.get("environment", "uat"),
            "pg_port": cfg["pg_port"],
        },
        "sections": {
            "cluster_metadata":   sections.get("cluster_metadata", {}),
            "openshift_events":   sections.get("openshift_events", []),
            "patroni_state":      sections.get("patroni_state", {}),
            "patroni_logs":       sections.get("patroni_logs", []),
            "postgresql_state":   sections.get("postgresql_state", {}),
            "postgresql_logs":    sections.get("postgresql_logs", []),
            "pgbackrest_status":  sections.get("pgbackrest_status", {}),
            "pgbackrest_logs":    sections.get("pgbackrest_logs", []),
            "pgbouncer_stats":    sections.get("pgbouncer_stats", {}),
            "pgbouncer_logs":     sections.get("pgbouncer_logs", []),
            "prometheus_metrics": sections.get("prometheus_metrics", {}),
            "resource_usage":     sections.get("resource_usage", {}),
            "cluster_config":     sections.get("cluster_config", {}),
        },
        "collection_errors": ERRORS,
        "collection_summary": {
            "total_sections": 13,
            "sections_with_errors": len(set(e["section"].split("/")[0] for e in ERRORS)),
            "total_errors": len(ERRORS),
            "patroni_leader_pod": cfg.get("_patroni_leader_pod"),
            "pod_count": len(cfg.get("_pods", [])),
        },
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="UAT AI Evidence Extractor — collects all evidence for the AI DBA Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--namespace",        default="uat-pgcluster-uae",    help="OpenShift namespace")
    p.add_argument("--cluster",          default="uat-pgcluster-uae",    help="PostgresCluster name")
    p.add_argument("--cluster-id",       default="uat",                  help="Short cluster ID used in console (uat/prod/dr)")
    p.add_argument("--patroni-cluster",  default="uat-pgcluster-uae-ha", help="Patroni cluster scope name")
    p.add_argument("--environment",      default="uat",                  help="Environment label (uat/prod/dr)")
    p.add_argument("--context",          default=None,                   help="oc context (blank = current)")
    p.add_argument("--pg-port",          default="5555",                 help="PostgreSQL port inside pods")
    p.add_argument("--pgbouncer-port",   default="5432",                 help="PgBouncer admin port inside pods")
    p.add_argument("--pgbackrest-stanza",default="db",                   help="pgBackRest stanza name")
    p.add_argument("--prometheus-url",   default="",                     help="Prometheus URL (auto-detect if blank)")
    p.add_argument("--patroni-url",      default="",                     help="Patroni API URL (auto-detect via pod exec if blank)")
    p.add_argument("--log-lines",        default=300, type=int,          help="Log lines to collect per source")
    p.add_argument("--out-dir",          default="",                     help="Output directory (auto-named if blank)")
    p.add_argument("--dry-run",          action="store_true",            help="Print commands without executing")
    p.add_argument("--skip-sections",    default="",                     help="Comma-separated section numbers to skip (e.g. 11,12)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    out_dir_name = args.out_dir or f"uat_evidence_{COLLECTED_AT.replace(':', '').replace('-', '')}"
    out_dir = Path(out_dir_name)
    for subdir in [
        "patroni_logs", "postgresql_logs", "pgbackrest_logs", "pgbouncer_logs",
        "openshift", "prometheus", "patroni", "postgresql", "pgbackrest", "pgbouncer",
    ]:
        (out_dir / subdir).mkdir(parents=True, exist_ok=True)

    skip = set(args.skip_sections.split(",")) if args.skip_sections else set()

    cfg: dict[str, Any] = {
        "namespace":           args.namespace,
        "cluster":             args.cluster,
        "cluster_id":          args.cluster_id,
        "patroni_cluster":     args.patroni_cluster,
        "environment":         args.environment,
        "context":             args.context,
        "pg_port":             args.pg_port,
        "pgbouncer_port":      args.pgbouncer_port,
        "pgbackrest_stanza":   args.pgbackrest_stanza,
        "prometheus_url":      args.prometheus_url,
        "log_lines":           args.log_lines,
        "_pods":               [],
        "_patroni_leader_pod": None,
    }

    log(f"UAT AI Evidence Extractor — {COLLECTED_AT}")
    log(f"Namespace: {cfg['namespace']}  Cluster: {cfg['cluster']}")
    log(f"Output:    {out_dir}/")
    if args.dry_run:
        log("DRY-RUN mode — commands will be printed, not executed")

    sections: dict[str, Any] = {}

    if "1" not in skip:
        sections["cluster_metadata"]   = collect_cluster_metadata(cfg, args.dry_run)
    if "2" not in skip:
        sections["openshift_events"]   = collect_openshift_events(cfg, out_dir, args.dry_run)
    if "3" not in skip:
        sections["patroni_state"]      = collect_patroni_api(cfg, out_dir, args.dry_run)
    if "4" not in skip:
        sections["patroni_logs"]       = collect_patroni_logs(cfg, out_dir, args.dry_run)
    if "5" not in skip:
        sections["postgresql_state"]   = collect_postgresql_state(cfg, out_dir, args.dry_run)
    if "6" not in skip:
        sections["postgresql_logs"]    = collect_postgresql_logs(cfg, out_dir, args.dry_run)
    if "7" not in skip:
        sections["pgbackrest_status"]  = collect_pgbackrest_status(cfg, out_dir, args.dry_run)
    if "8" not in skip:
        sections["pgbackrest_logs"]    = collect_pgbackrest_logs(cfg, out_dir, args.dry_run)
    if "9" not in skip:
        sections["pgbouncer_stats"]    = collect_pgbouncer_stats(cfg, out_dir, args.dry_run)
    if "10" not in skip:
        sections["pgbouncer_logs"]     = collect_pgbouncer_logs(cfg, out_dir, args.dry_run)
    if "11" not in skip:
        sections["prometheus_metrics"] = collect_prometheus_metrics(cfg, out_dir, args.dry_run)
    if "12" not in skip:
        sections["resource_usage"]     = collect_resource_usage(cfg, out_dir, args.dry_run)
    if "13" not in skip:
        sections["cluster_config"]     = collect_cluster_config(cfg, out_dir, args.dry_run)

    log("Assembling evidence bundle")
    bundle = assemble_bundle(cfg, sections)
    bundle_path = out_dir / "evidence_bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
    log(f"Bundle written: {bundle_path}")

    # Summary
    log(f"\n{'='*60}")
    log("COLLECTION SUMMARY")
    log(f"{'='*60}")
    log(f"Sections collected:  {13 - len(skip)}/13")
    log(f"Total errors:        {len(ERRORS)}")
    log(f"Patroni leader pod:  {cfg.get('_patroni_leader_pod', 'not detected')}")
    log(f"Pods found:          {len(cfg.get('_pods', []))}")

    if ERRORS:
        log("\nErrors encountered (non-fatal):")
        for e in ERRORS:
            log(f"  [{e['section']}] {e['error'][:120]}")

    # Create tar.gz
    import tarfile
    archive_path = f"{out_dir_name}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(out_dir, arcname=out_dir.name)
    log(f"\nArchive: {archive_path}")
    log(f"Bundle:  {bundle_path}")
    log("\nTransfer to air-gapped AI console machine:")
    log(f"  scp {archive_path} <console-host>:~/")
    log(f"  # Load into AI assistant: place evidence_bundle.json in static evidence directory")


if __name__ == "__main__":
    main()
