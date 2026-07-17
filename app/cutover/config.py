"""Region configuration for the cutover module.

console_cutover_configs holds per-region RuntimeConfig values (contexts,
namespaces, LB IPs, S3, secret NAMES, known pods) used to render the region
wrapper. No secret material is stored here: the kubeconfig stays a mounted
Secret, pgbackrest secrets are referenced by name only.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

CUTOVER_RUN_ROOT = os.environ.get("CUTOVER_RUN_ROOT", "/var/lib/console/cutover_runs")
CUTOVER_CONFIG_JSON = os.environ.get("CUTOVER_CONFIG_JSON")
CUTOVER_CONFIG_PATH = os.environ.get("CUTOVER_CONFIG_PATH")
DEFAULT_KUBECONFIG_PATH = os.environ.get("CUTOVER_KUBECONFIG", "/etc/cutover/kubeconfig")

# Job kind -> (orchestrator --phases value, approver role, destructive)
CUTOVER_JOB_KINDS: dict[str, dict[str, Any]] = {
    "cutover_switchover": {
        "phases": "01_planned_switchover",
        "role": "dba",
        "destructive": False,
        "label": "Planned switchover PROD(DC1) → DR(DC2)",
    },
    "cutover_rebuild_dc1": {
        "phases": "02_rebuild_dc1",
        "role": "admin",
        "destructive": True,
        "label": "Rebuild/reinit DC1 as standby of active DC2 (PVC delete + restore)",
    },
    "cutover_switchback": {
        "phases": "03_switchback",
        "role": "dba",
        "destructive": False,
        "label": "Switchback DR(DC2) → PROD(DC1)",
    },
    "cutover_rebuild_dc2": {
        "phases": "04_rebuild_dc2",
        "role": "admin",
        "destructive": True,
        "label": "Rebuild/reinit DC2 as standby of active DC1 (PVC delete + restore)",
    },
    "cutover_full_drill": {
        "phases": "all",
        "role": "admin",
        "destructive": True,
        "label": "Full 4-phase DR drill (switchover → rebuild DC1 → switchback → rebuild DC2)",
    },
}

CUTOVER_TIERS = ("preview", "rehearsal", "armed")

# Engine flag <- config key map used by the wrapper renderer. Keys absent from
# a region config fall back to the engine's built-in defaults, exactly like the
# UK wrapper relies on defaults for container/pg_user/stanza.
CONFIG_FLAG_MAP: dict[str, str] = {
    "prod_context": "--prod-context",
    "dr_context": "--dr-context",
    "prod_namespace": "--prod-namespace",
    "dr_namespace": "--dr-namespace",
    "prod_cluster": "--prod-cluster",
    "dr_cluster": "--dr-cluster",
    "patroni_prod_cluster": "--patroni-prod-cluster",
    "patroni_dr_cluster": "--patroni-dr-cluster",
    "container": "--container",
    "pg_user": "--pg-user",
    "pg_database": "--pg-database",
    "pgbackrest_stanza": "--pgbackrest-stanza",
    "pgbackrest_repo": "--pgbackrest-repo",
    "prod_primary_lb": "--prod-primary-lb",
    "dr_primary_lb": "--dr-primary-lb",
    "pgbackrest_s3_bucket": "--pgbackrest-s3-bucket",
    "pgbackrest_s3_endpoint": "--pgbackrest-s3-endpoint",
    "pgbackrest_s3_region": "--pgbackrest-s3-region",
    "prod_pgbackrest_secret": "--prod-pgbackrest-secret",
    "dr_pgbackrest_secret": "--dr-pgbackrest-secret",
    "postgres_port": "--postgres-port",
    "known_prod_primary_pod": "--known-prod-primary-pod",
    "known_prod_standby_pod": "--known-prod-standby-pod",
    "known_dr_standby_leader_pod": "--known-dr-standby-leader-pod",
    "known_dr_replica_pod": "--known-dr-replica-pod",
    "patroni_config_path": "--patroni-config-path",
}

REQUIRED_CONFIG_KEYS = (
    "prod_context",
    "dr_context",
    "prod_namespace",
    "dr_namespace",
    "prod_cluster",
    "dr_cluster",
    "prod_primary_lb",
    "dr_primary_lb",
)

HOOK_KEYS = ("freeze_hook", "unfreeze_hook", "route_hook")


def _console():
    import app.main as console

    return console


def ensure_cutover_schema() -> None:
    console = _console()
    s = console.schema_name()
    sql = f"""
    create table if not exists {s}.console_cutover_configs (
      id text primary key,
      tenant_id uuid not null references {s}.console_tenants(id),
      prod_cluster_id text references {s}.console_clusters(id),
      dr_cluster_id text references {s}.console_clusters(id),
      config jsonb not null,
      hooks jsonb not null default '{{}}'::jsonb,
      kubeconfig_path text not null default '{DEFAULT_KUBECONFIG_PATH}',
      enabled boolean not null default false,
      updated_by text,
      updated_at timestamptz not null default now()
    );

    create table if not exists {s}.console_cutover_runs (
      job_id uuid primary key references {s}.console_jobs(id) on delete cascade,
      config_id text not null references {s}.console_cutover_configs(id),
      mode text not null,
      tier text not null check (tier in ('preview','rehearsal','armed')),
      run_root text not null,
      resumes_job uuid references {s}.console_jobs(id),
      progress jsonb not null default '{{}}'::jsonb,
      pid integer,
      started_at timestamptz,
      finished_at timestamptz
    );

    create unique index if not exists console_cutover_one_active_idx
      on {s}.console_cutover_runs (config_id) where finished_at is null;
    """
    db = console.require_pool()
    with db.connection() as conn:
        conn.execute(sql)
        conn.commit()


def _load_seed_payload() -> list[dict[str, Any]]:
    raw: str | None = None
    if CUTOVER_CONFIG_JSON:
        raw = CUTOVER_CONFIG_JSON
    elif CUTOVER_CONFIG_PATH and os.path.isfile(CUTOVER_CONFIG_PATH):
        with open(CUTOVER_CONFIG_PATH, encoding="utf-8") as fh:
            raw = fh.read()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict) and "configs" in data:
        data = data["configs"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("cutover config seed must be an object or list of objects")
    return data


def seed_cutover_configs() -> int:
    """Upsert region configs from CUTOVER_CONFIG_JSON / CUTOVER_CONFIG_PATH."""
    entries = _load_seed_payload()
    if not entries:
        return 0
    console = _console()
    s = console.schema_name()
    db = console.require_pool()
    count = 0
    with db.connection() as conn:
        for entry in entries:
            config_id = str(entry.get("id") or "").strip()
            config = entry.get("config") or {}
            if not config_id or not isinstance(config, dict):
                continue
            conn.execute(
                f"""
                insert into {s}.console_cutover_configs
                  (id, tenant_id, prod_cluster_id, dr_cluster_id, config, hooks,
                   kubeconfig_path, enabled, updated_by, updated_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                on conflict (id) do update set
                  prod_cluster_id = excluded.prod_cluster_id,
                  dr_cluster_id = excluded.dr_cluster_id,
                  config = excluded.config,
                  hooks = excluded.hooks,
                  kubeconfig_path = excluded.kubeconfig_path,
                  enabled = excluded.enabled,
                  updated_by = excluded.updated_by,
                  updated_at = now()
                """,
                (
                    config_id,
                    console.DEFAULT_TENANT_ID,
                    entry.get("prod_cluster_id"),
                    entry.get("dr_cluster_id"),
                    Jsonb(config),
                    Jsonb(entry.get("hooks") or {}),
                    entry.get("kubeconfig_path") or DEFAULT_KUBECONFIG_PATH,
                    bool(entry.get("enabled", False)),
                    "seed:env",
                ),
            )
            count += 1
        conn.commit()
    return count


def get_cutover_config(config_id: str) -> dict[str, Any] | None:
    console = _console()
    s = console.schema_name()
    db = console.require_pool()
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"select * from {s}.console_cutover_configs where id = %s",
            (config_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_cutover_configs() -> list[dict[str, Any]]:
    console = _console()
    s = console.schema_name()
    db = console.require_pool()
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"select * from {s}.console_cutover_configs order by id")
        return [dict(row) for row in cur.fetchall()]


def redact_config_row(row: dict[str, Any], *, full_hooks: bool) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "prod_cluster_id": row.get("prod_cluster_id"),
        "dr_cluster_id": row.get("dr_cluster_id"),
        "config": row.get("config") or {},
        "kubeconfig_path": row.get("kubeconfig_path"),
        "enabled": bool(row.get("enabled")),
        "updated_by": row.get("updated_by"),
        "updated_at": row.get("updated_at"),
        "missing_keys": [
            key for key in REQUIRED_CONFIG_KEYS if not str((row.get("config") or {}).get(key) or "").strip()
        ],
    }
    hooks = row.get("hooks") or {}
    if full_hooks:
        out["hooks"] = hooks
    else:
        # Hook commands are admin-managed shell strings; non-admins only see
        # which hooks are configured.
        out["hooks"] = {key: bool(str(hooks.get(key) or "").strip()) for key in HOOK_KEYS}
    return out


def validate_config_payload(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Validate a PUT payload; returns (config, hooks, problems)."""
    problems: list[str] = []
    config = body.get("config")
    if not isinstance(config, dict):
        problems.append("config must be an object")
        config = {}
    unknown = sorted(set(config) - set(CONFIG_FLAG_MAP))
    if unknown:
        problems.append(f"unknown config keys: {', '.join(unknown)}")
    for key in REQUIRED_CONFIG_KEYS:
        if not str(config.get(key) or "").strip():
            problems.append(f"missing required config key: {key}")
    hooks = body.get("hooks") or {}
    if not isinstance(hooks, dict):
        problems.append("hooks must be an object")
        hooks = {}
    bad_hooks = sorted(set(hooks) - set(HOOK_KEYS))
    if bad_hooks:
        problems.append(f"unknown hook keys: {', '.join(bad_hooks)}")
    return config, hooks, problems


def upsert_cutover_config(
    config_id: str,
    *,
    config: dict[str, Any],
    hooks: dict[str, Any],
    kubeconfig_path: str,
    enabled: bool,
    prod_cluster_id: str | None,
    dr_cluster_id: str | None,
    updated_by: str,
) -> dict[str, Any]:
    console = _console()
    s = console.schema_name()
    db = console.require_pool()
    with db.connection() as conn:
        conn.execute(
            f"""
            insert into {s}.console_cutover_configs
              (id, tenant_id, prod_cluster_id, dr_cluster_id, config, hooks,
               kubeconfig_path, enabled, updated_by, updated_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            on conflict (id) do update set
              prod_cluster_id = excluded.prod_cluster_id,
              dr_cluster_id = excluded.dr_cluster_id,
              config = excluded.config,
              hooks = excluded.hooks,
              kubeconfig_path = excluded.kubeconfig_path,
              enabled = excluded.enabled,
              updated_by = excluded.updated_by,
              updated_at = now()
            """,
            (
                config_id,
                console.DEFAULT_TENANT_ID,
                prod_cluster_id,
                dr_cluster_id,
                Jsonb(config),
                Jsonb(hooks),
                kubeconfig_path,
                enabled,
                updated_by,
            ),
        )
        conn.commit()
    refreshed = get_cutover_config(config_id)
    assert refreshed is not None
    return refreshed


def manifest_matches_config(manifest: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str]:
    """Console-side equivalent of the UK wrapper's execute manifest guard."""
    manifest_cfg = manifest.get("config") or {}
    expected = str(config.get("prod_cluster") or "").strip()
    actual = str(manifest_cfg.get("prod_cluster") or "").strip()
    if not expected:
        return False, "region config has no prod_cluster"
    if actual != expected:
        return False, f"manifest prod_cluster={actual!r} does not match configured {expected!r}"
    return True, "ok"


def active_run_for_config(config_id: str) -> dict[str, Any] | None:
    console = _console()
    s = console.schema_name()
    db = console.require_pool()
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            select r.*, j.state as job_state, j.kind as job_kind
            from {s}.console_cutover_runs r
            join {s}.console_jobs j on j.id = r.job_id
            where r.config_id = %s and r.finished_at is null
            """,
            (config_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_cutover_runs(config_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    console = _console()
    s = console.schema_name()
    db = console.require_pool()
    clauses = ["1 = 1"]
    params: list[Any] = []
    if config_id:
        clauses.append("r.config_id = %s")
        params.append(config_id)
    params.append(limit)
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            select r.*, j.kind as job_kind, j.state as job_state, j.reason,
                   j.submitted_by_sub, j.approved_by_sub, j.submitted_at, j.completed_at
            from {s}.console_cutover_runs r
            join {s}.console_jobs j on j.id = r.job_id
            where {" and ".join(clauses)}
            order by j.submitted_at desc
            limit %s
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]


def get_cutover_run(job_id: str | uuid.UUID) -> dict[str, Any] | None:
    console = _console()
    s = console.schema_name()
    db = console.require_pool()
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"select * from {s}.console_cutover_runs where job_id = %s",
            (str(job_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None
