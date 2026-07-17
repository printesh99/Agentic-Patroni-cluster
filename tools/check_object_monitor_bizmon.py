#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REMOTE_CHECK = r'''
import json
import os
import pathlib
import time

try:
    import psycopg
except Exception as exc:
    print(json.dumps({"error": "python import psycopg failed: %s" % exc}))
    raise SystemExit(0)


def getenv(name, default=None):
    return os.environ.get(name, default)


def masked(value):
    return "SET" if value else "UNSET"


def conn_kwargs(mode, dbname):
    if mode == "monitor":
        return {
            "host": getenv("MONITOR_PGHOST"),
            "port": getenv("MONITOR_PGPORT", "5432"),
            "dbname": dbname,
            "user": getenv("PGVIEW_USER") or getenv("MONITOR_PGUSER"),
            "password": getenv("PGVIEW_PASSWORD") or getenv("MONITOR_PGPASSWORD", ""),
            "sslmode": getenv("MONITOR_PGSSLMODE") or getenv("PGSSLMODE", "prefer"),
            "connect_timeout": 5,
            "application_name": "om-bizmon-check",
        }
    return {
        "host": getenv("PGHOST"),
        "port": getenv("PGPORT", "5432"),
        "dbname": dbname,
        "user": getenv("PGVIEW_USER") or getenv("PGUSER"),
        "password": getenv("PGVIEW_PASSWORD") or getenv("PGPASSWORD", ""),
        "sslmode": getenv("PGSSLMODE", "prefer"),
        "connect_timeout": 5,
        "application_name": "om-bizmon-check",
    }


def short_error(exc):
    return str(exc).splitlines()[0][:300]


def registry_dbs():
    paths = [
        "/opt/app-root/src/app/bizmon_panels.json",
        "/app/app/bizmon_panels.json",
    ]
    for path in paths:
        p = pathlib.Path(path)
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
        except Exception as exc:
            return path, [], "read failed: %s" % short_error(exc)
        dbs = set()
        for dash in data.get("dashboards", []):
            for row in dash.get("rows", []):
                for panel in row.get("panels", []):
                    db = panel.get("db")
                    if db:
                        dbs.add(db)
        return path, sorted(dbs), None
    return None, [], "bizmon_panels.json not found in known image paths"


def list_monitor_databases():
    kw = conn_kwargs("monitor", "postgres")
    if not kw["host"] or not kw["user"]:
        return [], "MONITOR_PGHOST or monitor user is unset"
    try:
        with psycopg.connect(**kw) as conn:
            with conn.cursor() as cur:
                cur.execute("select datname from pg_database where not datistemplate order by 1")
                return [row[0] for row in cur.fetchall()], None
    except Exception as exc:
        return [], short_error(exc)


def smoke(mode, databases):
    results = []
    for dbname in databases:
        kw = conn_kwargs(mode, dbname)
        safe = {
            "mode": mode,
            "db": dbname,
            "host": kw.get("host"),
            "port": kw.get("port"),
            "user": kw.get("user"),
            "password": masked(kw.get("password")),
            "sslmode": kw.get("sslmode"),
        }
        if not kw.get("host") or not kw.get("user"):
            safe["status"] = "FAIL"
            safe["error"] = "host or user is unset"
            results.append(safe)
            continue
        start = time.monotonic()
        try:
            with psycopg.connect(**kw) as conn:
                with conn.cursor() as cur:
                    cur.execute("select current_database(), current_user")
                    row = cur.fetchone()
            safe["status"] = "OK"
            safe["current_database"] = row[0]
            safe["current_user"] = row[1]
        except Exception as exc:
            safe["status"] = "FAIL"
            safe["error"] = short_error(exc)
        safe["duration_ms"] = int((time.monotonic() - start) * 1000)
        results.append(safe)
    return results


env = {
    "PGHOST": getenv("PGHOST"),
    "PGPORT": getenv("PGPORT", "5432"),
    "PGDATABASE": getenv("PGDATABASE"),
    "PGUSER": getenv("PGUSER"),
    "PGPASSWORD": masked(getenv("PGPASSWORD")),
    "PGSSLMODE": getenv("PGSSLMODE", "prefer"),
    "MONITOR_PGHOST": getenv("MONITOR_PGHOST"),
    "MONITOR_PGPORT": getenv("MONITOR_PGPORT", "5432"),
    "MONITOR_PGUSER": getenv("MONITOR_PGUSER"),
    "MONITOR_PGPASSWORD": masked(getenv("MONITOR_PGPASSWORD")),
    "MONITOR_PGSSLMODE": getenv("MONITOR_PGSSLMODE"),
    "PGVIEW_USER": getenv("PGVIEW_USER"),
    "PGVIEW_PASSWORD": masked(getenv("PGVIEW_PASSWORD")),
}

path, registry, registry_error = registry_dbs()
actual, actual_error = list_monitor_databases()
actual_set = set(actual)
sample = [db for db in registry if db in actual_set]
if not sample:
    sample = [db for db in ["ae_service_uat", "ae_tps_uat", "ae_tps_warehouse_uat", "ae_common_uat"] if db in actual_set]
sample = sample[:8]

payload = {
    "env": env,
    "registry_path": path,
    "registry_error": registry_error,
    "registry_databases": registry,
    "monitor_database_error": actual_error,
    "monitor_databases": actual,
    "registry_missing_in_monitor": sorted(set(registry) - actual_set),
    "sample_databases": sample,
    "current_pg_path_smoke": smoke("current", sample),
    "monitor_pg_path_smoke": smoke("monitor", sample),
}
print(json.dumps(payload, indent=2, sort_keys=True))
'''


def run(cmd: list[str], *, stdin: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def load_json_output(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if proc.returncode != 0:
        return {
            "error": "command failed",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-2000:],
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {
            "error": "invalid json from remote check: %s" % exc,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:],
        }


def oc_json(args: argparse.Namespace, resource: str) -> dict[str, Any]:
    proc = run([args.oc, "get", resource, args.deployment, "-n", args.namespace, "-o", "json"], timeout=args.timeout)
    return load_json_output(proc)


def secret_refs(deploy: dict[str, Any], container_name: str | None) -> list[dict[str, str | None]]:
    containers = deploy.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if container_name:
        containers = [c for c in containers if c.get("name") == container_name]
    if not containers:
        return []
    refs = []
    for env in containers[0].get("env", []) or []:
        ref = ((env.get("valueFrom") or {}).get("secretKeyRef") or {})
        refs.append({
            "name": env.get("name"),
            "secret": ref.get("name"),
            "key": ref.get("key"),
            "value": env.get("value"),
        })
    return refs


def classify(payload: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if payload.get("error"):
        return ["remote check failed: %s" % payload["error"]]

    env = payload.get("env", {})
    pg_host = env.get("PGHOST")
    mon_host = env.get("MONITOR_PGHOST")
    if pg_host and mon_host and pg_host != mon_host:
        findings.append("PGHOST and MONITOR_PGHOST differ; BizMon must use MONITOR_PGHOST for application DB panels.")

    missing = payload.get("registry_missing_in_monitor") or []
    if missing:
        findings.append("registry contains DB names missing from monitored cluster: %s" % ", ".join(missing[:12]))

    current = payload.get("current_pg_path_smoke") or []
    monitor = payload.get("monitor_pg_path_smoke") or []
    current_fail = any(item.get("status") != "OK" for item in current)
    monitor_ok = bool(monitor) and all(item.get("status") == "OK" for item in monitor)
    monitor_fail = any(item.get("status") != "OK" for item in monitor)

    if current_fail and monitor_ok:
        findings.append("current PGHOST path fails but MONITOR_PGHOST path succeeds; fix BizMon connection builder to use MONITOR_* host/port.")
    elif monitor_fail:
        findings.append("MONITOR_PGHOST path failed; check object-monitor-region secret, role password, grants, or target DB names.")

    if not findings:
        findings.append("no BizMon connection mismatch detected by this checker")
    return findings


def print_table(title: str, rows: list[dict[str, Any]]) -> None:
    print("\n## " + title)
    if not rows:
        print("(none)")
        return
    for row in rows:
        status = row.get("status", "-")
        db = row.get("db", "-")
        host = row.get("host", "-")
        port = row.get("port", "-")
        user = row.get("user", "-")
        detail = row.get("error") or ("%s/%s" % (row.get("current_database"), row.get("current_user")))
        print("%s db=%s host=%s port=%s user=%s detail=%s" % (status, db, host, port, user, detail))


def print_summary(args: argparse.Namespace, deploy: dict[str, Any], payload: dict[str, Any], refs: list[dict[str, str | None]]) -> None:
    print("# Object Monitor BizMon Read-Only Check")
    print("collected_at=%s" % datetime.now(timezone.utc).isoformat())
    print("namespace=%s deployment=%s" % (args.namespace, args.deployment))

    if deploy.get("error"):
        print("\n## Deployment")
        print(deploy["error"])
    else:
        image = ""
        containers = deploy.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        if containers:
            image = containers[0].get("image", "")
        print("image=%s" % image)

    print("\n## Env Summary")
    env = payload.get("env", {})
    for key in [
        "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE",
        "MONITOR_PGHOST", "MONITOR_PGPORT", "MONITOR_PGUSER", "MONITOR_PGPASSWORD", "MONITOR_PGSSLMODE",
        "PGVIEW_USER", "PGVIEW_PASSWORD",
    ]:
        print("%s=%s" % (key, env.get(key)))

    print("\n## Secret References")
    for ref in refs:
        print("%s => secret=%s key=%s value=%s" % (
            ref.get("name"),
            ref.get("secret") or "",
            ref.get("key") or "",
            ref.get("value") or "",
        ))

    print("\n## Registry")
    print("path=%s" % payload.get("registry_path"))
    if payload.get("registry_error"):
        print("error=%s" % payload.get("registry_error"))
    print("registry_databases=%s" % ", ".join(payload.get("registry_databases") or []))

    print("\n## Monitored Databases")
    if payload.get("monitor_database_error"):
        print("error=%s" % payload.get("monitor_database_error"))
    print(", ".join(payload.get("monitor_databases") or []))

    print_table("Current PGHOST Path Smoke", payload.get("current_pg_path_smoke") or [])
    print_table("MONITOR_PGHOST Path Smoke", payload.get("monitor_pg_path_smoke") or [])

    print("\n## Findings")
    for item in classify(payload):
        print("- " + item)

    print("\n## Safe Fix Reminder")
    print("Keep PGHOST for the object-monitor metadata DB. BizMon application panels should use MONITOR_PGHOST/MONITOR_PGPORT/MONITOR_PGSSLMODE plus PGVIEW_* or MONITOR_* credentials.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only checker for object-monitor Application Monitoring/BizMon PostgreSQL wiring.",
    )
    parser.add_argument("--namespace", "-n", default="monitoring")
    parser.add_argument("--deployment", "-d", default="object-monitor")
    parser.add_argument("--container", help="Container name when the deployment has multiple containers.")
    parser.add_argument("--oc", default="oc")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--json-out", help="Write full collected JSON to this path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not shutil.which(args.oc):
        print("ERROR: oc binary not found: %s" % args.oc, file=sys.stderr)
        return 2

    deploy = oc_json(args, "deploy")
    refs = [] if deploy.get("error") else secret_refs(deploy, args.container)

    proc = run(
        [args.oc, "exec", "-i", "deploy/%s" % args.deployment, "-n", args.namespace, "--", "python", "-"],
        stdin=REMOTE_CHECK,
        timeout=args.timeout,
    )
    payload = load_json_output(proc)

    full = {
        "namespace": args.namespace,
        "deployment": args.deployment,
        "deployment_secret_refs": refs,
        "remote": payload,
    }
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(full, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print_summary(args, deploy, payload, refs)
    bad = any("failed" in item.lower() or "fix " in item.lower() for item in classify(payload))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
