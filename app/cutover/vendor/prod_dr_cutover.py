#!/usr/bin/env python3
"""Safe PROD/DR PostgreSQL cutover automation for Crunchy PGO.

This script is intentionally conservative:

* `dry-run` creates a run directory and command manifest without calling `oc`.
* `precheck` runs read-only OpenShift/PostgreSQL/pgBackRest checks only.
* `generate` writes a reviewable command manifest and shell review file.
* `execute` is gated and refuses state-changing steps unless an approval token
  and explicit flags are provided.

The high-risk production cutover operation is not Patroni cross-site failover.
For a Crunchy PGO standby cluster, planned DR promotion requires the active
cluster to be shut down or fenced first, then the DR PostgresCluster standby
configuration is disabled.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import textwrap
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


SCRIPT_VERSION = "2026-05-31.5"

DEFAULTS = {
    "prod_api": "https://api.ocp-prod.habibbank.local:6443",
    "dr_api": "https://api.ocp-dr.habibbank.local:6443",
    "oc_user": "mohsinali",
    "prod_context": "prod-pgcluster-uae/api-ocp-prod-habibbank-local:6443/mohsinali",
    "dr_context": "dr-pgcluster-uae/api-ocp-dr-habibbank-local:6443/mohsinali",
    "prod_namespace": "prod-pgcluster-uae",
    "dr_namespace": "dr-pgcluster-uae",
    "prod_cluster": "prod-pgcluster-uae",
    "dr_cluster": "dr-pgcluster-uae",
    "patroni_prod_cluster": "prod-pgcluster-uae-ha",
    "patroni_dr_cluster": "dr-pgcluster-uae-ha",
    "container": "database",
    "pg_user": "postgres",
    "pg_database": "postgres",
    "pgbackrest_stanza": "db",
    "pgbackrest_repo": "1",
    "prod_primary_lb": "10.171.1.229",
    "dr_primary_lb": "",
    "pgbackrest_s3_bucket": "pgbackrest-uae-prod-609d40f1-26e9-4616-9021-3135255d453e",
    "pgbackrest_s3_endpoint": "s3-openshift-storage.apps.ocp-prod.habibbank.local",
    "pgbackrest_s3_region": "prod",
    "prod_pgbackrest_secret": "prod-pgcluster-uae-pgbackrest-secret",
    "dr_pgbackrest_secret": "dr-pgcluster-uae-pgbackrest-secret",
    "postgres_port": "5555",
    "known_prod_primary_pod": "prod-pgcluster-uae-dc1-5c2q-0",
    "known_prod_standby_pod": "prod-pgcluster-uae-dc1-9c5j-0",
    "known_dr_standby_leader_pod": "dr-pgcluster-uae-dc1-p4rh-0",
    "known_dr_replica_pod": "dr-pgcluster-uae-dc1-lm6b-0",
    "patroni_config_path": None,
    "single_context_projects": False,
}

MODES = {
    "planned-switchover",
    "disaster-failover",
    "rebuild-dc1-standby",
    "switchback",
    "rebuild-dc2-standby",
    "full-lifecycle-plan",
}
LSN_RE = re.compile(r"^[0-9A-Fa-f]+/[0-9A-Fa-f]+$")

APPLICATION_WRITES_FROZEN_GATE = "application_writes_frozen"
PROD_FENCED_OR_SHUTDOWN_GATE = "prod_fenced_or_shutdown"
FINAL_LAG_APPROVED_GATE = "final_lag_approved"
SWITCHBACK_REBUILD_APPROVED_GATE = "switchback_rebuild_approved"
DC2_ACTIVE_CONFIRMED_GATE = "dc2_active_confirmed"
DC2_FENCED_OR_SHUTDOWN_GATE = "dc2_fenced_or_shutdown"
DC1_REBUILD_APPROVED_GATE = "dc1_rebuild_approved"
DC1_STANDBY_READY_GATE = "dc1_standby_ready"
DC1_ACTIVE_CONFIRMED_GATE = "dc1_active_confirmed"
DC2_REBUILD_APPROVED_GATE = "dc2_rebuild_approved"
DC2_STANDBY_READY_GATE = "dc2_standby_ready"

SECRET_REDACTIONS = [
    (re.compile(r"(password=)[^ \t\n\r]+", re.IGNORECASE), r"\1<redacted>"),
    (re.compile(r"(passfile=)[^ \t\n\r]+", re.IGNORECASE), r"\1<redacted>"),
    (re.compile(r"(--password=)[^ \t\n\r]+", re.IGNORECASE), r"\1<redacted>"),
    (re.compile(r"(oc\s+login\b[^\n\r]*?\s-p\s+)'[^']+'", re.IGNORECASE), r"\1'<redacted>'"),
    (re.compile(r'(oc\s+login\b[^\n\r]*?\s-p\s+)"[^"]+"', re.IGNORECASE), r'\1"<redacted>"'),
    (re.compile(r"(oc\s+login\b[^\n\r]*?\s-p\s+)[^ \t\n\r]+", re.IGNORECASE), r"\1<redacted>"),
]


class CutoverError(Exception):
    """Raised for expected operator-facing failures."""


class RawDefaultsHelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Preserve runbook line breaks while still showing argument defaults."""


@dataclass
class CommandResult:
    command: list[str]
    purpose: str
    target: str
    returncode: int
    stdout: str
    stderr: str
    started_at: str
    finished_at: str


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandStep:
    id: str
    title: str
    risk: str
    state_changing: bool
    requires_approval: bool
    target: str
    purpose: str
    command: str
    argv: list[str] | None = None
    expected_output: str = ""
    rollback: str = ""
    business_justification: str = ""
    manual_gate: bool = False
    required_gate_files: list[str] = field(default_factory=list)
    automatically_executable: bool = True
    internal_action: str | None = None


@dataclass
class RuntimeConfig:
    prod_api: str
    dr_api: str
    oc_user: str
    prod_context: str
    dr_context: str
    prod_namespace: str
    dr_namespace: str
    prod_cluster: str
    dr_cluster: str
    patroni_prod_cluster: str
    patroni_dr_cluster: str
    container: str
    pg_user: str
    pg_database: str
    pgbackrest_stanza: str
    pgbackrest_repo: str
    prod_primary_lb: str
    dr_primary_lb: str
    pgbackrest_s3_bucket: str
    pgbackrest_s3_endpoint: str
    pgbackrest_s3_region: str
    prod_pgbackrest_secret: str
    dr_pgbackrest_secret: str
    postgres_port: str
    known_prod_primary_pod: str
    known_prod_standby_pod: str
    known_dr_standby_leader_pod: str
    known_dr_replica_pod: str
    patroni_config_path: str | None = None
    single_context_projects: bool = False


def redact(value: str) -> str:
    text = value
    for regex, repl in SECRET_REDACTIONS:
        text = regex.sub(repl, text)
    return text


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def local_timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_json_dump(data: Any, path: pathlib.Path) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def shell_join(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def ensure_run_dir(base: str | None, label: str) -> pathlib.Path:
    root = pathlib.Path(base or "cutover_runs")
    run_dir = root / f"{local_timestamp()}_{label}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def gate_file_name(gate_name: str) -> str:
    return gate_name if gate_name.endswith(".approved") else f"{gate_name}.approved"


def require_gate_file(run_dir: pathlib.Path, gate_name: str) -> None:
    gate_file = run_dir / f"{gate_name}.approved"
    if not gate_file.exists():
        raise CutoverError(
            f"Required approval gate file missing: {gate_file}. "
            f"Create this file only after formal approval/evidence."
        )


def require_gate_files(run_dir: pathlib.Path, gate_files: list[str]) -> None:
    for gate_file in gate_files:
        gate_name = gate_file.removesuffix(".approved")
        require_gate_file(run_dir, gate_name)


def write_approval_gate(run_dir: pathlib.Path, gate_name: str, evidence: dict[str, Any]) -> pathlib.Path:
    gate_file = run_dir / f"{gate_name}.approved"
    payload = {
        "created_at": now_utc(),
        "gate": gate_name,
        "evidence": evidence,
    }
    safe_json_dump(payload, gate_file)
    return gate_file


def load_config(args: argparse.Namespace) -> RuntimeConfig:
    values = dict(DEFAULTS)
    for key in list(values):
        arg_key = key
        if hasattr(args, arg_key) and getattr(args, arg_key) is not None:
            values[key] = getattr(args, arg_key)
    return RuntimeConfig(**values)


def config_from_manifest(manifest: dict[str, Any]) -> RuntimeConfig:
    values = dict(DEFAULTS)
    values.update(manifest.get("config") or {})
    allowed = RuntimeConfig.__dataclass_fields__.keys()
    return RuntimeConfig(**{key: values.get(key) for key in allowed})


def parse_csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def wal_lag_within_bounds(lag_bytes: int | None, max_lag_bytes: int) -> bool:
    return lag_bytes is not None and 0 <= lag_bytes <= max_lag_bytes


class Audit:
    def __init__(self, run_dir: pathlib.Path) -> None:
        self.run_dir = run_dir
        self.log_path = run_dir / "evidence.log"

    def append(self, text: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")

    def record_command(self, result: CommandResult) -> None:
        self.append("=" * 88)
        self.append(f"Started: {result.started_at}")
        self.append(f"Finished: {result.finished_at}")
        self.append(f"Target: {result.target}")
        self.append(f"Purpose: {result.purpose}")
        self.append(f"Command: {shell_join(result.command)}")
        self.append(f"Exit code: {result.returncode}")
        if result.stdout:
            self.append("STDOUT:")
            self.append(redact(result.stdout.rstrip()))
        if result.stderr:
            self.append("STDERR:")
            self.append(redact(result.stderr.rstrip()))


def print_command_notice(
    *,
    purpose: str,
    target: str,
    argv: list[str],
    risk: str,
    expected: str,
    requires_approval: bool,
    safe_reason: str,
) -> None:
    print(
        textwrap.dedent(
            f"""
            Proposed Action:
            {purpose}

            Target:
            {target}

            Command:
            {shell_join(argv)}

            Risk:
            {risk}

            Why this is safe:
            {safe_reason}

            Expected Output:
            {expected}

            Requires approval:
            {"Yes" if requires_approval else "No"}
            """
        ).strip()
    )
    print()


def run_command(
    argv: list[str],
    *,
    audit: Audit,
    purpose: str,
    target: str,
    timeout: int = 90,
    allow_fail: bool = False,
    show_notice: bool = True,
) -> CommandResult:
    if show_notice:
        print_command_notice(
            purpose=purpose,
            target=target,
            argv=argv,
            risk="Low",
            expected="Read-only command output.",
            requires_approval=False,
            safe_reason="Read-only discovery command; no secret object reading and no state change.",
        )
    started = now_utc()
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    finished = now_utc()
    result = CommandResult(
        command=argv,
        purpose=purpose,
        target=target,
        returncode=proc.returncode,
        stdout=redact(proc.stdout),
        stderr=redact(proc.stderr),
        started_at=started,
        finished_at=finished,
    )
    audit.record_command(result)
    if proc.returncode != 0 and not allow_fail:
        message = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise CutoverError(f"{purpose} failed on {target}: {redact(message)}")
    return result


def json_loads_or_error(text: str, label: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise CutoverError(f"{label} returned empty output")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise CutoverError(f"{label} returned invalid JSON: {exc}") from exc


def oc_base(context: str) -> list[str]:
    if not context:
        return ["oc"]
    return ["oc", "--context", context]


def switch_project_if_needed(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    audit: Audit,
    purpose: str,
) -> None:
    if not cfg.single_context_projects:
        return
    run_command(
        oc_base(context) + ["project", namespace],
        audit=audit,
        purpose=purpose,
        target=f"current-context/{namespace}",
        timeout=90,
        show_notice=False,
    )


def oc_get_json(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    resource: str,
    name: str | None,
    audit: Audit,
    purpose: str,
    timeout: int = 90,
) -> Any:
    argv = oc_base(context) + ["get", resource]
    if name:
        argv.append(name)
    argv += ["-n", namespace, "-o", "json"]
    result = run_command(argv, audit=audit, purpose=purpose, target=f"{context}/{namespace}", timeout=timeout)
    return json_loads_or_error(result.stdout, purpose)


def oc_project(context: str, audit: Audit) -> str:
    result = run_command(
        oc_base(context) + ["project", "-q"],
        audit=audit,
        purpose="Verify OpenShift project for context",
        target=context,
        allow_fail=True,
    )
    return result.stdout.strip()


def pod_has_container(pod: dict[str, Any], container_name: str) -> bool:
    containers = pod.get("spec", {}).get("containers", [])
    return any(container.get("name") == container_name for container in containers)


def pod_ready(pod: dict[str, Any], container_name: str) -> tuple[bool, str]:
    phase = pod.get("status", {}).get("phase")
    conditions = pod.get("status", {}).get("conditions", [])
    ready_condition = next((item for item in conditions if item.get("type") == "Ready"), {})
    container_statuses = pod.get("status", {}).get("containerStatuses", [])
    db_status = next((item for item in container_statuses if item.get("name") == container_name), {})
    if phase != "Running":
        return False, f"phase={phase}"
    if ready_condition.get("status") != "True":
        return False, "pod Ready condition is not True"
    if db_status and not db_status.get("ready"):
        return False, f"{container_name} container not ready"
    return True, "ready"


def database_pods(pods_json: dict[str, Any], cluster: str, container_name: str) -> list[dict[str, Any]]:
    items = pods_json.get("items", [])
    selected: list[dict[str, Any]] = []
    for pod in items:
        name = pod.get("metadata", {}).get("name", "")
        labels = pod.get("metadata", {}).get("labels", {})
        cluster_label = labels.get("postgres-operator.crunchydata.com/cluster")
        has_instance_label = "postgres-operator.crunchydata.com/instance" in labels
        if cluster_label == cluster and has_instance_label and pod_has_container(pod, container_name):
            selected.append(pod)
            continue
        if name.startswith(cluster + "-") and pod_has_container(pod, container_name):
            if "pgbackrest" not in name and "repo" not in name and "pgbouncer" not in name:
                selected.append(pod)
    return sorted(selected, key=lambda item: item.get("metadata", {}).get("name", ""))


def psql_argv(cfg: RuntimeConfig, context: str, namespace: str, pod: str, sql: str) -> list[str]:
    return (
        oc_base(context)
        + [
            "exec",
            "-n",
            namespace,
            pod,
            "-c",
            cfg.container,
            "--",
            "psql",
            "-p",
            str(cfg.postgres_port),
            "-U",
            cfg.pg_user,
            "-d",
            cfg.pg_database,
            "-XAtq",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ]
    )


def pgbackrest_argv(cfg: RuntimeConfig, context: str, namespace: str, pod: str, extra: list[str]) -> list[str]:
    return (
        oc_base(context)
        + [
            "exec",
            "-n",
            namespace,
            pod,
            "-c",
            cfg.container,
            "--",
            "pgbackrest",
            f"--stanza={cfg.pgbackrest_stanza}",
            f"--repo={cfg.pgbackrest_repo}",
        ]
        + extra
    )


def patroni_argv(cfg: RuntimeConfig, context: str, namespace: str, pod: str) -> list[str]:
    patroni_cmd = ["patronictl"]
    if cfg.patroni_config_path:
        patroni_cmd += ["-c", cfg.patroni_config_path]
    patroni_cmd.append("list")
    return oc_base(context) + [
        "exec",
        "-n",
        namespace,
        pod,
        "-c",
        cfg.container,
        "--",
    ] + patroni_cmd


def exec_bash_argv(cfg: RuntimeConfig, context: str, namespace: str, pod: str, script: str) -> list[str]:
    return oc_base(context) + [
        "exec",
        "-n",
        namespace,
        pod,
        "-c",
        cfg.container,
        "--",
        "bash",
        "-c",
        script,
    ]


def lsn_to_int(value: str) -> int:
    hi, lo = value.split("/")
    return (int(hi, 16) << 32) | int(lo, 16)


def psql_scalar(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    pod: str,
    sql: str,
    audit: Audit,
    purpose: str,
    allow_fail: bool = False,
) -> str:
    result = run_command(
        psql_argv(cfg, context, namespace, pod, sql),
        audit=audit,
        purpose=purpose,
        target=f"{context}/{namespace}/{pod}",
        allow_fail=allow_fail,
    )
    return result.stdout.strip()


def psql_json(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    pod: str,
    sql: str,
    audit: Audit,
    purpose: str,
    allow_fail: bool = False,
) -> Any:
    output = psql_scalar(
        cfg,
        context=context,
        namespace=namespace,
        pod=pod,
        sql=sql,
        audit=audit,
        purpose=purpose,
        allow_fail=allow_fail,
    )
    if allow_fail and not output:
        return None
    return json_loads_or_error(output, purpose)


def patroni_summary(text: str) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    pending_restart = False
    for line in text.splitlines():
        if "Pending restart" in line:
            pending_restart = True
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        parts = [part.strip() for part in stripped.strip("|").split("|")]
        if not parts or parts[0].lower() in {"member", "cluster"}:
            continue
        if len(parts) >= 4 and parts[0] and not set(parts[0]) <= {"-", "+"}:
            rows.append(
                {
                    "member": parts[0],
                    "host": parts[1] if len(parts) > 1 else "",
                    "role": parts[2] if len(parts) > 2 else "",
                    "state": parts[3] if len(parts) > 3 else "",
                    "timeline": parts[4] if len(parts) > 4 else "",
                    "lag": parts[5] if len(parts) > 5 else "",
                }
            )
    return {"rows": rows, "pending_restart": pending_restart, "raw": redact(text)}


def select_patroni_member_by_role(patroni: dict[str, Any] | None, preferred_roles: list[str]) -> str | None:
    rows = (patroni or {}).get("rows") or []
    role_priority = [role.lower() for role in preferred_roles]
    for preferred_role in role_priority:
        for row in rows:
            role = str(row.get("role") or "").lower()
            member = row.get("member")
            if member and role == preferred_role:
                return str(member)
    for preferred_role in role_priority:
        for row in rows:
            role = str(row.get("role") or "").lower()
            member = row.get("member")
            if member and preferred_role in role:
                return str(member)
    return None


def choose_dr_replay_probe(dr_data: dict[str, Any]) -> str | None:
    recovery_ready = [
        pod.get("name")
        for pod in dr_data.get("database_pods", [])
        if pod.get("ready") and pod.get("pg_is_in_recovery") == "t" and pod.get("name")
    ]
    if not recovery_ready:
        return None
    preferred = select_patroni_member_by_role(dr_data.get("patroni"), ["standby leader", "leader"])
    if preferred in recovery_ready:
        return preferred
    return str(recovery_ready[0])


def pgbackrest_info(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    pod: str,
    audit: Audit,
    purpose: str,
) -> Any:
    result = run_command(
        pgbackrest_argv(cfg, context, namespace, pod, ["info", "--output=json"]),
        audit=audit,
        purpose=purpose,
        target=f"{context}/{namespace}/{pod}",
        timeout=180,
    )
    return json_loads_or_error(result.stdout, purpose)


def summarize_pgbackrest(info: Any) -> dict[str, Any]:
    if not isinstance(info, list) or not info:
        return {"ok": False, "detail": "pgBackRest info is not a non-empty list"}
    stanza = info[0]
    status = stanza.get("status", {})
    archive = stanza.get("archive", [])
    backups = stanza.get("backup", [])
    archive_ranges = [
        {
            "id": item.get("id"),
            "min": item.get("min"),
            "max": item.get("max"),
        }
        for item in archive
    ]
    latest_backup = None
    if backups:
        latest_backup = sorted(backups, key=lambda item: item.get("timestamp", {}).get("start", 0))[-1]
    return {
        "ok": status.get("code") == 0,
        "name": stanza.get("name"),
        "status": status,
        "cipher": stanza.get("cipher"),
        "archive": archive_ranges,
        "backup_count": len(backups),
        "latest_backup": latest_backup,
    }


def postgrescluster_summary(cr: dict[str, Any]) -> dict[str, Any]:
    spec = cr.get("spec", {})
    pgbackrest = spec.get("backups", {}).get("pgbackrest", {})
    repos = pgbackrest.get("repos", [])
    repo_summary = []
    for repo in repos:
        repo_summary.append(
            {
                "name": repo.get("name"),
                "s3": {
                    "bucket": repo.get("s3", {}).get("bucket"),
                    "endpoint": repo.get("s3", {}).get("endpoint"),
                    "region": repo.get("s3", {}).get("region"),
                },
                "volume": bool(repo.get("volume")),
            }
        )
    return {
        "name": cr.get("metadata", {}).get("name"),
        "namespace": cr.get("metadata", {}).get("namespace"),
        "postgresVersion": spec.get("postgresVersion"),
        "shutdown": spec.get("shutdown", False),
        "standby": spec.get("standby"),
        "patroni_dynamic": spec.get("patroni", {}).get("dynamicConfiguration", {}),
        "pgbackrest_global": pgbackrest.get("global", {}),
        "pgbackrest_repos": repo_summary,
        "pgbackrest_schedules": [
            {"name": repo.get("name"), "schedules": repo.get("schedules", {})} for repo in repos
        ],
        "proxy": spec.get("proxy", {}),
        "instances": spec.get("instances", []),
    }


def normalize_config_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def compare_specs(prod_summary: dict[str, Any], dr_summary: dict[str, Any]) -> tuple[list[str], list[str]]:
    mismatches: list[str] = []
    warnings: list[str] = []
    if normalize_config_value(prod_summary.get("postgresVersion")) != normalize_config_value(dr_summary.get("postgresVersion")):
        mismatches.append(
            f"postgresVersion mismatch: prod={prod_summary.get('postgresVersion')} dr={dr_summary.get('postgresVersion')}"
        )

    prod_global = prod_summary.get("pgbackrest_global") or {}
    dr_global = dr_summary.get("pgbackrest_global") or {}
    required_same_global_keys = {
        "compress-level",
        "compress-type",
        "process-max",
        "repo1-cipher-type",
        "repo1-retention-diff",
        "repo1-retention-full",
        "repo1-retention-full-type",
        "repo1-s3-uri-style",
        "repo1-s3-verify-tls",
        "spool-path",
    }
    ignored_generated_keys = {
        "archive-async",
        "repo1-path",
        "repo1-storage-host",
    }
    for key in sorted(required_same_global_keys):
        prod_value = normalize_config_value(prod_global.get(key))
        dr_value = normalize_config_value(dr_global.get(key))
        if prod_value != dr_value:
            mismatches.append(f"pgBackRest global {key} mismatch: prod={prod_global.get(key)} dr={dr_global.get(key)}")
    for key in sorted(ignored_generated_keys):
        prod_value = normalize_config_value(prod_global.get(key))
        dr_value = normalize_config_value(dr_global.get(key))
        if prod_value != dr_value:
            warnings.append(f"pgBackRest generated/global {key} differs: prod={prod_global.get(key)} dr={dr_global.get(key)}")

    if prod_summary.get("pgbackrest_repos") != dr_summary.get("pgbackrest_repos"):
        mismatches.append("pgBackRest repo summary mismatch between prod and DR")

    prod_params = (prod_summary.get("patroni_dynamic") or {}).get("postgresql", {}).get("parameters", {})
    dr_params = (dr_summary.get("patroni_dynamic") or {}).get("postgresql", {}).get("parameters", {})
    important_params = [
        "max_connections",
        "shared_buffers",
        "wal_level",
        "synchronous_commit",
        "max_wal_size",
        "min_wal_size",
        "max_slot_wal_keep_size",
    ]
    for key in important_params:
        prod_value = normalize_config_value(prod_params.get(key))
        dr_value = normalize_config_value(dr_params.get(key))
        if prod_value != dr_value:
            mismatches.append(f"PostgreSQL parameter {key} mismatch: prod={prod_params.get(key)} dr={dr_params.get(key)}")

    return mismatches, warnings


def sql_activity_summary() -> str:
    return """
    select row_to_json(t) from (
      select
        count(*) filter (where datname is not null) as total_db_sessions,
        count(*) filter (where datname is not null and state = 'active') as active_sessions,
        count(*) filter (where datname is not null and state like 'idle in transaction%') as idle_in_transaction,
        coalesce(extract(epoch from max(now() - state_change) filter (where state like 'idle in transaction%'))::bigint, 0) as longest_idle_in_txn_seconds,
        count(*) filter (
          where datname is not null
            and xact_start is not null
            and now() - xact_start > interval '5 minutes'
        ) as long_transaction_count,
        coalesce(extract(epoch from max(now() - xact_start) filter (
          where datname is not null
            and xact_start is not null
        ))::bigint, 0) as longest_transaction_seconds
      from pg_stat_activity
    ) t;
    """


def sql_application_session_summary(ignore_users: list[str]) -> str:
    ignored_array = "array[" + ", ".join(sql_literal(user) for user in ignore_users) + "]::text[]"
    return f"""
    with app_sessions as (
      select
        pid,
        usename,
        datname,
        coalesce(application_name, '') as application_name,
        coalesce(client_addr::text, '') as client_addr,
        coalesce(state, '') as state,
        coalesce(wait_event_type, '') as wait_event_type,
        coalesce(wait_event, '') as wait_event,
        coalesce(extract(epoch from now() - state_change)::bigint, 0) as state_age_seconds,
        coalesce(extract(epoch from now() - xact_start)::bigint, 0) as xact_age_seconds
      from pg_stat_activity
      where pid <> pg_backend_pid()
        and datname is not null
        and backend_type = 'client backend'
        and (cardinality({ignored_array}) = 0 or usename <> all({ignored_array}))
    ),
    sample_sessions as (
      select *
      from app_sessions
      where state = 'active' or state like 'idle in transaction%'
      order by xact_age_seconds desc, state_age_seconds desc, pid
      limit 20
    )
    select row_to_json(t) from (
      select
        {ignored_array} as ignored_users,
        (select count(*) from app_sessions) as total_considered_sessions,
        (select count(*) from app_sessions where state = 'active') as active_sessions,
        (select count(*) from app_sessions where state like 'idle in transaction%') as idle_in_transaction_sessions,
        (
          select count(*)
          from app_sessions
          where state = 'active' or state like 'idle in transaction%'
        ) as active_application_sessions,
        (
          select coalesce(json_agg(row_to_json(sample_sessions)), '[]'::json)
          from sample_sessions
        ) as sample_sessions
    ) t;
    """


def sql_replication_summary() -> str:
    return """
    select coalesce(json_agg(row_to_json(t)), '[]'::json) from (
      select
        application_name,
        client_addr::text,
        state,
        sync_state,
        sent_lsn::text,
        write_lsn::text,
        flush_lsn::text,
        replay_lsn::text,
        coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint, 0) as byte_lag,
        coalesce(write_lag::text, '') as write_lag,
        coalesce(flush_lag::text, '') as flush_lag,
        coalesce(replay_lag::text, '') as replay_lag
      from pg_stat_replication
      order by application_name
    ) t;
    """


def sql_archiver_summary() -> str:
    return """
    select row_to_json(t) from (
      select
        archived_count,
        coalesce(last_archived_wal, '') as last_archived_wal,
        coalesce(last_archived_time::text, '') as last_archived_time,
        case
          when last_archived_time is null then null
          else extract(epoch from now() - last_archived_time)::bigint
        end as archive_age_seconds,
        failed_count,
        coalesce(last_failed_wal, '') as last_failed_wal,
        coalesce(last_failed_time::text, '') as last_failed_time,
        case
          when last_failed_time is null then null
          else extract(epoch from now() - last_failed_time)::bigint
        end as last_failed_age_seconds
      from pg_stat_archiver
    ) t;
    """


def sql_wal_receiver_summary() -> str:
    return """
    select row_to_json(t) from (
      select
        pg_is_in_recovery() as in_recovery,
        pg_is_wal_replay_paused() as replay_paused,
        pg_last_wal_receive_lsn()::text as last_receive_lsn,
        pg_last_wal_replay_lsn()::text as last_replay_lsn,
        coalesce(extract(epoch from now() - pg_last_xact_replay_timestamp())::bigint, 0) as replay_delay_seconds,
        (select count(*) from pg_stat_wal_receiver) as wal_receiver_count,
        (select coalesce(json_agg(row_to_json(w)), '[]'::json) from (
          select
            status,
            receive_start_lsn::text,
            written_lsn::text,
            flushed_lsn::text,
            latest_end_lsn::text,
            latest_end_time::text
          from pg_stat_wal_receiver
        ) w) as wal_receiver
    ) t;
    """


def collect_site(
    cfg: RuntimeConfig,
    *,
    site: str,
    context: str,
    namespace: str,
    cluster: str,
    audit: Audit,
    allow_fail: bool = False,
) -> dict[str, Any]:
    site_data: dict[str, Any] = {
        "site": site,
        "context": context,
        "namespace": namespace,
        "cluster": cluster,
        "errors": [],
    }
    try:
        switch_project_if_needed(
            cfg,
            context=context,
            namespace=namespace,
            audit=audit,
            purpose=f"Switch to {site} project before site collection",
        )
        site_data["project"] = oc_project(context, audit)
        site_data["postgrescluster"] = oc_get_json(
            cfg,
            context=context,
            namespace=namespace,
            resource="postgrescluster",
            name=cluster,
            audit=audit,
            purpose=f"Collect {site} PostgresCluster JSON",
        )
        site_data["postgrescluster_summary"] = postgrescluster_summary(site_data["postgrescluster"])
        site_data["pods_json"] = oc_get_json(
            cfg,
            context=context,
            namespace=namespace,
            resource="pods",
            name=None,
            audit=audit,
            purpose=f"Collect {site} pod inventory",
        )
    except Exception as exc:
        if not allow_fail:
            raise
        site_data["errors"].append(str(exc))
        return site_data

    db_pods = database_pods(site_data["pods_json"], cluster, cfg.container)
    site_data["database_pods"] = []
    for pod in db_pods:
        name = pod.get("metadata", {}).get("name", "")
        ready, ready_detail = pod_ready(pod, cfg.container)
        pod_record: dict[str, Any] = {
            "name": name,
            "ready": ready,
            "ready_detail": ready_detail,
            "node": pod.get("spec", {}).get("nodeName"),
            "pod_ip": pod.get("status", {}).get("podIP"),
        }
        try:
            recovery = psql_scalar(
                cfg,
                context=context,
                namespace=namespace,
                pod=name,
                sql="select pg_is_in_recovery();",
                audit=audit,
                purpose=f"Check {site} recovery state on {name}",
                allow_fail=allow_fail,
            )
            pod_record["pg_is_in_recovery"] = recovery
        except Exception as exc:
            if not allow_fail:
                raise
            pod_record["error"] = str(exc)
        site_data["database_pods"].append(pod_record)

    first_ready = next((pod for pod in site_data["database_pods"] if pod.get("ready")), None)
    first_ready_name = first_ready.get("name") if first_ready else None
    if first_ready_name:
        try:
            patroni = run_command(
                patroni_argv(cfg, context, namespace, first_ready_name),
                audit=audit,
                purpose=f"Collect {site} Patroni list",
                target=f"{context}/{namespace}/{first_ready_name}",
                allow_fail=allow_fail,
            )
            site_data["patroni"] = patroni_summary(patroni.stdout)
        except Exception as exc:
            if not allow_fail:
                raise
            site_data["errors"].append(str(exc))

    return site_data


def summarize_postgrescluster_status(cr: dict[str, Any]) -> dict[str, Any]:
    status = cr.get("status", {}) if isinstance(cr, dict) else {}
    return {
        "observedGeneration": status.get("observedGeneration"),
        "instances": status.get("instances", []),
        "pgbackrest": status.get("pgbackrest", {}),
        "proxy": status.get("proxy", {}),
        "conditions": [
            {
                "type": condition.get("type"),
                "status": condition.get("status"),
                "reason": condition.get("reason"),
                "message": condition.get("message"),
            }
            for condition in status.get("conditions", [])
        ],
    }


def collect_context_site(
    cfg: RuntimeConfig,
    *,
    site: str,
    context: str,
    namespace: str,
    cluster: str,
    audit: Audit,
) -> dict[str, Any]:
    site_data: dict[str, Any] = {
        "site": site,
        "context": context,
        "namespace": namespace,
        "cluster": cluster,
        "errors": [],
    }
    switch_project_if_needed(
        cfg,
        context=context,
        namespace=namespace,
        audit=audit,
        purpose=f"Switch to {site} project before context check",
    )
    project = run_command(
        oc_base(context) + ["project", "-q"],
        audit=audit,
        purpose=f"Verify {site} OpenShift project for context",
        target=context,
        allow_fail=True,
    )
    site_data["project_returncode"] = project.returncode
    site_data["project"] = project.stdout.strip()
    if project.returncode != 0:
        site_data["errors"].append(redact(project.stderr.strip() or project.stdout.strip() or "context authentication failed"))
        return site_data

    cr_result = run_command(
        oc_base(context) + ["get", "postgrescluster", cluster, "-n", namespace, "-o", "json"],
        audit=audit,
        purpose=f"Collect {site} PostgresCluster JSON without pod exec",
        target=f"{context}/{namespace}/{cluster}",
        allow_fail=True,
    )
    site_data["postgrescluster_returncode"] = cr_result.returncode
    if cr_result.returncode == 0:
        cr = json_loads_or_error(cr_result.stdout, f"{site} PostgresCluster JSON")
        site_data["postgrescluster_summary"] = postgrescluster_summary(cr)
        site_data["postgrescluster_status"] = summarize_postgrescluster_status(cr)
    else:
        site_data["errors"].append(redact(cr_result.stderr.strip() or cr_result.stdout.strip() or "postgrescluster get failed"))

    pods_result = run_command(
        oc_base(context) + ["get", "pods", "-n", namespace, "-o", "json"],
        audit=audit,
        purpose=f"Collect {site} pod inventory without pod exec",
        target=f"{context}/{namespace}",
        allow_fail=True,
    )
    site_data["pods_returncode"] = pods_result.returncode
    if pods_result.returncode == 0:
        pods_json = json_loads_or_error(pods_result.stdout, f"{site} pod inventory")
        db_pods = database_pods(pods_json, cluster, cfg.container)
        site_data["database_pods"] = []
        for pod in db_pods:
            ready, ready_detail = pod_ready(pod, cfg.container)
            site_data["database_pods"].append(
                {
                    "name": pod.get("metadata", {}).get("name", ""),
                    "ready": ready,
                    "ready_detail": ready_detail,
                    "phase": pod.get("status", {}).get("phase"),
                    "pod_ip": pod.get("status", {}).get("podIP"),
                    "node": pod.get("spec", {}).get("nodeName"),
                    "ready_containers": sum(
                        1
                        for item in pod.get("status", {}).get("containerStatuses", [])
                        if item.get("ready")
                    ),
                    "total_containers": len(pod.get("status", {}).get("containerStatuses", [])),
                }
            )
    else:
        site_data["errors"].append(redact(pods_result.stderr.strip() or pods_result.stdout.strip() or "pod inventory failed"))
    return site_data


def collect_context_check(args: argparse.Namespace, cfg: RuntimeConfig, run_dir: pathlib.Path) -> dict[str, Any]:
    audit = Audit(run_dir)
    allow_pods_not_ready = bool(getattr(args, "allow_pods_not_ready", False))
    allow_archive_only_catchup = bool(getattr(args, "allow_archive_only_catchup", False))
    result: dict[str, Any] = {
        "script_version": SCRIPT_VERSION,
        "run_id": str(uuid.uuid4()),
        "mode": "context-check",
        "created_at": now_utc(),
        "run_dir": str(run_dir),
        "config": asdict(cfg),
        "checks": [],
        "status": "UNKNOWN",
        "errors": [],
        "warnings": [],
        "data": {},
    }
    result["data"]["context_check_options"] = {
        "allow_pods_not_ready": allow_pods_not_ready,
    }

    current_context = run_command(
        ["oc", "config", "current-context"],
        audit=audit,
        purpose="Record bastion current OpenShift context",
        target="local kubeconfig",
        allow_fail=True,
    )
    current_project = run_command(
        ["oc", "project"],
        audit=audit,
        purpose="Record bastion current OpenShift project",
        target="local kubeconfig",
        allow_fail=True,
    )
    current_server = run_command(
        ["oc", "whoami", "--show-server"],
        audit=audit,
        purpose="Record bastion current OpenShift API server",
        target="local kubeconfig",
        allow_fail=True,
    )
    result["data"]["bastion_current"] = {
        "context": current_context.stdout.strip(),
        "project_output": current_project.stdout.strip(),
        "server": current_server.stdout.strip(),
    }
    if not cfg.single_context_projects and current_context.stdout.strip() not in {cfg.prod_context, cfg.dr_context}:
        result["warnings"].append(
            "Bastion current context is not PROD or DR target; script uses explicit --context values, but operators must not rely on oc project."
        )

    prod = collect_context_site(
        cfg,
        site="prod",
        context=cfg.prod_context,
        namespace=cfg.prod_namespace,
        cluster=cfg.prod_cluster,
        audit=audit,
    )
    dr = collect_context_site(
        cfg,
        site="dr",
        context=cfg.dr_context,
        namespace=cfg.dr_namespace,
        cluster=cfg.dr_cluster,
        audit=audit,
    )
    result["data"]["prod"] = prod
    result["data"]["dr"] = dr

    checks: list[CheckResult] = []
    mode = getattr(args, "mode", "planned-switchover")
    # rebuild-dc1-standby runs AFTER a planned-switchover has shut down prod (DC1) and
    # promoted dr (DC2) to active. For this mode the steady-state expectations are
    # deliberately inverted: prod is the rebuild target (expected down, 0 pods) and
    # dr is the active source (expected standby.enabled=False).
    # switchback runs AFTER rebuild-dc1-standby has restored prod (DC1) as a healthy
    # standby of dr (DC2) - dr is still the active source (standby.enabled=False) at
    # the start of switchback, but prod is fully up (not a rebuild target).
    prod_role_is_rebuild_target = mode == "rebuild-dc1-standby"
    dr_role_is_active_source = mode in ("rebuild-dc1-standby", "switchback")

    def add_check(name: str, ok: bool, detail: str, evidence: dict[str, Any] | None = None) -> None:
        checks.append(CheckResult(name, "PASS" if ok else "FAIL", detail, evidence or {}))

    for site_data, expected_namespace in ((prod, cfg.prod_namespace), (dr, cfg.dr_namespace)):
        site = site_data["site"]
        add_check(
            f"{site}_context_authenticated",
            site_data.get("project_returncode") == 0,
            site_data.get("project") or "; ".join(site_data.get("errors", [])) or "unavailable",
        )
        add_check(
            f"{site}_context_namespace",
            site_data.get("project") == expected_namespace,
            f"expected {expected_namespace}, got {site_data.get('project') or 'unavailable'}",
        )
        add_check(
            f"{site}_postgrescluster_visible",
            bool(site_data.get("postgrescluster_summary")),
            f"cluster={site_data.get('cluster')} visible={bool(site_data.get('postgrescluster_summary'))}",
        )
        db_pods = site_data.get("database_pods") or []
        ready_pods = [pod for pod in db_pods if pod.get("ready")]
        pods_ready_ok = len(db_pods) >= 2 and len(ready_pods) == len(db_pods)
        pods_visible_but_not_ready = len(db_pods) > 0 and not pods_ready_ok
        prod_rebuild_target_no_pods = site == "prod" and prod_role_is_rebuild_target and len(db_pods) == 0
        if allow_pods_not_ready and pods_visible_but_not_ready:
            result["warnings"].append(
                f"{site} database pod readiness bypassed for validation only: ready={len(ready_pods)} total={len(db_pods)}"
            )
        if prod_rebuild_target_no_pods:
            result["warnings"].append(
                f"{site} has 0 database pods; expected for mode={mode} (prod is the rebuild target)"
            )
        add_check(
            f"{site}_database_pods_ready_no_exec",
            pods_ready_ok or (allow_pods_not_ready and pods_visible_but_not_ready) or prod_rebuild_target_no_pods,
            f"ready={len(ready_pods)} total={len(db_pods)} allow_pods_not_ready={allow_pods_not_ready}",
            {"database_pods": db_pods},
        )

    prod_summary = prod.get("postgrescluster_summary") or {}
    dr_summary = dr.get("postgrescluster_summary") or {}
    if prod_summary:
        add_check(
            "prod_not_shutdown",
            prod_role_is_rebuild_target or prod_summary.get("shutdown") is not True,
            f"prod shutdown={prod_summary.get('shutdown')}"
            + (f"; expected for mode={mode}" if prod_role_is_rebuild_target else ""),
        )
    if dr_summary:
        dr_standby = dr_summary.get("standby") or {}
        add_check(
            "dr_standby_enabled",
            dr_role_is_active_source or dr_standby.get("enabled") is True,
            f"dr standby={dr_standby}"
            + (f"; dr is active source for mode={mode}, standby.enabled=False expected" if dr_role_is_active_source else ""),
        )

    result["checks"] = [asdict(check) for check in checks]
    result["errors"] = [f"{check.name}: {check.detail}" for check in checks if check.status == "FAIL"]
    result["status"] = "PASS" if not result["errors"] else "FAIL"
    safe_json_dump(result, run_dir / "context_check.json")
    return result


def collect_precheck(args: argparse.Namespace, cfg: RuntimeConfig, run_dir: pathlib.Path) -> dict[str, Any]:
    audit = Audit(run_dir)
    ignore_db_users = parse_csv_list(args.ignore_db_users)
    allow_pods_not_ready = bool(getattr(args, "allow_pods_not_ready", False))
    allow_archive_only_catchup = bool(getattr(args, "allow_archive_only_catchup", False))
    result: dict[str, Any] = {
        "script_version": SCRIPT_VERSION,
        "run_id": str(uuid.uuid4()),
        "mode": args.mode,
        "created_at": now_utc(),
        "run_dir": str(run_dir),
        "config": asdict(cfg),
        "checks": [],
        "status": "UNKNOWN",
        "errors": [],
        "warnings": [],
        "data": {},
    }
    result["data"]["precheck_options"] = {
        "ignore_db_users": ignore_db_users,
        "allow_active_sessions": bool(args.allow_active_sessions),
        "allow_pods_not_ready": allow_pods_not_ready,
        "allow_archive_only_catchup": allow_archive_only_catchup,
        "max_lag_bytes": args.max_lag_bytes,
        "max_replay_delay_seconds": args.max_replay_delay_seconds,
        "max_archive_age_seconds": args.max_archive_age_seconds,
    }

    # rebuild-dc1-standby runs after planned-switchover has shut down prod (DC1) and
    # promoted dr (DC2) to active - prod being down/unreachable is the EXPECTED
    # precondition for this mode (rebuild target), exactly like disaster-failover.
    prod_allow_fail = args.mode in ("disaster-failover", "rebuild-dc1-standby")
    # For rebuild-dc1-standby AND switchback, dr (DC2) is the active source, not a
    # pure standby of prod - the steady-state "dr is all-standby / standby.enabled=True"
    # checks are inverted for these modes.
    dr_role_is_active_source = args.mode in ("rebuild-dc1-standby", "switchback")
    # switchback runs after rebuild-dc1-standby has restored prod (DC1) as a healthy
    # standby_cluster of active dr (DC2) - prod has 0 live primaries and all pods in
    # recovery, the inverse of the normal "prod is the writable primary" steady state.
    prod_role_is_standby_source = args.mode == "switchback"
    prod = collect_site(
        cfg,
        site="prod",
        context=cfg.prod_context,
        namespace=cfg.prod_namespace,
        cluster=cfg.prod_cluster,
        audit=audit,
        allow_fail=prod_allow_fail,
    )
    dr = collect_site(
        cfg,
        site="dr",
        context=cfg.dr_context,
        namespace=cfg.dr_namespace,
        cluster=cfg.dr_cluster,
        audit=audit,
        allow_fail=False,
    )
    result["data"]["prod"] = prod
    result["data"]["dr"] = dr

    checks: list[CheckResult] = []

    def add_check(name: str, ok: bool, detail: str, evidence: dict[str, Any] | None = None) -> None:
        checks.append(CheckResult(name, "PASS" if ok else "FAIL", detail, evidence or {}))

    add_check(
        "prod_context_namespace",
        prod_allow_fail or prod.get("project") == cfg.prod_namespace,
        f"expected {cfg.prod_namespace}, got {prod.get('project') or 'unavailable'}",
    )
    add_check(
        "dr_context_namespace",
        dr.get("project") == cfg.dr_namespace,
        f"expected {cfg.dr_namespace}, got {dr.get('project') or 'unavailable'}",
    )

    prod_db_pods = prod.get("database_pods", [])
    dr_db_pods = dr.get("database_pods", [])
    prod_ready = [pod for pod in prod_db_pods if pod.get("ready")]
    dr_ready = [pod for pod in dr_db_pods if pod.get("ready")]
    prod_pods_ready_ok = len(prod_ready) == len(prod_db_pods) >= 2
    dr_pods_ready_ok = len(dr_ready) == len(dr_db_pods) >= 2
    prod_pods_visible_but_not_ready = len(prod_db_pods) > 0 and not prod_pods_ready_ok
    dr_pods_visible_but_not_ready = len(dr_db_pods) > 0 and not dr_pods_ready_ok
    if allow_pods_not_ready and prod_pods_visible_but_not_ready:
        result["warnings"].append(
            f"PROD database pod readiness bypassed for validation only: ready={len(prod_ready)} total={len(prod_db_pods)}"
        )
    if allow_pods_not_ready and dr_pods_visible_but_not_ready:
        result["warnings"].append(
            f"DR database pod readiness bypassed for validation only: ready={len(dr_ready)} total={len(dr_db_pods)}"
        )
    add_check(
        "prod_database_pods_ready",
        prod_allow_fail or prod_pods_ready_ok or (allow_pods_not_ready and prod_pods_visible_but_not_ready),
        f"ready={len(prod_ready)} total={len(prod_db_pods)} allow_pods_not_ready={allow_pods_not_ready}",
    )
    add_check(
        "dr_database_pods_ready",
        dr_pods_ready_ok or (allow_pods_not_ready and dr_pods_visible_but_not_ready),
        f"ready={len(dr_ready)} total={len(dr_db_pods)} allow_pods_not_ready={allow_pods_not_ready}",
    )

    prod_primary_pods = [pod for pod in prod_db_pods if pod.get("pg_is_in_recovery") == "f"]
    prod_recovery_pods = [pod for pod in prod_db_pods if pod.get("pg_is_in_recovery") == "t"]
    dr_recovery_pods = [pod for pod in dr_db_pods if pod.get("pg_is_in_recovery") == "t"]
    add_check(
        "prod_single_primary",
        prod_allow_fail or prod_role_is_standby_source or len(prod_primary_pods) == 1,
        f"prod primary count={len(prod_primary_pods)}"
        + (f"; prod is standby source for mode={args.mode}, 0 primaries expected" if prod_role_is_standby_source else ""),
    )
    add_check("prod_has_standby", prod_allow_fail or len(prod_recovery_pods) >= 1, f"prod standby count={len(prod_recovery_pods)}")
    add_check(
        "dr_all_pods_in_recovery",
        dr_role_is_active_source or (len(dr_recovery_pods) == len(dr_db_pods) and len(dr_db_pods) > 0),
        f"dr recovery pods={len(dr_recovery_pods)} total={len(dr_db_pods)}"
        + (f"; dr is active source for mode={args.mode}, not all pods in recovery expected" if dr_role_is_active_source else ""),
    )

    prod_summary = prod.get("postgrescluster_summary") or {}
    dr_summary = dr.get("postgrescluster_summary") or {}
    dr_standby = dr_summary.get("standby") or {}
    add_check(
        "dr_standby_enabled",
        dr_role_is_active_source or dr_standby.get("enabled") is True,
        f"dr standby={dr_standby}"
        + (f"; dr is active source for mode={args.mode}, standby.enabled=False expected" if dr_role_is_active_source else ""),
    )
    add_check("prod_not_shutdown", prod_allow_fail or prod_summary.get("shutdown") is not True, f"prod shutdown={prod_summary.get('shutdown')}")

    mismatches: list[str] = []
    spec_warnings: list[str] = []
    if prod.get("postgrescluster_summary") and dr.get("postgrescluster_summary"):
        mismatches, spec_warnings = compare_specs(prod_summary, dr_summary)
        result["warnings"].extend(spec_warnings)
    add_check("prod_dr_spec_consistency", not mismatches, "no blocking spec mismatch" if not mismatches else "; ".join(mismatches), {"mismatches": mismatches})

    if prod_primary_pods:
        primary_pod = prod_primary_pods[0]["name"]
        result["data"]["prod"]["primary_pod"] = primary_pod
        result["data"]["prod"]["standby_pods"] = [pod["name"] for pod in prod_recovery_pods]
        result["data"]["prod"]["current_lsn"] = psql_scalar(
            cfg,
            context=cfg.prod_context,
            namespace=cfg.prod_namespace,
            pod=primary_pod,
            sql="select pg_current_wal_lsn()::text;",
            audit=audit,
            purpose="Collect prod current WAL LSN",
        )
        result["data"]["prod"]["activity_summary"] = psql_json(
            cfg,
            context=cfg.prod_context,
            namespace=cfg.prod_namespace,
            pod=primary_pod,
            sql=sql_activity_summary(),
            audit=audit,
            purpose="Collect prod activity summary",
        )
        result["data"]["prod"]["application_session_summary"] = psql_json(
            cfg,
            context=cfg.prod_context,
            namespace=cfg.prod_namespace,
            pod=primary_pod,
            sql=sql_application_session_summary(ignore_db_users),
            audit=audit,
            purpose="Collect prod application session summary",
        )
        result["data"]["prod"]["replication_summary"] = psql_json(
            cfg,
            context=cfg.prod_context,
            namespace=cfg.prod_namespace,
            pod=primary_pod,
            sql=sql_replication_summary(),
            audit=audit,
            purpose="Collect prod replication summary",
        )
        result["data"]["prod"]["archiver_before"] = psql_json(
            cfg,
            context=cfg.prod_context,
            namespace=cfg.prod_namespace,
            pod=primary_pod,
            sql=sql_archiver_summary(),
            audit=audit,
            purpose="Collect prod archiver summary",
        )
        if args.archiver_sample_seconds > 0:
            print(f"Waiting {args.archiver_sample_seconds}s before second archiver sample...")
            import time

            time.sleep(args.archiver_sample_seconds)
            result["data"]["prod"]["archiver_after"] = psql_json(
                cfg,
                context=cfg.prod_context,
                namespace=cfg.prod_namespace,
                pod=primary_pod,
                sql=sql_archiver_summary(),
                audit=audit,
                purpose="Collect prod archiver summary second sample",
            )
        result["data"]["prod"]["pgbackrest_info"] = pgbackrest_info(
            cfg,
            context=cfg.prod_context,
            namespace=cfg.prod_namespace,
            pod=primary_pod,
            audit=audit,
            purpose="Collect prod pgBackRest repository info",
        )
        result["data"]["prod"]["pgbackrest_summary"] = summarize_pgbackrest(result["data"]["prod"]["pgbackrest_info"])

    dr_probe_pod = choose_dr_replay_probe(dr)
    if dr_probe_pod:
        result["data"]["dr"]["probe_pod"] = dr_probe_pod
        result["data"]["dr"]["wal_receiver_summary"] = psql_json(
            cfg,
            context=cfg.dr_context,
            namespace=cfg.dr_namespace,
            pod=dr_probe_pod,
            sql=sql_wal_receiver_summary(),
            audit=audit,
            purpose="Collect DR WAL receiver and replay summary",
        )
        result["data"]["dr"]["pgbackrest_info"] = pgbackrest_info(
            cfg,
            context=cfg.dr_context,
            namespace=cfg.dr_namespace,
            pod=dr_probe_pod,
            audit=audit,
            purpose="Collect DR pgBackRest repository info",
        )
        result["data"]["dr"]["pgbackrest_summary"] = summarize_pgbackrest(result["data"]["dr"]["pgbackrest_info"])

    prod_pgbr_ok = bool(result["data"].get("prod", {}).get("pgbackrest_summary", {}).get("ok"))
    dr_pgbr_ok = bool(result["data"].get("dr", {}).get("pgbackrest_summary", {}).get("ok"))
    add_check("prod_pgbackrest_ok", prod_allow_fail or prod_pgbr_ok, f"prod pgBackRest ok={prod_pgbr_ok}")
    add_check("dr_pgbackrest_ok", dr_pgbr_ok, f"dr pgBackRest ok={dr_pgbr_ok}")

    patroni_pending = False
    for site_data in (prod, dr):
        patroni_pending = patroni_pending or bool(site_data.get("patroni", {}).get("pending_restart"))
    add_check("no_patroni_pending_restart", not patroni_pending, f"pending_restart={patroni_pending}")

    archiver_ok = True
    archiver_detail = "archiver check unavailable"
    archiver_before = result["data"].get("prod", {}).get("archiver_before")
    archiver_after = result["data"].get("prod", {}).get("archiver_after")
    if archiver_before and not prod_allow_fail:
        archiver_detail = f"failed_count={archiver_before.get('failed_count')}"
        if archiver_before.get("last_failed_time") and archiver_before.get("last_archived_time"):
            archiver_ok = str(archiver_before["last_failed_time"]) <= str(archiver_before["last_archived_time"])
        if archiver_after:
            archiver_ok = archiver_ok and archiver_after.get("failed_count") == archiver_before.get("failed_count")
            archiver_detail += f" second_failed_count={archiver_after.get('failed_count')}"
    add_check("prod_archiver_not_failing", prod_allow_fail or archiver_ok, archiver_detail)

    activity = result["data"].get("prod", {}).get("activity_summary") or {}
    app_sessions = result["data"].get("prod", {}).get("application_session_summary") or {}
    no_idle = int(activity.get("idle_in_transaction") or 0) == 0
    no_long_tx = int(activity.get("long_transaction_count") or 0) == 0
    add_check("no_idle_in_transaction", prod_allow_fail or no_idle, f"idle_in_transaction={activity.get('idle_in_transaction')}")
    add_check("no_long_transactions", prod_allow_fail or no_long_tx, f"long_transaction_count={activity.get('long_transaction_count')}")
    active_app_sessions = int(app_sessions.get("active_application_sessions") or 0)
    app_session_ok = args.allow_active_sessions or active_app_sessions == 0
    if args.mode == "planned-switchover":
        add_check(
            "no_active_application_sessions",
            app_session_ok,
            (
                f"active_application_sessions={active_app_sessions}; "
                f"ignored_users={','.join(ignore_db_users) or '(none)'}; "
                f"allow_active_sessions={args.allow_active_sessions}"
            ),
            {"sample_sessions": app_sessions.get("sample_sessions", [])},
        )
    else:
        add_check(
            "no_active_application_sessions",
            True,
            f"recorded active_application_sessions={active_app_sessions}; hard enforcement is planned-switchover only",
            {"sample_sessions": app_sessions.get("sample_sessions", [])},
        )

    wal_receiver = result["data"].get("dr", {}).get("wal_receiver_summary") or {}
    prod_lsn = result["data"].get("prod", {}).get("current_lsn")
    dr_lsn = wal_receiver.get("last_replay_lsn")
    lag_bytes = None
    if prod_lsn and dr_lsn and LSN_RE.match(prod_lsn) and LSN_RE.match(dr_lsn) and prod_primary_pods:
        primary_pod = prod_primary_pods[0]["name"]
        lag_output = psql_scalar(
            cfg,
            context=cfg.prod_context,
            namespace=cfg.prod_namespace,
            pod=primary_pod,
            sql=f"select pg_wal_lsn_diff('{prod_lsn}', '{dr_lsn}')::bigint;",
            audit=audit,
            purpose="Compare prod current LSN to DR replay LSN",
        )
        try:
            lag_bytes = int(lag_output)
        except ValueError:
            lag_bytes = None
    result["data"]["computed_lag_bytes"] = lag_bytes
    required_lag = args.max_lag_bytes if args.mode == "planned-switchover" else args.disaster_max_lag_bytes
    archive_only_zero_lag_ok = (
        args.mode == "planned-switchover"
        and allow_archive_only_catchup
        and lag_bytes is not None
        and 0 <= lag_bytes <= required_lag
        and prod_pgbr_ok
        and dr_pgbr_ok
    )
    result["data"]["archive_only_zero_lag_ok"] = archive_only_zero_lag_ok

    archive_sample = archiver_after or archiver_before or {}
    archive_age = to_int(archive_sample.get("archive_age_seconds"))
    last_archived_time = archive_sample.get("last_archived_time")
    app_database_active = int(app_sessions.get("total_considered_sessions") or 0) > 0
    if prod_allow_fail:
        if not last_archived_time or archive_age is None or archive_age > args.max_archive_age_seconds:
            result["warnings"].append(
                "prod archive freshness unavailable or older than threshold; recorded only in disaster mode"
            )
        add_check(
            "prod_archive_fresh_enough",
            True,
            (
                f"last_archived_time={last_archived_time or 'unavailable'} "
                f"archive_age_seconds={archive_age} max={args.max_archive_age_seconds}; disaster mode warning only"
            ),
        )
    else:
        archive_fresh_ok = bool(last_archived_time)
        archive_detail = (
            f"last_archived_time={last_archived_time or 'unavailable'} "
            f"archive_age_seconds={archive_age} max={args.max_archive_age_seconds} "
            f"app_database_active={app_database_active} "
            f"lag_bytes={lag_bytes} allow_archive_only_catchup={allow_archive_only_catchup} "
            f"archive_only_zero_lag_ok={archive_only_zero_lag_ok}"
        )
        if archive_fresh_ok and app_database_active and archive_age is not None:
            archive_fresh_ok = archive_age <= args.max_archive_age_seconds or archive_only_zero_lag_ok
            if archive_age > args.max_archive_age_seconds and archive_only_zero_lag_ok:
                result["warnings"].append(
                    "prod archive age is above threshold, but explicit archive-only catch-up is allowed and computed_lag_bytes=0"
                )
        if archive_fresh_ok and not app_database_active and archive_age is not None and archive_age > args.max_archive_age_seconds:
            result["warnings"].append(
                f"prod archive age is {archive_age}s, above threshold, but no non-ignored app sessions were active"
            )
        add_check("prod_archive_fresh_enough", archive_fresh_ok, archive_detail)

    wal_receiver = result["data"].get("dr", {}).get("wal_receiver_summary") or {}
    add_check("dr_replay_not_paused", wal_receiver.get("replay_paused") is False, f"replay_paused={wal_receiver.get('replay_paused')}")
    wal_receiver_count = to_int(wal_receiver.get("wal_receiver_count"))
    replay_delay_seconds = to_int(wal_receiver.get("replay_delay_seconds"))
    if args.mode == "planned-switchover":
        dr_wal_receiver_ok = wal_receiver_count is not None and wal_receiver_count >= 1
        dr_replay_delay_ok = replay_delay_seconds is not None and replay_delay_seconds <= args.max_replay_delay_seconds
        if not dr_wal_receiver_ok and archive_only_zero_lag_ok:
            result["warnings"].append(
                "DR WAL receiver is inactive, but explicit archive-only catch-up is allowed and computed_lag_bytes=0"
            )
        if not dr_replay_delay_ok and archive_only_zero_lag_ok:
            result["warnings"].append(
                "DR replay timestamp is older than threshold, but explicit archive-only catch-up is allowed and computed_lag_bytes=0"
            )
        add_check(
            "dr_wal_receiver_active",
            dr_wal_receiver_ok or archive_only_zero_lag_ok,
            f"wal_receiver_count={wal_receiver_count} archive_only_zero_lag_ok={archive_only_zero_lag_ok} lag_bytes={lag_bytes}",
            {"wal_receiver": wal_receiver.get("wal_receiver", [])},
        )
        add_check(
            "dr_replay_delay_acceptable",
            dr_replay_delay_ok or archive_only_zero_lag_ok or (dr_wal_receiver_ok and lag_bytes is not None and lag_bytes <= required_lag),
            f"replay_delay_seconds={replay_delay_seconds} max={args.max_replay_delay_seconds} "
            f"archive_only_zero_lag_ok={archive_only_zero_lag_ok} lag_bytes={lag_bytes}",
        )
    else:
        if wal_receiver_count is None or wal_receiver_count < 1:
            result["warnings"].append("DR WAL receiver is not active or unavailable; warning only outside planned switchover")
        if replay_delay_seconds is None or replay_delay_seconds > args.max_replay_delay_seconds:
            result["warnings"].append("DR replay delay is unavailable or above threshold; evidence only outside planned switchover")
        add_check(
            "dr_wal_receiver_active",
            True,
            f"wal_receiver_count={wal_receiver_count}; warning-only for mode={args.mode}",
            {"wal_receiver": wal_receiver.get("wal_receiver", [])},
        )
        add_check(
            "dr_replay_delay_acceptable",
            True,
            f"replay_delay_seconds={replay_delay_seconds} max={args.max_replay_delay_seconds}; warning-only for mode={args.mode}",
        )

    prod_lsn = result["data"].get("prod", {}).get("current_lsn")
    dr_lsn = wal_receiver.get("last_replay_lsn")
    lag_bytes = None
    if prod_lsn and dr_lsn and LSN_RE.match(prod_lsn) and LSN_RE.match(dr_lsn) and prod_primary_pods:
        primary_pod = prod_primary_pods[0]["name"]
        lag_output = psql_scalar(
            cfg,
            context=cfg.prod_context,
            namespace=cfg.prod_namespace,
            pod=primary_pod,
            sql=f"select pg_wal_lsn_diff('{prod_lsn}', '{dr_lsn}')::bigint;",
            audit=audit,
            purpose="Compare prod current LSN to DR replay LSN",
        )
        try:
            lag_bytes = int(lag_output)
        except ValueError:
            lag_bytes = None
    result["data"]["computed_lag_bytes"] = lag_bytes
    required_lag = args.max_lag_bytes if args.mode == "planned-switchover" else args.disaster_max_lag_bytes
    if args.mode == "planned-switchover":
        add_check(
            "planned_switchover_zero_or_allowed_lag",
            wal_lag_within_bounds(lag_bytes, required_lag),
            f"lag_bytes={lag_bytes} allowed={required_lag}",
        )
    elif args.mode == "disaster-failover":
        add_check(
            "disaster_dr_lag_recorded",
            lag_bytes is None or lag_bytes <= required_lag,
            f"lag_bytes={lag_bytes} allowed={required_lag}; prod may be unavailable in disaster mode",
        )

    result["checks"] = [asdict(check) for check in checks]
    result["errors"] = [f"{check.name}: {check.detail}" for check in checks if check.status == "FAIL"]
    result["status"] = "PASS" if not result["errors"] else "FAIL"
    safe_json_dump(result, run_dir / "precheck.json")
    safe_json_dump(result.get("data", {}).get("prod", {}).get("postgrescluster", {}), run_dir / "prod_postgrescluster.json")
    safe_json_dump(result.get("data", {}).get("dr", {}).get("postgrescluster", {}), run_dir / "dr_postgrescluster.json")
    return result


def command_step(
    *,
    step_id: str,
    title: str,
    risk: str,
    state_changing: bool,
    target: str,
    purpose: str,
    argv: list[str] | None = None,
    command: str | None = None,
    expected_output: str = "",
    rollback: str = "",
    business_justification: str = "",
    manual_gate: bool = False,
    required_gate_files: list[str] | None = None,
    automatically_executable: bool | None = None,
    internal_action: str | None = None,
) -> CommandStep:
    auto = (not manual_gate) if automatically_executable is None else automatically_executable
    return CommandStep(
        id=step_id,
        title=title,
        risk=risk,
        state_changing=state_changing,
        requires_approval=state_changing or risk.lower() in {"medium", "high"},
        target=target,
        purpose=purpose,
        command=command or (shell_join(argv) if argv else ""),
        argv=argv,
        expected_output=expected_output,
        rollback=rollback,
        business_justification=business_justification,
        manual_gate=manual_gate,
        required_gate_files=required_gate_files or [],
        automatically_executable=auto,
        internal_action=internal_action,
    )


def patch_argv(context: str, namespace: str, cluster: str, patch: dict[str, Any]) -> list[str]:
    return oc_base(context) + [
        "patch",
        "postgrescluster",
        cluster,
        "-n",
        namespace,
        "--type=merge",
        "-p",
        json.dumps(patch, separators=(",", ":")),
    ]



def postgres_pvc_selector(cluster: str) -> str:
    return (
        f"postgres-operator.crunchydata.com/cluster={cluster},"
        "postgres-operator.crunchydata.com/data=postgres"
    )


def delete_postgres_pvc_argv(context: str, namespace: str, cluster: str) -> list[str]:
    return oc_base(context) + [
        "delete",
        "pvc",
        "-n",
        namespace,
        "-l",
        postgres_pvc_selector(cluster),
    ]


def inventory_rebuild_argv(context: str, namespace: str, cluster: str) -> list[str]:
    return oc_base(context) + [
        "get",
        "postgrescluster,pods,pvc,svc",
        "-n",
        namespace,
    ]


def pgbackrest_data_source(cfg: RuntimeConfig, *, target_secret: str) -> dict[str, Any]:
    return {
        "pgbackrest": {
            "stanza": cfg.pgbackrest_stanza,
            "configuration": [{"secret": {"name": target_secret}}],
            "global": {
                "compress-level": "3",
                "compress-type": "lz4",
                "process-max": "8",
                "repo1-cipher-type": "aes-256-cbc",
                "repo1-s3-uri-style": "path",
                "repo1-s3-verify-tls": "n",
                "spool-path": "/pgdata/pgbackrest-spool",
            },
            "repo": {
                "name": "repo1",
                "s3": {
                    "bucket": cfg.pgbackrest_s3_bucket,
                    "endpoint": cfg.pgbackrest_s3_endpoint,
                    "region": cfg.pgbackrest_s3_region,
                },
            },
        }
    }


def standby_restore_patch(cfg: RuntimeConfig, *, host: str, target_secret: str) -> dict[str, Any]:
    return {
        "spec": {
            "shutdown": False,
            "standby": {
                "enabled": True,
                "host": host,
                "port": int(cfg.postgres_port),
                "repoName": "repo1",
            },
            "dataSource": pgbackrest_data_source(cfg, target_secret=target_secret),
        }
    }


def require_non_empty(value: str, option_name: str) -> str:
    if not value:
        raise CutoverError(f"{option_name} is required for this lifecycle phase")
    return value


def append_rebuild_standby_steps(
    steps: list[CommandStep],
    cfg: RuntimeConfig,
    *,
    prefix: str,
    target_label: str,
    active_label: str,
    context: str,
    namespace: str,
    cluster: str,
    target_secret: str,
    standby_host: str,
    active_confirm_gate: str,
    rebuild_gate: str,
    ready_gate: str,
    include_destructive_rebuild: bool,
) -> None:
    steps.extend(
        [
            command_step(
                step_id=f"manual-confirm-{active_label.lower()}-active-before-{prefix}-rebuild",
                title=f"Confirm {active_label} Active",
                risk="High",
                state_changing=True,
                target=f"{active_label} database and application routing",
                purpose=f"Confirm {active_label} is the only writable production database before rebuilding {target_label}.",
                command=(
                    f"# MANUAL GATE: confirm {active_label} is writable, applications are using {active_label}, "
                    f"and {target_label} is not writable.\n"
                    f"# After approval/evidence, create: {gate_file_name(active_confirm_gate)}"
                ),
                expected_output=f"{gate_file_name(active_confirm_gate)} exists.",
                rollback="Cancel rebuild; keep current active site unchanged.",
                business_justification="Prevent stale data from rejoining after the active site changed.",
                manual_gate=True,
            ),
            command_step(
                step_id=f"inventory-{prefix}-before-rebuild",
                title=f"Inventory {target_label}",
                risk="Low",
                state_changing=False,
                target=f"{context}/{namespace}",
                purpose=f"Collect {target_label} PostgresCluster, pod, PVC, and service inventory before rebuild.",
                argv=inventory_rebuild_argv(context, namespace, cluster),
                expected_output="Inventory only; confirm PVC selector returns only pgdata and pgwal PVCs for the target cluster.",
            ),
            command_step(
                step_id=f"shutdown-{prefix}-before-rebuild",
                title=f"Shutdown {target_label}",
                risk="High",
                state_changing=True,
                target=f"{context}/{namespace}/{cluster}",
                purpose=f"Ensure {target_label} PGO workloads are stopped before deleting stale PostgreSQL PVCs.",
                argv=patch_argv(context, namespace, cluster, {"spec": {"shutdown": True}}),
                expected_output="PostgresCluster patched with spec.shutdown=true and database pods begin stopping.",
                rollback="Patch spec.shutdown=false only if the active site has not changed and no PVC deletion was done.",
                business_justification="PVC rebuild must not happen while database pods are mounted or writable.",
                required_gate_files=[gate_file_name(active_confirm_gate)],
            ),
            command_step(
                step_id=f"manual-approve-{prefix}-pvc-delete",
                title=f"Approve {target_label} PVC Delete",
                risk="High",
                state_changing=True,
                target=f"{context}/{namespace}",
                purpose=f"Approve deletion of stale {target_label} pgdata/pgwal PVCs so PGO can restore a clean standby.",
                command=(
                    f"# MANUAL GATE: verify {target_label} database pods are stopped, inventory is saved, "
                    f"and a current {active_label} pgBackRest backup/archive chain is visible.\n"
                    f"# PVC selector: {postgres_pvc_selector(cluster)}\n"
                    f"# After formal approval, create: {gate_file_name(rebuild_gate)}"
                ),
                expected_output=f"{gate_file_name(rebuild_gate)} exists.",
                rollback="Cancel rebuild before PVC deletion. After deletion, restore from the approved pgBackRest source.",
                business_justification="Old data directory cannot safely rejoin after the active site accepted writes.",
                manual_gate=True,
                required_gate_files=[gate_file_name(active_confirm_gate)],
            ),
        ]
    )
    if include_destructive_rebuild:
        steps.extend(
            [
                command_step(
                    step_id=f"delete-{prefix}-postgres-pvcs",
                    title=f"Delete {target_label} PostgreSQL PVCs",
                    risk="High",
                    state_changing=True,
                    target=f"{context}/{namespace}",
                    purpose=f"Delete only {target_label} pgdata/pgwal PVCs selected by PGO labels.",
                    argv=delete_postgres_pvc_argv(context, namespace, cluster),
                    expected_output="Only pgdata and pgwal PVCs for the target PostgresCluster are deleted.",
                    rollback="No simple rollback. Recreate by allowing PGO to restore from pgBackRest according to the approved plan.",
                    business_justification="Force a clean standby restore from the current active site archive chain.",
                    required_gate_files=[gate_file_name(active_confirm_gate), gate_file_name(rebuild_gate)],
                ),
                command_step(
                    step_id=f"restore-{prefix}-as-standby",
                    title=f"Restore {target_label} As Standby",
                    risk="High",
                    state_changing=True,
                    target=f"{context}/{namespace}/{cluster}",
                    purpose=f"Patch {target_label} to restore from pgBackRest and follow {active_label} as standby.",
                    argv=patch_argv(context, namespace, cluster, standby_restore_patch(cfg, host=standby_host, target_secret=target_secret)),
                    expected_output=f"PGO recreates {target_label} pods from pgBackRest and leaves PostgreSQL in recovery.",
                    rollback="If restore fails, keep target stopped and review PGO restore job/pgBackRest logs before retry.",
                    business_justification=f"Re-establish {target_label} as standby of active {active_label}.",
                    required_gate_files=[gate_file_name(active_confirm_gate), gate_file_name(rebuild_gate)],
                ),
                command_step(
                    step_id=f"remediate-{prefix}-phantom-timelines",
                    title=f"Remediate {target_label} Phantom Timeline History",
                    risk="High",
                    state_changing=True,
                    target=f"{context}/{namespace}/{cluster}",
                    purpose=(
                        f"Detect and remove any phantom timeline-history file left in the shared "
                        f"pgBackRest S3 archive by the {target_label} standby restore (and any locally "
                        f"cached copies on {target_label} pods) before declaring {target_label} standby ready."
                    ),
                    command=f"# internal_action: remediate_phantom_timelines (mode-aware target={prefix})",
                    expected_output=(
                        f"No phantom timeline-history found for {target_label} (no-op), or the phantom "
                        f".history file is removed from S3 and from pg_wal/archive_status on every "
                        f"{target_label} pod, affected pods are recycled, and all {target_label} pods "
                        f"converge to streaming on the restored timeline."
                    ),
                    rollback=(
                        "No destructive rollback needed: a detected phantom file is an artifact of this "
                        "restore, not legitimate WAL history. If pods do not converge afterwards, "
                        "diagnostic state is preserved in phantom_timeline_remediation_*.json for manual "
                        "follow-up per HANDOFF_20260611_NEXT_RUN.md."
                    ),
                    business_justification=(
                        f"Prevents {target_label} from crash-looping with 'requested timeline N+1 does not "
                        f"contain minimum recovery point ... on timeline N' (phase 2 incident, 2026-06-11) "
                        f"without manual S3/pod intervention."
                    ),
                    internal_action="remediate_phantom_timelines",
                    required_gate_files=[gate_file_name(active_confirm_gate), gate_file_name(rebuild_gate)],
                ),
            ]
        )
    else:
        steps.append(
            command_step(
                step_id=f"manual-run-{prefix}-rebuild",
                title=f"Manual {target_label} Rebuild",
                risk="High",
                state_changing=True,
                target=f"{context}/{namespace}/{cluster}",
                purpose="Destructive PVC delete and standby restore commands are hidden until --include-destructive-rebuild is used.",
                command=(
                    f"# Re-run generate/dry-run with --include-destructive-rebuild after approval to include:\n"
                    f"#   oc delete pvc -n {namespace} -l {postgres_pvc_selector(cluster)}\n"
                    f"#   oc patch postgrescluster {cluster} -n {namespace} --type=merge -p <standby restore patch>"
                ),
                expected_output="No automatic destructive action in this manifest.",
                rollback="Cancel rebuild; keep current active site unchanged.",
                business_justification="Destructive rebuild commands require explicit inclusion.",
                manual_gate=True,
                required_gate_files=[gate_file_name(active_confirm_gate), gate_file_name(rebuild_gate)],
                automatically_executable=False,
            )
        )
    steps.extend(
        [
            command_step(
                step_id=f"verify-{prefix}-standby-resources",
                title=f"Verify {target_label} Standby Resources",
                risk="Low",
                state_changing=False,
                target=f"{context}/{namespace}",
                purpose=f"Check {target_label} pods/PVCs after standby restore begins.",
                argv=inventory_rebuild_argv(context, namespace, cluster),
                expected_output="Database pods are recreated and eventually Ready/in recovery.",
            ),
            command_step(
                step_id=f"manual-confirm-{prefix}-standby-ready",
                title=f"Confirm {target_label} Standby Ready",
                risk="High",
                state_changing=True,
                target=f"{target_label} PostgreSQL",
                purpose=f"Confirm {target_label} is in recovery, following {active_label}, and has acceptable lag.",
                command=(
                    f"# MANUAL GATE: verify all {target_label} DB pods are healthy, pg_is_in_recovery()=true, "
                    f"and lag to {active_label} is within policy.\n"
                    f"# After evidence is approved, create: {gate_file_name(ready_gate)}"
                ),
                expected_output=f"{gate_file_name(ready_gate)} exists.",
                rollback="Keep active site unchanged; troubleshoot standby restore.",
                business_justification="Do not use the rebuilt site for switchback until standby health and lag are proven.",
                manual_gate=True,
            ),
        ]
    )


def generate_steps(
    cfg: RuntimeConfig,
    mode: str,
    precheck: dict[str, Any] | None,
    include_destructive_switchback: bool = False,
) -> list[CommandStep]:
    data = (precheck or {}).get("data", {})
    prod_data = data.get("prod", {})
    dr_data = data.get("dr", {})
    prod_primary = prod_data.get("primary_pod") or cfg.known_prod_primary_pod
    dr_probe = dr_data.get("probe_pod") or cfg.known_dr_standby_leader_pod
    prod_lsn_argv = psql_argv(cfg, cfg.prod_context, cfg.prod_namespace, prod_primary, "select pg_current_wal_lsn()::text;")
    dr_replay_argv = psql_argv(cfg, cfg.dr_context, cfg.dr_namespace, dr_probe, "select pg_last_wal_replay_lsn()::text;")
    final_lag_command = "\n".join(
        [
            shell_join(prod_lsn_argv),
            shell_join(dr_replay_argv),
            "# prod_dr_cutover.py computes pg_wal_lsn_diff(prod_lsn, dr_replay_lsn) on the PROD primary.",
        ]
    )
    switch_wal_argv = psql_argv(cfg, cfg.prod_context, cfg.prod_namespace, prod_primary, "select pg_switch_wal()::text;")
    checkpoint_argv = psql_argv(cfg, cfg.prod_context, cfg.prod_namespace, prod_primary, "checkpoint;")
    archive_sample_argv = psql_argv(cfg, cfg.prod_context, cfg.prod_namespace, prod_primary, sql_archiver_summary())

    steps: list[CommandStep] = []

    steps.append(
        command_step(
            step_id="login-prod",
            title="Login to PROD API",
            risk="Medium",
            state_changing=False,
            target=cfg.prod_api,
            purpose="Authenticate to the PROD OpenShift API if the session expired.",
            command=f"oc login {shlex.quote(cfg.prod_api)} -u {shlex.quote(cfg.oc_user)}",
            expected_output="Login succeeds without printing credentials.",
            manual_gate=True,
        )
    )
    steps.append(
        command_step(
            step_id="login-dr",
            title="Login to DR API",
            risk="Medium",
            state_changing=False,
            target=cfg.dr_api,
            purpose="Authenticate to the DR OpenShift API if the session expired.",
            command=f"oc login {shlex.quote(cfg.dr_api)} -u {shlex.quote(cfg.oc_user)}",
            expected_output="Login succeeds without printing credentials.",
            manual_gate=True,
        )
    )

    steps.extend(
        [
            command_step(
                step_id="verify-prod-project",
                title="Verify PROD Context",
                risk="Low",
                state_changing=False,
                target=f"{cfg.prod_context}/{cfg.prod_namespace}",
                purpose="Confirm the PROD context and namespace before any action.",
                argv=oc_base(cfg.prod_context) + ["project", "-q"],
                expected_output=cfg.prod_namespace,
            ),
            command_step(
                step_id="verify-dr-project",
                title="Verify DR Context",
                risk="Low",
                state_changing=False,
                target=f"{cfg.dr_context}/{cfg.dr_namespace}",
                purpose="Confirm the DR context and namespace before any action.",
                argv=oc_base(cfg.dr_context) + ["project", "-q"],
                expected_output=cfg.dr_namespace,
            ),
            command_step(
                step_id="final-prod-lsn",
                title="Final PROD LSN",
                risk="Low",
                state_changing=False,
                target=f"{cfg.prod_context}/{cfg.prod_namespace}/{prod_primary}",
                purpose="Capture final PROD WAL LSN before cutover.",
                argv=prod_lsn_argv,
                expected_output="One WAL LSN.",
            ),
            command_step(
                step_id="final-dr-replay-lsn",
                title="Final DR Replay LSN",
                risk="Low",
                state_changing=False,
                target=f"{cfg.dr_context}/{cfg.dr_namespace}/{dr_probe}",
                purpose="Capture final DR replay LSN before promotion.",
                argv=dr_replay_argv,
                expected_output="One WAL LSN matching PROD for planned switchover.",
            ),
            command_step(
                step_id="prod-patroni-list",
                title="PROD Patroni List",
                risk="Low",
                state_changing=False,
                target=f"{cfg.prod_context}/{cfg.prod_namespace}/{prod_primary}",
                purpose="Verify PROD Patroni state before cutover.",
                argv=patroni_argv(cfg, cfg.prod_context, cfg.prod_namespace, prod_primary),
                expected_output="One Leader and one Sync Standby, no pending restart.",
            ),
            command_step(
                step_id="dr-patroni-list",
                title="DR Patroni List",
                risk="Low",
                state_changing=False,
                target=f"{cfg.dr_context}/{cfg.dr_namespace}/{dr_probe}",
                purpose="Verify DR standby state before promotion.",
                argv=patroni_argv(cfg, cfg.dr_context, cfg.dr_namespace, dr_probe),
                expected_output="Standby Leader and Replica in archive recovery before promotion.",
            ),
            command_step(
                step_id="manual-freeze-apps",
                title="Freeze Application Writers",
                risk="High",
                state_changing=True,
                target="Application routing / app namespaces",
                purpose="Stop all application writes to PROD before any planned switchover.",
                command=(
                    "# MANUAL GATE: freeze application writes and confirm no sessions are writing to PROD.\n"
                    f"# After formal approval/evidence, create: {gate_file_name(APPLICATION_WRITES_FROZEN_GATE)}"
                ),
                expected_output=f"Application owners confirm write traffic is stopped; {gate_file_name(APPLICATION_WRITES_FROZEN_GATE)} exists.",
                rollback="Unfreeze application writers if cutover is cancelled before PROD shutdown.",
                business_justification="Prevent split-brain and prevent writes after final LSN validation.",
                manual_gate=True,
            ),
        ]
    )

    if mode != "planned-switchover":
        keep_step_ids = {"login-prod", "login-dr", "verify-prod-project", "verify-dr-project"}
        steps = [step for step in steps if step.id in keep_step_ids]

    if mode == "planned-switchover":
        steps.extend(
            [
                command_step(
                    step_id="switch-wal-and-check-archive",
                    title="Switch WAL And Check Archive",
                    risk="Medium",
                    state_changing=True,
                    target=f"{cfg.prod_context}/{cfg.prod_namespace}/{prod_primary}",
                    purpose="Force a checkpoint, switch WAL, and sample pg_stat_archiver before cutover.",
                    command="\n".join([shell_join(checkpoint_argv), shell_join(switch_wal_argv), shell_join(archive_sample_argv)]),
                    expected_output="CHECKPOINT completes, pg_switch_wal returns an LSN, and pg_stat_archiver shows a recent archived WAL.",
                    rollback="No data rollback required; this only advances WAL/checkpoint state.",
                    business_justification="Confirm the WAL archive path is advancing before planned cutover.",
                    required_gate_files=[gate_file_name(APPLICATION_WRITES_FROZEN_GATE)],
                    internal_action="switch_wal_and_check_archive",
                ),
                command_step(
                    step_id="final-lag-check",
                    title="Fresh Final Lag Check",
                    risk="Low",
                    state_changing=False,
                    target=f"{cfg.prod_context}/{cfg.prod_namespace}/{prod_primary} and {cfg.dr_context}/{cfg.dr_namespace}/{dr_probe}",
                    purpose="Compute fresh PROD current LSN versus DR replay LSN after application writes are frozen.",
                    command=final_lag_command,
                    expected_output=f"final_lag.json is written and {gate_file_name(FINAL_LAG_APPROVED_GATE)} is created when lag is within threshold.",
                    rollback="If lag is above threshold, keep applications frozen or abort before PROD shutdown.",
                    business_justification="Prevent promotion using stale precheck lag data.",
                    required_gate_files=[gate_file_name(APPLICATION_WRITES_FROZEN_GATE)],
                    internal_action="final_lag_check",
                ),
                command_step(
                    step_id="shutdown-prod",
                    title="Shutdown PROD PGO Cluster",
                    risk="High",
                    state_changing=True,
                    target=f"{cfg.prod_context}/{cfg.prod_namespace}/{cfg.prod_cluster}",
                    purpose="Scale PROD Postgres workloads to zero before DR promotion.",
                    argv=patch_argv(
                        cfg.prod_context,
                        cfg.prod_namespace,
                        cfg.prod_cluster,
                        {"spec": {"shutdown": True}},
                    ),
                    expected_output="PostgresCluster patched; workloads begin scaling to zero.",
                    rollback=(
                        "If DR has not been promoted, patch PROD with "
                        "'{\"spec\":{\"shutdown\":false}}'. Do not do this after DR accepts writes."
                    ),
                    business_justification="Required by PGO standby promotion flow to avoid split-brain.",
                    required_gate_files=[
                        gate_file_name(APPLICATION_WRITES_FROZEN_GATE),
                        gate_file_name(FINAL_LAG_APPROVED_GATE),
                    ],
                ),
                command_step(
                    step_id="wait-prod-stopped",
                    title="Wait For PROD Pods Stopped",
                    risk="Low",
                    state_changing=False,
                    target=f"{cfg.prod_context}/{cfg.prod_namespace}",
                    purpose="Wait until PROD database pods are not Running or Pending after the shutdown patch.",
                    command=shell_join(
                        oc_base(cfg.prod_context)
                        + [
                            "get",
                            "pods",
                            "-n",
                            cfg.prod_namespace,
                            "-l",
                            "postgres-operator.crunchydata.com/cluster=" + cfg.prod_cluster,
                            "-o",
                            "json",
                        ]
                    ),
                    expected_output=f"No running/pending database pods; {gate_file_name(PROD_FENCED_OR_SHUTDOWN_GATE)} is created.",
                    rollback="If pods do not stop, do not promote DR; investigate PGO shutdown/fencing.",
                    business_justification="PGO standby promotion is safe only after the active primary is inactive.",
                    required_gate_files=[gate_file_name(APPLICATION_WRITES_FROZEN_GATE)],
                    internal_action="wait_prod_stopped",
                ),
                command_step(
                    step_id="verify-prod-workloads-zero",
                    title="Verify PROD Workloads Stopped",
                    risk="Low",
                    state_changing=False,
                    target=f"{cfg.prod_context}/{cfg.prod_namespace}",
                    purpose="Verify PGO scaled PROD workloads down after shutdown patch.",
                    argv=oc_base(cfg.prod_context)
                    + [
                        "get",
                        "deploy,sts,cronjob",
                        "-n",
                        cfg.prod_namespace,
                        "--selector=postgres-operator.crunchydata.com/cluster=" + cfg.prod_cluster,
                    ],
                    expected_output="Postgres/PgBouncer workloads show 0 ready or suspended.",
                ),
                command_step(
                    step_id="promote-dr",
                    title="Promote DR PGO Cluster",
                    risk="High",
                    state_changing=True,
                    target=f"{cfg.dr_context}/{cfg.dr_namespace}/{cfg.dr_cluster}",
                    purpose="Disable DR standby mode so the standby leader promotes and accepts writes.",
                    argv=patch_argv(
                        cfg.dr_context,
                        cfg.dr_namespace,
                        cfg.dr_cluster,
                        {"spec": {"standby": {"enabled": False}}},
                    ),
                    expected_output="PostgresCluster patched; DR standby leader promotes to primary.",
                    rollback="No simple rollback after writes are accepted. Switchback must be planned separately.",
                    business_justification="Activate DR as the writable production database after PROD is shut down.",
                    required_gate_files=[
                        gate_file_name(APPLICATION_WRITES_FROZEN_GATE),
                        gate_file_name(PROD_FENCED_OR_SHUTDOWN_GATE),
                        gate_file_name(FINAL_LAG_APPROVED_GATE),
                    ],
                ),
                command_step(
                    step_id="verify-dr-writable-primary",
                    title="Verify DR Primary",
                    risk="Low",
                    state_changing=False,
                    target=f"{cfg.dr_context}/{cfg.dr_namespace}/{dr_probe}",
                    purpose="Verify DR has a writable primary after promotion.",
                    argv=psql_argv(cfg, cfg.dr_context, cfg.dr_namespace, dr_probe, "select pg_is_in_recovery();"),
                    expected_output="f",
                ),
                command_step(
                    step_id="manual-route-apps-dr",
                    title="Route Applications to DR",
                    risk="High",
                    state_changing=True,
                    target="Application routing / DNS / secrets",
                    purpose="Point applications to DR database endpoint after DR promotion verification.",
                    command="# MANUAL GATE: update application routing/DNS/config to use DR PgBouncer or DR primary endpoint.",
                    expected_output="Applications connect to DR and health checks pass.",
                    rollback="Route applications back only if DR has not accepted writes, otherwise stop and plan recovery.",
                    business_justification="Complete planned switchover of application traffic to DR.",
                    manual_gate=True,
                ),
            ]
        )
    elif mode == "disaster-failover":
        steps.extend(
            [
                command_step(
                    step_id="manual-confirm-prod-fenced",
                    title="Confirm PROD Fenced",
                    risk="High",
                    state_changing=True,
                    target="PROD DC / routing / storage / OpenShift",
                    purpose="Confirm PROD cannot accept writes before DR is promoted.",
                    command=(
                        "# MANUAL GATE: confirm PROD is down, fenced, isolated, or formally declared lost.\n"
                        f"# After formal approval/evidence, create: {gate_file_name(PROD_FENCED_OR_SHUTDOWN_GATE)}"
                    ),
                    expected_output=f"Incident commander confirms PROD cannot write to the shared pgBackRest repo; {gate_file_name(PROD_FENCED_OR_SHUTDOWN_GATE)} exists.",
                    rollback="None if PROD is lost. If PROD is reachable, use planned switchover instead.",
                    business_justification="Avoid split-brain when promoting DR during a disaster.",
                    manual_gate=True,
                ),
                command_step(
                    step_id="promote-dr-disaster",
                    title="Promote DR During Disaster",
                    risk="High",
                    state_changing=True,
                    target=f"{cfg.dr_context}/{cfg.dr_namespace}/{cfg.dr_cluster}",
                    purpose="Disable DR standby mode when PROD is confirmed unavailable/fenced.",
                    argv=patch_argv(
                        cfg.dr_context,
                        cfg.dr_namespace,
                        cfg.dr_cluster,
                        {"spec": {"standby": {"enabled": False}}},
                    ),
                    expected_output="DR promotes and starts accepting writes.",
                    rollback="No simple rollback after writes are accepted. Rebuild old PROD as standby later.",
                    business_justification="Restore database write availability from DR during PROD disaster.",
                    required_gate_files=[gate_file_name(PROD_FENCED_OR_SHUTDOWN_GATE)],
                ),
            ]
        )
    elif mode == "rebuild-dc1-standby":
        dc2_host = require_non_empty(cfg.dr_primary_lb, "--dr-primary-lb")
        append_rebuild_standby_steps(
            steps,
            cfg,
            prefix="dc1",
            target_label="DC1",
            active_label="DC2",
            context=cfg.prod_context,
            namespace=cfg.prod_namespace,
            cluster=cfg.prod_cluster,
            target_secret=cfg.prod_pgbackrest_secret,
            standby_host=dc2_host,
            active_confirm_gate=DC2_ACTIVE_CONFIRMED_GATE,
            rebuild_gate=DC1_REBUILD_APPROVED_GATE,
            ready_gate=DC1_STANDBY_READY_GATE,
            include_destructive_rebuild=include_destructive_switchback,
        )
    elif mode == "switchback":
        steps.extend(
            [
                command_step(
                    step_id="manual-confirm-dc1-standby-ready",
                    title="Confirm DC1 Standby Ready",
                    risk="High",
                    state_changing=True,
                    target="DC1 PostgreSQL",
                    purpose="Confirm rebuilt DC1 is a healthy standby of active DC2 before switchback.",
                    command=(
                        "# MANUAL GATE: verify DC1 pg_is_in_recovery()=true, follows DC2, and lag is within policy.\n"
                        f"# After approval/evidence, create: {gate_file_name(DC1_STANDBY_READY_GATE)}"
                    ),
                    expected_output=f"{gate_file_name(DC1_STANDBY_READY_GATE)} exists.",
                    rollback="Cancel switchback; keep DC2 active.",
                    business_justification="Switchback requires DC1 to be fully rebuilt and caught up first.",
                    manual_gate=True,
                ),
                command_step(
                    step_id="manual-freeze-apps-dc2",
                    title="Freeze DC2 Application Writers",
                    risk="High",
                    state_changing=True,
                    target="Application routing / app namespaces",
                    purpose="Stop all application writes to active DC2 before switchback.",
                    command=(
                        "# MANUAL GATE: freeze application writes and confirm no sessions are writing to DC2.\n"
                        f"# After approval/evidence, create: {gate_file_name(APPLICATION_WRITES_FROZEN_GATE)}"
                    ),
                    expected_output=f"{gate_file_name(APPLICATION_WRITES_FROZEN_GATE)} exists.",
                    rollback="Unfreeze DC2 writers if switchback is cancelled before DC2 shutdown.",
                    business_justification="Prevent split-brain and prevent writes after final reverse lag validation.",
                    manual_gate=True,
                    required_gate_files=[gate_file_name(DC1_STANDBY_READY_GATE)],
                ),
                command_step(
                    step_id="reverse-final-lag-check",
                    title="Final DC2 To DC1 Lag Check",
                    risk="Low",
                    state_changing=False,
                    target="DC2 active and DC1 standby",
                    purpose="Compute fresh DC2 current LSN versus rebuilt DC1 replay LSN after writes are frozen.",
                    command="# Internal read-only action: computes pg_wal_lsn_diff(dc2_lsn, dc1_replay_lsn).",
                    expected_output=f"reverse_final_lag.json is written and {gate_file_name(FINAL_LAG_APPROVED_GATE)} is created when lag is within threshold.",
                    rollback="If lag is not zero, keep applications frozen or abort before DC2 shutdown.",
                    business_justification="Prevent promotion of stale DC1 data.",
                    required_gate_files=[gate_file_name(APPLICATION_WRITES_FROZEN_GATE), gate_file_name(DC1_STANDBY_READY_GATE)],
                    internal_action="reverse_final_lag_check",
                ),
                command_step(
                    step_id="shutdown-dc2-active",
                    title="Shutdown DC2 Active Cluster",
                    risk="High",
                    state_changing=True,
                    target=f"{cfg.dr_context}/{cfg.dr_namespace}/{cfg.dr_cluster}",
                    purpose="Stop active DC2 before promoting DC1 back to writable primary.",
                    argv=patch_argv(cfg.dr_context, cfg.dr_namespace, cfg.dr_cluster, {"spec": {"shutdown": True}}),
                    expected_output="DC2 PostgresCluster patched with spec.shutdown=true.",
                    rollback="If DC1 has not been promoted, patch DC2 shutdown=false. Do not do this after DC1 accepts writes.",
                    business_justification="DC2 must be fenced before DC1 promotion to avoid split-brain.",
                    required_gate_files=[gate_file_name(APPLICATION_WRITES_FROZEN_GATE), gate_file_name(FINAL_LAG_APPROVED_GATE), gate_file_name(DC1_STANDBY_READY_GATE)],
                ),
                command_step(
                    step_id="manual-confirm-dc2-stopped",
                    title="Confirm DC2 Stopped",
                    risk="High",
                    state_changing=True,
                    target=f"{cfg.dr_context}/{cfg.dr_namespace}",
                    purpose="Confirm DC2 database pods are stopped or fenced before DC1 promotion.",
                    command=(
                        "# MANUAL GATE: verify no DC2 database pod can accept writes.\n"
                        f"# After approval/evidence, create: {gate_file_name(DC2_FENCED_OR_SHUTDOWN_GATE)}"
                    ),
                    expected_output=f"{gate_file_name(DC2_FENCED_OR_SHUTDOWN_GATE)} exists.",
                    rollback="If DC2 did not stop, do not promote DC1; troubleshoot shutdown/fencing.",
                    business_justification="Promotion is safe only after active DC2 is inactive.",
                    manual_gate=True,
                    required_gate_files=[gate_file_name(APPLICATION_WRITES_FROZEN_GATE), gate_file_name(FINAL_LAG_APPROVED_GATE)],
                ),
                command_step(
                    step_id="promote-dc1",
                    title="Promote DC1",
                    risk="High",
                    state_changing=True,
                    target=f"{cfg.prod_context}/{cfg.prod_namespace}/{cfg.prod_cluster}",
                    purpose="Disable DC1 standby mode so rebuilt DC1 promotes and accepts writes.",
                    argv=patch_argv(cfg.prod_context, cfg.prod_namespace, cfg.prod_cluster, {"spec": {"standby": {"enabled": False}}}),
                    expected_output="DC1 PostgresCluster patched; DC1 promotes to writable primary.",
                    rollback="No simple rollback after DC1 accepts writes. Keep DC2 stopped and rebuild it as standby.",
                    business_justification="Return writable production database to DC1 after DC2 is fenced.",
                    required_gate_files=[gate_file_name(APPLICATION_WRITES_FROZEN_GATE), gate_file_name(FINAL_LAG_APPROVED_GATE), gate_file_name(DC2_FENCED_OR_SHUTDOWN_GATE), gate_file_name(DC1_STANDBY_READY_GATE)],
                ),
                command_step(
                    step_id="manual-route-apps-dc1",
                    title="Route Applications To DC1",
                    risk="High",
                    state_changing=True,
                    target="Application routing / DNS / secrets",
                    purpose="Point applications back to DC1 after DC1 promotion verification.",
                    command=(
                        "# MANUAL GATE: update application routing/DNS/config to use DC1 PgBouncer or primary endpoint.\n"
                        f"# After validation, create: {gate_file_name(DC1_ACTIVE_CONFIRMED_GATE)}"
                    ),
                    expected_output=f"Applications connect to DC1 and {gate_file_name(DC1_ACTIVE_CONFIRMED_GATE)} exists.",
                    rollback="Do not route back to DC2 after DC1 accepts writes; rebuild DC2 as standby.",
                    business_justification="Complete switchback of application traffic to DC1.",
                    manual_gate=True,
                    required_gate_files=[gate_file_name(DC2_FENCED_OR_SHUTDOWN_GATE)],
                ),
            ]
        )
    elif mode == "rebuild-dc2-standby":
        append_rebuild_standby_steps(
            steps,
            cfg,
            prefix="dc2",
            target_label="DC2",
            active_label="DC1",
            context=cfg.dr_context,
            namespace=cfg.dr_namespace,
            cluster=cfg.dr_cluster,
            target_secret=cfg.dr_pgbackrest_secret,
            standby_host=cfg.prod_primary_lb,
            active_confirm_gate=DC1_ACTIVE_CONFIRMED_GATE,
            rebuild_gate=DC2_REBUILD_APPROVED_GATE,
            ready_gate=DC2_STANDBY_READY_GATE,
            include_destructive_rebuild=include_destructive_switchback,
        )
    elif mode == "full-lifecycle-plan":
        steps.extend(
            [
                command_step(
                    step_id="manual-full-lifecycle-sequence",
                    title="Full Lifecycle Sequence",
                    risk="High",
                    state_changing=True,
                    target="DC1 and DC2",
                    purpose="Document the required sequence for a complete DR drill lifecycle.",
                    command=(
                        "# Execute as separate reviewed phases, not as one blind run:\n"
                        "# 1. planned-switchover: DC1 -> DC2\n"
                        "# 2. rebuild-dc1-standby: rebuild old DC1 from active DC2/S3\n"
                        "# 3. switchback: DC2 -> rebuilt DC1\n"
                        "# 4. rebuild-dc2-standby: rebuild old DC2 from active DC1/S3"
                    ),
                    expected_output="Four separate manifests with evidence and gates for each phase.",
                    rollback="Stop at the last confirmed active site; do not start the old site writable.",
                    business_justification="End-to-end DR drill without split-brain.",
                    manual_gate=True,
                ),
            ]
        )
    return steps


def write_command_review(path: pathlib.Path, manifest: dict[str, Any]) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "# Review file generated by prod_dr_cutover.py.",
        "# This is a review-only file. Execute approved steps with prod_dr_cutover.py execute --step.",
        "# No passwords or secret objects are included.",
        "set -euo pipefail",
        "echo 'Review-only file. Use prod_dr_cutover.py execute --step for approved actions.' >&2",
        "exit 99",
        "",
        f"# run_id: {manifest['run_id']}",
        f"# mode: {manifest['mode']}",
        f"# generated_at: {manifest['generated_at']}",
        "",
    ]
    for step in manifest["steps"]:
        lines.extend(
            [
                "",
                "################################################################################",
                f"# {step['id']}: {step['title']}",
                f"# Risk: {step['risk']}",
                f"# State changing: {step['state_changing']}",
                f"# Requires approval: {step['requires_approval']}",
                f"# Automatically executable by this script: {step.get('automatically_executable', not step.get('manual_gate'))}",
                f"# Required gate files: {', '.join(step.get('required_gate_files') or []) or '(none)'}",
                f"# Target: {step['target']}",
                f"# Purpose: {step['purpose']}",
            ]
        )
        if step.get("expected_output"):
            lines.append(f"# Expected: {step['expected_output']}")
        if step.get("rollback"):
            lines.append(f"# Rollback: {step['rollback']}")
        if step.get("business_justification"):
            lines.append(f"# Business justification: {step['business_justification']}")
        if step.get("manual_gate"):
            lines.append(step["command"])
        else:
            if step["state_changing"]:
                lines.append("# APPROVAL REQUIRED BEFORE RUNNING THIS COMMAND")
                lines.extend("# " + line if line else "#" for line in step["command"].splitlines())
            else:
                lines.append(step["command"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_manifest(
    cfg: RuntimeConfig,
    *,
    mode: str,
    run_dir: pathlib.Path,
    precheck: dict[str, Any] | None,
    include_destructive_switchback: bool,
    uses_stale_pod_fallback: bool,
) -> dict[str, Any]:
    run_id = (precheck or {}).get("run_id") or str(uuid.uuid4())
    steps = generate_steps(cfg, mode, precheck, include_destructive_switchback)
    return {
        "script_version": SCRIPT_VERSION,
        "run_id": run_id,
        "mode": mode,
        "generated_at": now_utc(),
        "run_dir": str(run_dir),
        "config": asdict(cfg),
        "precheck_status": (precheck or {}).get("status"),
        "precheck_file": str(run_dir / "precheck.json") if precheck else None,
        "uses_stale_pod_fallback": uses_stale_pod_fallback,
        "approval_token": run_id,
        "steps": [asdict(step) for step in steps],
    }


def generate_command_files(
    args: argparse.Namespace,
    cfg: RuntimeConfig,
    run_dir: pathlib.Path,
    precheck: dict[str, Any] | None,
) -> dict[str, Any]:
    if precheck and precheck.get("status") != "PASS" and not args.force:
        raise CutoverError(
            f"Precheck status is {precheck.get('status')}; refusing to generate state-changing plan without --force"
        )
    uses_stale_pod_fallback = not has_live_precheck_pod_data(precheck)
    if (
        getattr(args, "command", "") == "generate"
        and args.force
        and uses_stale_pod_fallback
        and not getattr(args, "allow_stale_pod_fallback", False)
    ):
        raise CutoverError(
            "No live precheck data available; refusing to generate pod-specific command steps from stale defaults."
        )
    manifest = build_manifest(
        cfg,
        mode=args.mode,
        run_dir=run_dir,
        precheck=precheck,
        include_destructive_switchback=(
            getattr(args, "include_destructive_switchback", False)
            or getattr(args, "include_destructive_rebuild", False)
        ),
        uses_stale_pod_fallback=uses_stale_pod_fallback,
    )
    manifest_path = run_dir / "command_manifest.json"
    review_path = run_dir / "commands_review.sh"
    safe_json_dump(manifest, manifest_path)
    write_command_review(review_path, manifest)
    decision = {
        "script_version": SCRIPT_VERSION,
        "run_id": manifest["run_id"],
        "mode": args.mode,
        "generated_at": manifest["generated_at"],
        "precheck_status": manifest["precheck_status"],
        "uses_stale_pod_fallback": manifest["uses_stale_pod_fallback"],
        "manifest_sha256": sha256_file(manifest_path),
        "commands_review_sha256": sha256_file(review_path),
        "next_required_action": "Review command_manifest.json and commands_review.sh. State-changing commands need approval.",
    }
    safe_json_dump(decision, run_dir / "decision.json")
    return manifest


def load_precheck(path: str | None, run_dir: pathlib.Path) -> dict[str, Any] | None:
    candidate = pathlib.Path(path) if path else run_dir / "precheck.json"
    if not candidate.exists():
        return None
    return json.loads(candidate.read_text(encoding="utf-8"))


def has_live_precheck_pod_data(precheck: dict[str, Any] | None) -> bool:
    if not precheck:
        return False
    data = precheck.get("data") or {}
    prod = data.get("prod") or {}
    dr = data.get("dr") or {}
    return bool(prod.get("primary_pod") and dr.get("probe_pod"))


def command_is_allowed_for_execute(step: dict[str, Any], args: argparse.Namespace, manifest: dict[str, Any]) -> tuple[bool, str]:
    if step.get("manual_gate"):
        return False, "manual gate"
    if not step.get("automatically_executable", True):
        return False, "step is marked manual-only"
    if step.get("state_changing"):
        if not args.execute_state_changing:
            return False, "state-changing step requires --execute-state-changing"
        if args.approval_token != manifest.get("approval_token"):
            return False, "approval token mismatch"
        if step.get("risk") == "High" and not args.confirm_prod_impact:
            return False, "high-risk step requires --confirm-prod-impact"
    return True, "allowed"


def load_precheck_for_manifest(manifest: dict[str, Any], run_dir: pathlib.Path) -> dict[str, Any] | None:
    candidates = []
    if manifest.get("precheck_file"):
        candidates.append(pathlib.Path(str(manifest["precheck_file"])))
    candidates.append(run_dir / "precheck.json")
    for candidate in candidates:
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def require_live_manifest_pods(manifest: dict[str, Any], run_dir: pathlib.Path) -> tuple[str, str]:
    precheck = load_precheck_for_manifest(manifest, run_dir)
    if not has_live_precheck_pod_data(precheck):
        raise CutoverError(
            "No live precheck data available; refusing to execute pod-specific command steps from stale defaults."
        )
    data = precheck.get("data", {}) if precheck else {}
    return data["prod"]["primary_pod"], data["dr"]["probe_pod"]


def require_precheck_checks_pass(manifest: dict[str, Any], run_dir: pathlib.Path, check_names: list[str]) -> None:
    precheck = load_precheck_for_manifest(manifest, run_dir)
    if not precheck:
        raise CutoverError("Required precheck evidence is missing from the manifest run directory.")
    checks = {check.get("name"): check for check in precheck.get("checks", [])}
    failures = []
    for name in check_names:
        check = checks.get(name)
        if not check:
            failures.append(f"{name}=MISSING")
        elif check.get("status") != "PASS":
            failures.append(f"{name}={check.get('status')} ({check.get('detail')})")
    if failures:
        raise CutoverError("Required precheck check(s) not PASS before promotion: " + "; ".join(failures))


def infer_step_namespace(cfg: RuntimeConfig, step: dict[str, Any]) -> str | None:
    text_parts = [
        str(step.get("id") or ""),
        str(step.get("target") or ""),
        str(step.get("purpose") or ""),
        str(step.get("command") or ""),
        " ".join(str(item) for item in (step.get("argv") or [])),
    ]
    haystack = "\n".join(text_parts)
    has_prod = cfg.prod_namespace in haystack or cfg.prod_cluster in haystack
    has_dr = cfg.dr_namespace in haystack or cfg.dr_cluster in haystack
    if has_prod and not has_dr:
        return cfg.prod_namespace
    if has_dr and not has_prod:
        return cfg.dr_namespace
    return None


def switch_project_for_execute_step(
    cfg: RuntimeConfig,
    step: dict[str, Any],
    audit: Audit,
) -> None:
    if not cfg.single_context_projects:
        return
    namespace = infer_step_namespace(cfg, step)
    if not namespace:
        return
    run_command(
        ["oc", "project", namespace],
        audit=audit,
        purpose=f"Switch current project before executing {step.get('id')}",
        target=f"current-context/{namespace}",
        timeout=90,
        show_notice=False,
    )


def discover_database_recovery_states(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    cluster: str,
    audit: Audit,
) -> list[dict[str, str]]:
    selector = f"postgres-operator.crunchydata.com/cluster={cluster}"
    result = run_command(
        oc_base(context)
        + [
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            selector,
            "-o",
            "json",
        ],
        audit=audit,
        purpose="Discover live database pods",
        target=f"{context}/{namespace}",
        timeout=90,
        show_notice=False,
    )
    pods_json = json_loads_or_error(result.stdout, "database pod discovery")
    records: list[dict[str, str]] = []
    for pod in database_pods(pods_json, cluster, cfg.container):
        name = pod.get("metadata", {}).get("name", "")
        phase = pod.get("status", {}).get("phase", "")
        if phase != "Running":
            continue
        recovery = psql_scalar(
            cfg,
            context=context,
            namespace=namespace,
            pod=name,
            sql="select pg_is_in_recovery();",
            audit=audit,
            purpose=f"Discover recovery state on {name}",
            allow_fail=True,
        ).strip()
        records.append({"name": name, "phase": phase, "pg_is_in_recovery": recovery})
    return records


def list_database_pod_names(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    cluster: str,
    audit: Audit,
) -> list[str]:
    """All Running database pods of `cluster`, regardless of psql connectivity.

    Unlike discover_database_recovery_states(), this does not query postgres -
    it is used to reach pods that are crash-looping (Patroni retries the
    postgres process internally, so the pod itself stays Running).
    """
    selector = f"postgres-operator.crunchydata.com/cluster={cluster}"
    result = run_command(
        oc_base(context) + ["get", "pods", "-n", namespace, "-l", selector, "-o", "json"],
        audit=audit,
        purpose="List database pods",
        target=f"{context}/{namespace}",
        timeout=90,
        show_notice=False,
    )
    pods_json = json_loads_or_error(result.stdout, "database pod listing")
    names = []
    for pod in database_pods(pods_json, cluster, cfg.container):
        if pod.get("status", {}).get("phase") == "Running":
            names.append(pod.get("metadata", {}).get("name", ""))
    return [name for name in names if name]


def discover_current_prod_primary(cfg: RuntimeConfig, audit: Audit) -> str:
    return discover_current_primary(
        cfg,
        context=cfg.prod_context,
        namespace=cfg.prod_namespace,
        cluster=cfg.prod_cluster,
        audit=audit,
        label="PROD",
    )


def discover_current_primary(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    cluster: str,
    audit: Audit,
    label: str,
) -> str:
    records = discover_database_recovery_states(
        cfg,
        context=context,
        namespace=namespace,
        cluster=cluster,
        audit=audit,
    )
    primaries = [record["name"] for record in records if record.get("pg_is_in_recovery") == "f"]
    if len(primaries) != 1:
        raise CutoverError(f"Expected exactly one live {label} primary, found {len(primaries)}: {primaries}")
    return primaries[0]


def discover_dr_replay_probe(cfg: RuntimeConfig, audit: Audit) -> str:
    return discover_replay_probe(
        cfg,
        context=cfg.dr_context,
        namespace=cfg.dr_namespace,
        cluster=cfg.dr_cluster,
        audit=audit,
        label="DR",
    )


def discover_replay_probe(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    cluster: str,
    audit: Audit,
    label: str,
) -> str:
    records = discover_database_recovery_states(
        cfg,
        context=context,
        namespace=namespace,
        cluster=cluster,
        audit=audit,
    )
    recovery_pods = [record["name"] for record in records if record.get("pg_is_in_recovery") == "t"]
    if not recovery_pods:
        raise CutoverError(f"No live {label} recovery pod available for replay LSN probe")
    for pod in recovery_pods:
        patroni = run_command(
            patroni_argv(cfg, context, namespace, pod),
            audit=audit,
            purpose=f"Discover {label} standby leader for replay LSN probe",
            target=f"{context}/{namespace}/{pod}",
            allow_fail=True,
        )
        preferred = select_patroni_member_by_role(patroni_summary(patroni.stdout), ["standby leader", "leader"])
        if preferred in recovery_pods:
            return preferred
    return recovery_pods[0]


def validate_lsn(label: str, value: str) -> None:
    if not value or not LSN_RE.match(value):
        raise CutoverError(f"{label} returned invalid WAL LSN: {value or 'empty'}")


def execute_final_lag_check(
    args: argparse.Namespace,
    cfg: RuntimeConfig,
    manifest: dict[str, Any],
    run_dir: pathlib.Path,
    audit: Audit,
) -> None:
    require_live_manifest_pods(manifest, run_dir)
    prod_primary = discover_current_prod_primary(cfg, audit)
    dr_probe = discover_dr_replay_probe(cfg, audit)
    prod_lsn = psql_scalar(
        cfg,
        context=cfg.prod_context,
        namespace=cfg.prod_namespace,
        pod=prod_primary,
        sql="select pg_current_wal_lsn()::text;",
        audit=audit,
        purpose="Execute final lag check: collect PROD current WAL LSN",
    )
    dr_replay_lsn = psql_scalar(
        cfg,
        context=cfg.dr_context,
        namespace=cfg.dr_namespace,
        pod=dr_probe,
        sql="select pg_last_wal_replay_lsn()::text;",
        audit=audit,
        purpose="Execute final lag check: collect DR replay WAL LSN",
    )
    validate_lsn("PROD current LSN", prod_lsn)
    validate_lsn("DR replay LSN", dr_replay_lsn)
    lag_output = psql_scalar(
        cfg,
        context=cfg.prod_context,
        namespace=cfg.prod_namespace,
        pod=prod_primary,
        sql=f"select pg_wal_lsn_diff('{prod_lsn}', '{dr_replay_lsn}')::bigint;",
        audit=audit,
        purpose="Execute final lag check: compute WAL byte lag",
    )
    lag_bytes = to_int(lag_output)
    if lag_bytes is None:
        raise CutoverError(f"Could not parse final WAL lag bytes: {lag_output}")
    if lag_bytes < 0:
        raise CutoverError(
            f"Final lag check failed: DR replay LSN is ahead of PROD current LSN "
            f"(lag_bytes={lag_bytes}). Stop and investigate timeline/divergence."
        )
    payload = {
        "created_at": now_utc(),
        "mode": manifest.get("mode"),
        "prod_primary_pod": prod_primary,
        "dr_probe_pod": dr_probe,
        "prod_current_lsn": prod_lsn,
        "dr_replay_lsn": dr_replay_lsn,
        "lag_bytes": lag_bytes,
        "max_lag_bytes": args.max_lag_bytes,
        "status": "PASS" if lag_bytes <= args.max_lag_bytes else "FAIL",
    }
    safe_json_dump(payload, run_dir / "final_lag.json")
    if lag_bytes > args.max_lag_bytes:
        raise CutoverError(f"Final lag check failed: lag_bytes={lag_bytes} exceeds max_lag_bytes={args.max_lag_bytes}")
    if manifest.get("mode") == "planned-switchover":
        gate_file = write_approval_gate(run_dir, FINAL_LAG_APPROVED_GATE, payload)
        print(f"Created approval gate: {gate_file}")
    print(f"Final lag check PASS: lag_bytes={lag_bytes} max_lag_bytes={args.max_lag_bytes}")


def execute_reverse_final_lag_check(
    args: argparse.Namespace,
    cfg: RuntimeConfig,
    manifest: dict[str, Any],
    run_dir: pathlib.Path,
    audit: Audit,
) -> None:
    dc2_primary = discover_current_primary(
        cfg,
        context=cfg.dr_context,
        namespace=cfg.dr_namespace,
        cluster=cfg.dr_cluster,
        audit=audit,
        label="DC2",
    )
    dc1_probe = discover_replay_probe(
        cfg,
        context=cfg.prod_context,
        namespace=cfg.prod_namespace,
        cluster=cfg.prod_cluster,
        audit=audit,
        label="DC1",
    )
    dc2_lsn = psql_scalar(
        cfg,
        context=cfg.dr_context,
        namespace=cfg.dr_namespace,
        pod=dc2_primary,
        sql="select pg_current_wal_lsn()::text;",
        audit=audit,
        purpose="Execute reverse final lag check: collect DC2 current WAL LSN",
    )
    dc1_replay_lsn = psql_scalar(
        cfg,
        context=cfg.prod_context,
        namespace=cfg.prod_namespace,
        pod=dc1_probe,
        sql="select pg_last_wal_replay_lsn()::text;",
        audit=audit,
        purpose="Execute reverse final lag check: collect DC1 replay WAL LSN",
    )
    validate_lsn("DC2 current LSN", dc2_lsn)
    validate_lsn("DC1 replay LSN", dc1_replay_lsn)
    lag_output = psql_scalar(
        cfg,
        context=cfg.dr_context,
        namespace=cfg.dr_namespace,
        pod=dc2_primary,
        sql=f"select pg_wal_lsn_diff('{dc2_lsn}', '{dc1_replay_lsn}')::bigint;",
        audit=audit,
        purpose="Execute reverse final lag check: compute WAL byte lag",
    )
    lag_bytes = to_int(lag_output)
    if lag_bytes is None:
        raise CutoverError(f"Could not parse reverse WAL lag bytes: {lag_output}")
    if lag_bytes < 0:
        raise CutoverError(
            f"Reverse final lag check failed: DC1 replay LSN is ahead of DC2 current LSN "
            f"(lag_bytes={lag_bytes}). Stop and investigate timeline/divergence."
        )
    payload = {
        "created_at": now_utc(),
        "mode": manifest.get("mode"),
        "dc2_primary_pod": dc2_primary,
        "dc1_probe_pod": dc1_probe,
        "dc2_current_lsn": dc2_lsn,
        "dc1_replay_lsn": dc1_replay_lsn,
        "lag_bytes": lag_bytes,
        "max_lag_bytes": args.max_lag_bytes,
        "status": "PASS" if lag_bytes <= args.max_lag_bytes else "FAIL",
    }
    safe_json_dump(payload, run_dir / "reverse_final_lag.json")
    if lag_bytes > args.max_lag_bytes:
        raise CutoverError(f"Reverse final lag check failed: lag_bytes={lag_bytes} exceeds max_lag_bytes={args.max_lag_bytes}")
    gate_file = write_approval_gate(run_dir, FINAL_LAG_APPROVED_GATE, payload)
    print(f"Created approval gate: {gate_file}")
    print(f"Reverse final lag check PASS: lag_bytes={lag_bytes} max_lag_bytes={args.max_lag_bytes}")


def wait_prod_database_pods_stopped(
    cfg: RuntimeConfig,
    *,
    run_dir: pathlib.Path,
    audit: Audit,
    timeout_seconds: int,
) -> None:
    import time

    selector = f"postgres-operator.crunchydata.com/cluster={cfg.prod_cluster}"
    deadline = time.monotonic() + timeout_seconds
    last_not_stopped: list[dict[str, str]] = []
    while True:
        result = run_command(
            oc_base(cfg.prod_context)
            + [
                "get",
                "pods",
                "-n",
                cfg.prod_namespace,
                "-l",
                selector,
                "-o",
                "json",
            ],
            audit=audit,
            purpose="Wait for PROD database pods to stop",
            target=f"{cfg.prod_context}/{cfg.prod_namespace}",
            timeout=90,
            show_notice=False,
        )
        pods_json = json_loads_or_error(result.stdout, "PROD pod inventory")
        db_pods = database_pods(pods_json, cfg.prod_cluster, cfg.container)
        last_not_stopped = []
        for pod in db_pods:
            name = pod.get("metadata", {}).get("name", "")
            phase = pod.get("status", {}).get("phase", "")
            if phase in {"Running", "Pending"}:
                last_not_stopped.append({"name": name, "phase": phase})
        payload = {
            "checked_at": now_utc(),
            "selector": selector,
            "database_pod_count": len(db_pods),
            "not_stopped": last_not_stopped,
        }
        safe_json_dump(payload, run_dir / "prod_pods_stopped.json")
        if not last_not_stopped:
            gate_file = write_approval_gate(run_dir, PROD_FENCED_OR_SHUTDOWN_GATE, payload)
            print(f"Created approval gate: {gate_file}")
            print("PROD database pods stopped/inactive.")
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CutoverError(
                "Timed out waiting for PROD database pods to stop: "
                + ", ".join(f"{item['name']} phase={item['phase']}" for item in last_not_stopped)
            )
        time.sleep(min(15, max(1, int(remaining))))


def execute_switch_wal_and_check_archive(
    args: argparse.Namespace,
    cfg: RuntimeConfig,
    manifest: dict[str, Any],
    run_dir: pathlib.Path,
    audit: Audit,
) -> None:
    require_live_manifest_pods(manifest, run_dir)
    prod_primary = discover_current_prod_primary(cfg, audit)
    run_command(
        psql_argv(cfg, cfg.prod_context, cfg.prod_namespace, prod_primary, "checkpoint;"),
        audit=audit,
        purpose="Checkpoint PROD primary before WAL switch",
        target=f"{cfg.prod_context}/{cfg.prod_namespace}/{prod_primary}",
        timeout=args.timeout,
        show_notice=False,
    )
    run_command(
        psql_argv(cfg, cfg.prod_context, cfg.prod_namespace, prod_primary, "select pg_switch_wal()::text;"),
        audit=audit,
        purpose="Switch WAL on PROD primary after checkpoint",
        target=f"{cfg.prod_context}/{cfg.prod_namespace}/{prod_primary}",
        timeout=args.timeout,
        show_notice=False,
    )
    archiver = psql_json(
        cfg,
        context=cfg.prod_context,
        namespace=cfg.prod_namespace,
        pod=prod_primary,
        sql=sql_archiver_summary(),
        audit=audit,
        purpose="Sample PROD archiver after WAL switch",
    )
    archive_age = to_int(archiver.get("archive_age_seconds"))
    payload = {
        "created_at": now_utc(),
        "prod_primary_pod": prod_primary,
        "archiver": archiver,
        "max_archive_age_seconds": args.max_archive_age_seconds,
        "status": "PASS",
    }
    if not archiver.get("last_archived_time"):
        payload["status"] = "FAIL"
        safe_json_dump(payload, run_dir / "archive_after_switch_wal.json")
        raise CutoverError("Archive check failed after WAL switch: last_archived_time is null")
    if archive_age is not None and archive_age > args.max_archive_age_seconds:
        payload["status"] = "FAIL"
        safe_json_dump(payload, run_dir / "archive_after_switch_wal.json")
        raise CutoverError(
            f"Archive check failed after WAL switch: archive_age_seconds={archive_age} "
            f"exceeds max_archive_age_seconds={args.max_archive_age_seconds}"
        )
    safe_json_dump(payload, run_dir / "archive_after_switch_wal.json")
    print(f"Archive check after WAL switch PASS: archive_age_seconds={archive_age}")


def read_pgbackrest_s3_credentials(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    pod: str,
    audit: Audit,
) -> tuple[str, str]:
    """Read repo1-s3-key / repo1-s3-key-secret from the pod's mounted pgBackRest config.

    Bypasses run_command()/audit.record_command() deliberately: these are live PROD S3
    credentials, and SECRET_REDACTIONS does not (and should not - this code needs the
    plaintext values) cover pgBackRest's repo1-s3-key* config keys.
    """
    argv = exec_bash_argv(
        cfg,
        context,
        namespace,
        pod,
        "grep -rh '^repo1-s3-key' /etc/pgbackrest/conf.d/ 2>/dev/null",
    )
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    if proc.returncode != 0:
        raise CutoverError(
            f"failed to read pgBackRest S3 credentials from {pod}: "
            f"{proc.stderr.strip() or f'exit code {proc.returncode}'}"
        )
    key_match = re.search(r"^repo1-s3-key=(\S+)$", proc.stdout, re.M)
    secret_match = re.search(r"^repo1-s3-key-secret=(\S+)$", proc.stdout, re.M)
    if not key_match or not secret_match:
        raise CutoverError(f"could not find repo1-s3-key/repo1-s3-key-secret in pgBackRest config on {pod}")
    audit.append(
        f"[{now_utc()}] Read pgBackRest S3 credentials from {context}/{namespace}/{pod} "
        f"for phantom-timeline remediation (values redacted from this log)."
    )
    return key_match.group(1), secret_match.group(1)


def build_pgbackrest_s3_client(
    cfg: RuntimeConfig,
    *,
    context: str,
    namespace: str,
    pod: str,
    audit: Audit,
) -> tuple[Any, str]:
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:
        raise CutoverError(
            "boto3 is required for phantom-timeline remediation but is not installed "
            "(pip3 install boto3)."
        ) from exc
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        pass
    access_key, secret_key = read_pgbackrest_s3_credentials(
        cfg, context=context, namespace=namespace, pod=pod, audit=audit
    )
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{cfg.pgbackrest_s3_endpoint}",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=cfg.pgbackrest_s3_region,
        verify=False,
        config=BotoConfig(s3={"addressing_style": "path"}),
    )
    return s3, cfg.pgbackrest_s3_bucket


def execute_remediate_phantom_timelines(
    args: argparse.Namespace,
    cfg: RuntimeConfig,
    manifest: dict[str, Any],
    run_dir: pathlib.Path,
    audit: Audit,
) -> None:
    """Detect and remove a phantom timeline-history file left in the SHARED pgBackRest
    S3 archive by a restore-<role>-as-standby bootstrap (see
    DESIGN_phantom_timeline_autofix.md). No-op if nothing phantom is found.
    """
    if args.mode == "rebuild-dc1-standby":
        context, namespace, cluster = cfg.prod_context, cfg.prod_namespace, cfg.prod_cluster
        prefix, target_label = "dc1", "DC1"
    elif args.mode == "rebuild-dc2-standby":
        context, namespace, cluster = cfg.dr_context, cfg.dr_namespace, cfg.dr_cluster
        prefix, target_label = "dc2", "DC2"
    else:
        raise CutoverError(f"remediate-phantom-timelines is not defined for mode={args.mode!r}")

    evidence: dict[str, Any] = {
        "created_at": now_utc(),
        "mode": args.mode,
        "target": f"{context}/{namespace}/{cluster}",
        "checked_history_files": [],
        "phantom_found": False,
        "remediated": False,
    }
    evidence_path = run_dir / f"phantom_timeline_remediation_{prefix}.json"

    pods = list_database_pod_names(cfg, context=context, namespace=namespace, cluster=cluster, audit=audit)
    if not pods:
        evidence["detail"] = f"no Running database pods found for {target_label}; cannot inspect"
        safe_json_dump(evidence, evidence_path)
        raise CutoverError(f"remediate-phantom-timelines-{prefix}: {evidence['detail']}")
    probe_pod = pods[0]
    evidence["pods"] = pods
    evidence["probe_pod"] = probe_pod

    controldata = run_command(
        exec_bash_argv(cfg, context, namespace, probe_pod, "pg_controldata $PGDATA"),
        audit=audit,
        purpose=f"pg_controldata on {target_label} probe pod {probe_pod}",
        target=f"{context}/{namespace}/{probe_pod}",
        timeout=args.timeout,
        show_notice=False,
    ).stdout
    tli_match = re.search(r"Latest checkpoint's TimeLineID:\s+(\d+)", controldata)
    minrec_match = re.search(r"Minimum recovery ending location:\s+([0-9A-Fa-f]+/[0-9A-Fa-f]+)", controldata)
    if not tli_match or not minrec_match:
        evidence["detail"] = (
            "could not parse pg_controldata output for TimeLineID / Minimum recovery ending location"
        )
        evidence["pg_controldata_excerpt"] = controldata[:2000]
        safe_json_dump(evidence, evidence_path)
        raise CutoverError(f"remediate-phantom-timelines-{prefix}: {evidence['detail']}")
    current_tli = int(tli_match.group(1))
    min_rec_lsn = minrec_match.group(1)
    min_rec_int = lsn_to_int(min_rec_lsn)
    evidence["current_timeline"] = current_tli
    evidence["min_recovery_ending_location"] = min_rec_lsn

    info = pgbackrest_info(
        cfg,
        context=context,
        namespace=namespace,
        pod=probe_pod,
        audit=audit,
        purpose=f"pgbackrest info on {target_label} probe pod {probe_pod}",
    )
    archives = (info[0] if info else {}).get("archive") or []
    if not archives:
        evidence["detail"] = f"pgbackrest info returned no archive entries for stanza={cfg.pgbackrest_stanza}"
        safe_json_dump(evidence, evidence_path)
        raise CutoverError(f"remediate-phantom-timelines-{prefix}: {evidence['detail']}")
    archive_id = archives[-1].get("id")
    evidence["archive_id"] = archive_id

    s3, bucket = build_pgbackrest_s3_client(cfg, context=context, namespace=namespace, pod=probe_pod, audit=audit)

    started_at = None
    started_at_file = run_dir / f"restore-{prefix}-as-standby_started_at.json"
    if started_at_file.exists():
        try:
            started_at = dt.datetime.fromisoformat(json.loads(started_at_file.read_text(encoding="utf-8"))["started_at"])
        except Exception:
            started_at = None
    evidence["restore_started_at"] = started_at.isoformat() if started_at else None
    evidence["last_modified_check_skipped"] = started_at is None

    from botocore.exceptions import ClientError

    phantom_files: list[dict[str, str]] = []
    n = current_tli
    for _ in range(3):
        candidate_n = n + 1
        candidate_name = f"{candidate_n:08X}.history"
        key = f"pgbackrest/repo1/archive/{cfg.pgbackrest_stanza}/{archive_id}/{candidate_name}"
        check: dict[str, Any] = {"file": candidate_name, "key": key, "exists": False}
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                evidence["checked_history_files"].append(check)
                break
            raise
        check["exists"] = True
        check["last_modified"] = head["LastModified"].isoformat()
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8", "replace")
        lines = [line for line in body.splitlines() if line.strip()]
        if not lines:
            check["detail"] = "empty .history file"
            evidence["checked_history_files"].append(check)
            break
        branch_tli_str, branch_lsn = lines[-1].split()[:2]
        branch_tli = int(branch_tli_str)
        check["branch_tli"] = branch_tli
        check["branch_lsn"] = branch_lsn
        is_phantom = (
            branch_tli == n
            and lsn_to_int(branch_lsn) <= min_rec_int
            and (started_at is None or head["LastModified"] > started_at)
        )
        check["is_phantom"] = is_phantom
        evidence["checked_history_files"].append(check)
        if not is_phantom:
            break
        phantom_files.append({"name": candidate_name, "key": key})
        n = candidate_n

    if not phantom_files:
        evidence["detail"] = f"no phantom timeline-history found for {target_label} (current TL={current_tli})"
        safe_json_dump(evidence, evidence_path)
        print(f"[remediate-phantom-timelines-{prefix}] {evidence['detail']}")
        return

    evidence["phantom_found"] = True
    print(
        f"[remediate-phantom-timelines-{prefix}] phantom timeline-history found: "
        + ", ".join(item["name"] for item in phantom_files)
    )

    for item in phantom_files:
        s3.delete_object(Bucket=bucket, Key=item["key"])
        try:
            s3.head_object(Bucket=bucket, Key=item["key"])
            raise CutoverError(
                f"remediate-phantom-timelines-{prefix}: {item['key']} still present in S3 after delete_object"
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                raise

    recycled_pods: list[str] = []
    for pod in pods:
        for item in phantom_files:
            name = item["name"]
            script = (
                f'removed=0; '
                f'for f in "$PGDATA/pg_wal/{name}" "$PGDATA/pg_wal/archive_status/{name}.done" '
                f'"$PGDATA/pg_wal/archive_status/{name}.ready"; do '
                f'if [ -e "$f" ]; then rm -fv "$f"; removed=1; fi; '
                f'done; echo REMOVED=$removed'
            )
            out = run_command(
                exec_bash_argv(cfg, context, namespace, pod, script),
                audit=audit,
                purpose=f"Remove cached {name} from {target_label} pod {pod} pg_wal",
                target=f"{context}/{namespace}/{pod}",
                timeout=args.timeout,
                allow_fail=True,
                show_notice=False,
            ).stdout
            if "REMOVED=1" in out and pod not in recycled_pods:
                recycled_pods.append(pod)

    evidence["recycled_pods"] = recycled_pods
    for pod in recycled_pods:
        run_command(
            oc_base(context) + ["delete", "pod", "-n", namespace, pod],
            audit=audit,
            purpose=f"Recycle {target_label} pod {pod} after removing phantom timeline-history cache",
            target=f"{context}/{namespace}/{pod}",
            timeout=args.timeout,
            show_notice=False,
        )

    converged = False
    last_summary: dict[str, Any] = {}
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(15)
        live_pods = list_database_pod_names(cfg, context=context, namespace=namespace, cluster=cluster, audit=audit)
        for pod in live_pods:
            patroni = run_command(
                patroni_argv(cfg, context, namespace, pod),
                audit=audit,
                purpose=f"Check patroni status on {target_label} pod {pod} after phantom-timeline remediation",
                target=f"{context}/{namespace}/{pod}",
                allow_fail=True,
                show_notice=False,
            )
            summary = patroni_summary(patroni.stdout)
            rows = summary.get("rows") or []
            if not rows:
                continue
            last_summary = summary
            on_target_tl = all(str(r.get("timeline")) == str(current_tli) for r in rows)
            streaming = all(r.get("state") == "streaming" for r in rows)
            no_failures = not any("failed" in str(r.get("state", "")).lower() for r in rows)
            if on_target_tl and streaming and no_failures:
                converged = True
            break
        if converged:
            break

    evidence["remediated"] = True
    evidence["converged"] = converged
    evidence["final_patroni_summary"] = last_summary
    safe_json_dump(evidence, evidence_path)
    if not converged:
        raise CutoverError(
            f"remediate-phantom-timelines-{prefix}: removed phantom "
            f"{', '.join(item['name'] for item in phantom_files)} and recycled "
            f"{recycled_pods}, but pods did not converge to streaming on TL={current_tli} "
            f"within 180s; see {evidence_path} for diagnostic state."
        )
    print(f"[remediate-phantom-timelines-{prefix}] remediation complete; all pods streaming on TL={current_tli}.")


def execute_manifest(args: argparse.Namespace, run_dir: pathlib.Path) -> int:
    if not args.step and not args.dry_run_execute:
        raise CutoverError("For safety, execute requires at least one --step.")
    manifest_path = pathlib.Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cfg = config_from_manifest(manifest)
    audit = Audit(run_dir)
    selected_ids = set(args.step or [])
    available_ids = {step.get("id") for step in manifest.get("steps", [])}
    unknown_ids = selected_ids - available_ids
    if unknown_ids:
        raise CutoverError(f"Unknown manifest step(s): {', '.join(sorted(unknown_ids))}")
    failures = 0
    for step in manifest.get("steps", []):
        if selected_ids and step["id"] not in selected_ids:
            continue
        if args.dry_run_execute:
            argv = step.get("argv") or []
            internal_action = step.get("internal_action")
            notice_argv = argv or ([f"<internal:{internal_action}>"] if internal_action else [f"<manual:{step['id']}>"])
            print_command_notice(
                purpose=step["purpose"],
                target=step["target"],
                argv=notice_argv,
                risk=step["risk"],
                expected=step.get("expected_output") or "Command-specific output.",
                requires_approval=bool(step.get("requires_approval")),
                safe_reason="Dry-run execute prints manifest content only; no command is executed and approval gate files are not consumed.",
            )
            if step.get("manual_gate"):
                print(f"DRY RUN MANUAL GATE: {step.get('command')}")
            else:
                print(f"DRY RUN EXECUTE: {step.get('command') or shell_join(argv)}")
            continue
        allowed, reason = command_is_allowed_for_execute(step, args, manifest)
        if not allowed:
            print(f"SKIP {step['id']}: {reason}")
            continue
        # Safety gates are operator-created marker files in the original run directory.
        # They make shutdown, promotion, and rebuild phases fail closed if run out of order.
        require_gate_files(run_dir, step.get("required_gate_files") or [])
        if step["id"] == "promote-dr" and manifest.get("mode") == "planned-switchover":
            require_precheck_checks_pass(
                manifest,
                run_dir,
                [
                    "dr_standby_enabled",
                    "dr_wal_receiver_active",
                    "dr_replay_delay_acceptable",
                    "prod_pgbackrest_ok",
                    "dr_pgbackrest_ok",
                    "no_patroni_pending_restart",
                ],
            )
        argv = step.get("argv")
        internal_action = step.get("internal_action")
        if not argv:
            if not internal_action:
                print(f"SKIP {step['id']}: no argv available for safe non-shell execution")
                continue
            argv = []
        print_command_notice(
            purpose=step["purpose"],
            target=step["target"],
            argv=argv or [f"<internal:{internal_action}>"],
            risk=step["risk"],
            expected=step.get("expected_output") or "Command-specific output.",
            requires_approval=bool(step.get("requires_approval")),
            safe_reason="Command is from reviewed manifest and is being logged to evidence.log.",
        )
        if args.dry_run_execute:
            print(f"DRY RUN EXECUTE: {step.get('command') or shell_join(argv)}")
            continue
        try:
            switch_project_for_execute_step(cfg, step, audit)
            if internal_action == "final_lag_check":
                execute_final_lag_check(args, cfg, manifest, run_dir, audit)
            elif internal_action == "reverse_final_lag_check":
                execute_reverse_final_lag_check(args, cfg, manifest, run_dir, audit)
            elif internal_action == "wait_prod_stopped":
                wait_prod_database_pods_stopped(
                    cfg,
                    run_dir=run_dir,
                    audit=audit,
                    timeout_seconds=args.prod_shutdown_timeout_seconds,
                )
            elif internal_action == "switch_wal_and_check_archive":
                execute_switch_wal_and_check_archive(args, cfg, manifest, run_dir, audit)
            elif internal_action == "remediate_phantom_timelines":
                execute_remediate_phantom_timelines(args, cfg, manifest, run_dir, audit)
            else:
                if step["id"].startswith("restore-") and step["id"].endswith("-as-standby"):
                    safe_json_dump({"started_at": now_utc()}, run_dir / f"{step['id']}_started_at.json")
                run_command(
                    argv,
                    audit=audit,
                    purpose=step["purpose"],
                    target=step["target"],
                    timeout=args.timeout,
                    show_notice=False,
                )
        except Exception as exc:
            failures += 1
            print(f"ERROR {step['id']}: {exc}", file=sys.stderr)
            if not args.continue_on_error:
                break
    return 1 if failures else 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-root", default="cutover_runs", help="Directory where timestamped run folders are created.")
    parser.add_argument("--prod-api", default=DEFAULTS["prod_api"])
    parser.add_argument("--dr-api", default=DEFAULTS["dr_api"])
    parser.add_argument("--oc-user", default=DEFAULTS["oc_user"])
    parser.add_argument(
        "--single-context-projects",
        action="store_true",
        default=DEFAULTS["single_context_projects"],
        help="Use the current oc context for both sites and switch projects/namespaces before site-specific operations.",
    )
    parser.add_argument("--prod-context", default=DEFAULTS["prod_context"])
    parser.add_argument("--dr-context", default=DEFAULTS["dr_context"])
    parser.add_argument("--prod-namespace", default=DEFAULTS["prod_namespace"])
    parser.add_argument("--dr-namespace", default=DEFAULTS["dr_namespace"])
    parser.add_argument("--prod-cluster", default=DEFAULTS["prod_cluster"])
    parser.add_argument("--dr-cluster", default=DEFAULTS["dr_cluster"])
    parser.add_argument("--patroni-prod-cluster", default=DEFAULTS["patroni_prod_cluster"])
    parser.add_argument("--patroni-dr-cluster", default=DEFAULTS["patroni_dr_cluster"])
    parser.add_argument("--container", default=DEFAULTS["container"])
    parser.add_argument("--pg-user", default=DEFAULTS["pg_user"])
    parser.add_argument("--pg-database", default=DEFAULTS["pg_database"])
    parser.add_argument("--pgbackrest-stanza", default=DEFAULTS["pgbackrest_stanza"])
    parser.add_argument("--pgbackrest-repo", default=DEFAULTS["pgbackrest_repo"])
    parser.add_argument("--prod-primary-lb", default=DEFAULTS["prod_primary_lb"])
    parser.add_argument("--dr-primary-lb", default=DEFAULTS["dr_primary_lb"], help="Required for rebuilding DC1 as standby of active DC2.")
    parser.add_argument("--pgbackrest-s3-bucket", default=DEFAULTS["pgbackrest_s3_bucket"])
    parser.add_argument("--pgbackrest-s3-endpoint", default=DEFAULTS["pgbackrest_s3_endpoint"])
    parser.add_argument("--pgbackrest-s3-region", default=DEFAULTS["pgbackrest_s3_region"])
    parser.add_argument("--prod-pgbackrest-secret", default=DEFAULTS["prod_pgbackrest_secret"])
    parser.add_argument("--dr-pgbackrest-secret", default=DEFAULTS["dr_pgbackrest_secret"])
    parser.add_argument("--postgres-port", default=DEFAULTS["postgres_port"])
    parser.add_argument("--known-prod-primary-pod", default=DEFAULTS["known_prod_primary_pod"])
    parser.add_argument("--known-prod-standby-pod", default=DEFAULTS["known_prod_standby_pod"])
    parser.add_argument("--known-dr-standby-leader-pod", default=DEFAULTS["known_dr_standby_leader_pod"])
    parser.add_argument("--known-dr-replica-pod", default=DEFAULTS["known_dr_replica_pod"])
    parser.add_argument("--patroni-config-path", default=DEFAULTS["patroni_config_path"], help="Optional patronictl config path inside database pods.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safe PROD/DR cutover precheck and command generation for Crunchy PGO.",
        formatter_class=RawDefaultsHelpFormatter,
        epilog=textwrap.dedent(
            """
            Safe planned switchover example:
              python3 prod_dr_cutover.py context-check
              python3 prod_dr_cutover.py dry-run --mode planned-switchover
              python3 prod_dr_cutover.py precheck --mode planned-switchover --max-lag-bytes 0 --max-replay-delay-seconds 30 --archiver-sample-seconds 60 --postgres-port 5555
              python3 prod_dr_cutover.py generate --mode planned-switchover --run-dir <precheck_run_dir>
              touch <run_dir>/application_writes_frozen.approved
              python3 prod_dr_cutover.py execute --manifest <run_dir>/command_manifest.json --step final-lag-check --max-lag-bytes 0
              python3 prod_dr_cutover.py execute --manifest <run_dir>/command_manifest.json --step shutdown-prod --execute-state-changing --confirm-prod-impact --approval-token <token>
              python3 prod_dr_cutover.py execute --manifest <run_dir>/command_manifest.json --step wait-prod-stopped
              python3 prod_dr_cutover.py execute --manifest <run_dir>/command_manifest.json --step promote-dr --execute-state-changing --confirm-prod-impact --approval-token <token>
              python3 prod_dr_cutover.py execute --manifest <run_dir>/command_manifest.json --step verify-dr-writable-primary
            """
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ctx = sub.add_parser("context-check", help="Verify bastion PROD/DR contexts without pod exec.")
    add_common_args(ctx)
    ctx.add_argument("--mode", choices=sorted(MODES), default="planned-switchover")
    ctx.add_argument(
        "--allow-pods-not-ready",
        action="store_true",
        help="Validation-only bypass: downgrade visible database pod readiness failures to warnings.",
    )

    dry = sub.add_parser("dry-run", help="Generate files only; do not call oc.")
    add_common_args(dry)
    dry.add_argument("--mode", choices=sorted(MODES), default="planned-switchover")
    dry.add_argument("--force", action="store_true", help="Allow command generation without a passing precheck.")
    dry.add_argument("--include-destructive-switchback", action="store_true")
    dry.add_argument("--include-destructive-rebuild", action="store_true", help="Include gated PVC delete and standby restore steps for rebuild modes.")

    pre = sub.add_parser("precheck", help="Run read-only live prechecks and write precheck.json.")
    add_common_args(pre)
    pre.add_argument("--mode", choices=sorted(MODES), default="planned-switchover")
    pre.add_argument("--max-lag-bytes", type=int, default=0, help="Allowed planned switchover lag. Keep 0 for no data loss.")
    pre.add_argument("--disaster-max-lag-bytes", type=int, default=2**63 - 1, help="Recorded disaster lag threshold.")
    pre.add_argument("--max-replay-delay-seconds", type=int, default=30, help="Maximum DR replay delay for planned switchover.")
    pre.add_argument("--max-archive-age-seconds", type=int, default=600, help="Maximum acceptable prod archive age when app sessions are active.")
    pre.add_argument("--archiver-sample-seconds", type=int, default=60, help="Second archiver sample delay. Use 0 only for quick diagnostics.")
    pre.add_argument("--ignore-db-users", default="postgres", help="Comma-separated database users excluded from app session gating.")
    pre.add_argument("--allow-active-sessions", action="store_true", help="Allow planned switchover precheck to pass with active app sessions.")
    pre.add_argument(
        "--allow-pods-not-ready",
        action="store_true",
        help="Validation-only bypass: downgrade visible database pod readiness failures to warnings.",
    )
    pre.add_argument(
        "--allow-archive-only-catchup",
        action="store_true",
        help=(
            "Allow planned switchover precheck to pass WAL receiver, replay-age, and archive-age gates "
            "when pgBackRest repo checks pass and computed WAL lag is zero."
        ),
    )

    gen = sub.add_parser("generate", help="Generate command manifest from a precheck file or force/offline defaults.")
    add_common_args(gen)
    gen.add_argument("--mode", choices=sorted(MODES), default="planned-switchover")
    gen.add_argument("--run-dir", help="Existing run directory containing precheck.json, or new target with --force.")
    gen.add_argument("--precheck-json", help="Explicit precheck JSON path.")
    gen.add_argument("--force", action="store_true", help="Generate even without passing precheck.")
    gen.add_argument("--allow-stale-pod-fallback", action="store_true", help="Allow offline generation to use configured known pod names.")
    gen.add_argument("--include-destructive-switchback", action="store_true")
    gen.add_argument("--include-destructive-rebuild", action="store_true", help="Include gated PVC delete and standby restore steps for rebuild modes.")

    exe = sub.add_parser("execute", help="Execute selected manifest steps with strict gates.")
    exe.add_argument("--manifest", required=True)
    exe.add_argument("--run-root", default="cutover_runs")
    exe.add_argument("--step", action="append", help="Step id to execute. Repeat for multiple. Required for safety.")
    exe.add_argument("--approval-token", help="Must match manifest approval_token for state-changing commands.")
    exe.add_argument("--execute-state-changing", action="store_true")
    exe.add_argument("--confirm-prod-impact", action="store_true")
    exe.add_argument("--dry-run-execute", action="store_true", help="Print commands that would execute; run nothing.")
    exe.add_argument("--continue-on-error", action="store_true")
    exe.add_argument("--timeout", type=int, default=180)
    exe.add_argument("--max-lag-bytes", type=int, default=0, help="Allowed final lag for final-lag-check.")
    exe.add_argument("--max-archive-age-seconds", type=int, default=600, help="Allowed archive age for switch-wal-and-check-archive.")
    exe.add_argument("--prod-shutdown-timeout-seconds", type=int, default=600, help="Timeout for wait-prod-stopped.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "execute":
            run_dir = pathlib.Path(args.manifest).expanduser().resolve().parent
            return execute_manifest(args, run_dir)

        cfg = load_config(args)
        if args.command == "context-check":
            run_dir = ensure_run_dir(args.run_root, "context_check")
            check = collect_context_check(args, cfg, run_dir)
            print(f"Context check status: {check['status']}")
            print(f"Context check file: {run_dir / 'context_check.json'}")
            if check["warnings"]:
                print("Warnings:")
                for warning in check["warnings"]:
                    print(f"  - {warning}")
            if check["status"] != "PASS":
                print("Failures:")
                for failure in check["errors"]:
                    print(f"  - {failure}")
                return 2
            return 0

        if args.command == "dry-run":
            run_dir = ensure_run_dir(args.run_root, f"{args.mode}_dry_run")
            precheck = {
                "script_version": SCRIPT_VERSION,
                "run_id": str(uuid.uuid4()),
                "mode": args.mode,
                "created_at": now_utc(),
                "run_dir": str(run_dir),
                "status": "DRY_RUN_ONLY",
                "errors": ["No live precheck was run."],
                "warnings": ["This dry run generated files only and did not call oc."],
                "config": asdict(cfg),
                "data": {},
            }
            safe_json_dump(precheck, run_dir / "precheck.json")
            args.force = True
            manifest = generate_command_files(args, cfg, run_dir, precheck)
            print(f"Dry run generated: {run_dir}")
            print(f"Manifest: {run_dir / 'command_manifest.json'}")
            print(f"Review commands: {run_dir / 'commands_review.sh'}")
            print(f"Approval token for later gated execute: {manifest['approval_token']}")
            return 0

        if args.command == "precheck":
            run_dir = ensure_run_dir(args.run_root, f"{args.mode}_precheck")
            precheck = collect_precheck(args, cfg, run_dir)
            print(f"Precheck status: {precheck['status']}")
            print(f"Precheck file: {run_dir / 'precheck.json'}")
            if precheck["status"] != "PASS":
                print("Failures:")
                for failure in precheck["errors"]:
                    print(f"  - {failure}")
                return 2
            return 0

        if args.command == "generate":
            if args.run_dir:
                run_dir = pathlib.Path(args.run_dir)
                run_dir.mkdir(parents=True, exist_ok=True)
            else:
                run_dir = ensure_run_dir(args.run_root, f"{args.mode}_generate")
            precheck = load_precheck(args.precheck_json, run_dir)
            if precheck is None and not args.force:
                raise CutoverError("No precheck.json found. Run precheck first or use --force for offline command generation.")
            manifest = generate_command_files(args, cfg, run_dir, precheck)
            print(f"Generated command manifest: {run_dir / 'command_manifest.json'}")
            print(f"Generated review file: {run_dir / 'commands_review.sh'}")
            print(f"Approval token for later gated execute: {manifest['approval_token']}")
            return 0

        parser.error(f"unknown command {args.command}")
        return 2
    except CutoverError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
