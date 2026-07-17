"""HTTP API for the cutover module: /api/v1/cutover/*.

Imports app.main lazily (module attribute access at call time) because main.py
includes this router near its bottom; everything the handlers need is defined
by then.
"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app import cutover as vendor_pkg
from app.cutover import config as cutover_config
from app.cutover import runner as cutover_runner

router = APIRouter(prefix="/api/v1/cutover", tags=["Cutover"])

# Submitter-tunable orchestrator options; everything else (hook commands,
# contexts, LBs) comes from the admin-managed region config only.
ALLOWED_OPTION_KEYS = {
    "max_lag_bytes",
    "settle_timeout",
    "allow_archive_only",
    "freeze_hook_enabled",
    "route_hook_enabled",
    "timeout_seconds",
}


def _console():
    import app.main as console

    return console


def _require_user(request: Request, required_role: str) -> dict[str, Any]:
    console = _console()
    user = console.get_current_user(request)
    console.require_role_for_user(user, required_role)
    return user


def _vendor_status() -> dict[str, Any]:
    try:
        checksums = vendor_pkg.verify_vendor()
        return {"ok": True, "files": checksums}
    except vendor_pkg.VendorIntegrityError as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/config")
def cutover_get_config(request: Request) -> dict[str, Any]:
    user = _require_user(request, "viewer")
    full_hooks = _console().role_at_least(user["role"], "admin")
    rows = cutover_config.list_cutover_configs()
    return {
        "configs": [cutover_config.redact_config_row(row, full_hooks=full_hooks) for row in rows],
        "vendor": _vendor_status(),
        "oc_available": shutil.which("oc") is not None,
        "run_root": cutover_config.CUTOVER_RUN_ROOT,
        "read_only": _console().READ_ONLY_MODE,
    }


@router.put("/config/{config_id}")
async def cutover_put_config(config_id: str, request: Request) -> dict[str, Any]:
    console = _console()
    if console.READ_ONLY_MODE:
        raise HTTPException(status_code=403, detail="console is in read-only mode")
    user = _require_user(request, "admin")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="request body must be JSON")
    config, hooks, problems = cutover_config.validate_config_payload(body)
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))
    before = cutover_config.get_cutover_config(config_id)
    row = cutover_config.upsert_cutover_config(
        config_id,
        config=config,
        hooks=hooks,
        kubeconfig_path=str(body.get("kubeconfig_path") or cutover_config.DEFAULT_KUBECONFIG_PATH),
        enabled=bool(body.get("enabled", False)),
        prod_cluster_id=body.get("prod_cluster_id"),
        dr_cluster_id=body.get("dr_cluster_id"),
        updated_by=user["oidc_sub"],
    )
    console.write_audit(
        "cutover.config_updated",
        "cutover_config",
        config_id,
        request=request,
        user=user,
        payload={
            "enabled": row["enabled"],
            "had_previous": before is not None,
            "config_keys": sorted(config.keys()),
            "hooks_configured": sorted(k for k in hooks if str(hooks.get(k) or "").strip()),
        },
        source="cutover",
    )
    return {"config": cutover_config.redact_config_row(row, full_hooks=True)}


@router.get("/modes")
def cutover_modes(request: Request) -> dict[str, Any]:
    _require_user(request, "viewer")
    return {
        "kinds": [
            {
                "kind": kind,
                "phases": meta["phases"],
                "approver_role": meta["role"],
                "destructive": meta["destructive"],
                "label": meta["label"],
            }
            for kind, meta in cutover_config.CUTOVER_JOB_KINDS.items()
        ],
        "tiers": [
            {"tier": "preview", "description": "Engine dry-run only: generates the command manifest without calling oc."},
            {"tier": "rehearsal", "description": "Orchestrator without --arm: read-only walk against live clusters; nothing state-changing runs."},
            {"tier": "armed", "description": "Orchestrator --arm: executes state-changing steps, protected by live gate-proofs."},
        ],
        "engine": {
            "vendored_from": "switchover_switchback_UK (live-tested 2026-06-11)",
            "files": sorted(vendor_pkg.expected_checksums()),
        },
    }


def _run_view(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["job_id"] = str(out["job_id"])
    if out.get("resumes_job"):
        out["resumes_job"] = str(out["resumes_job"])
    return out


@router.get("/runs")
def cutover_list_runs(request: Request, config_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    _require_user(request, "viewer")
    rows = cutover_config.list_cutover_runs(config_id=config_id, limit=max(1, min(limit, 200)))
    return {"runs": [_run_view(row) for row in rows], "count": len(rows)}


@router.get("/runs/{job_id}")
def cutover_get_run(job_id: str, request: Request) -> dict[str, Any]:
    _require_user(request, "viewer")
    console = _console()
    job = console.get_job(job_id)
    run = cutover_config.get_cutover_run(job["id"])
    if run is None:
        raise HTTPException(status_code=404, detail=f"no cutover run for job {job_id}")
    return {"run": _run_view(run), "job": job}


@router.post("/runs/{job_id}/cancel")
async def cutover_cancel_run(job_id: str, request: Request) -> dict[str, Any]:
    console = _console()
    user = _require_user(request, "operator")
    job = console.get_job(job_id)
    if not job["kind"].startswith(console.CUTOVER_JOB_KIND_PREFIX):
        raise HTTPException(status_code=400, detail=f"job {job_id} is not a cutover job")
    if job["state"] != "running":
        raise HTTPException(
            status_code=409,
            detail=f"job is {job['state']}, not running; pending jobs cancel via DELETE /api/v1/jobs/{job_id}",
        )
    cancelled = await cutover_runner.request_cancel(str(job["id"]))
    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail="no active orchestrator process for this job in this console instance",
        )
    console.write_audit(
        "job.cancel_requested",
        "job",
        str(job["id"]),
        request=request,
        user=user,
        request_id=job["request_id"],
        cluster_id=job["cluster_id"],
        payload={"kind": job["kind"]},
        source="cutover",
    )
    return {"job": console.get_job(job_id)}


@router.post("/{config_id}/runs")
async def cutover_submit_run(config_id: str, request: Request) -> dict[str, Any]:
    """Submit a cutover job.

    Every tier starts with a submit-time engine dry-run (manifest generation,
    no oc). preview stops there; rehearsal/armed park in pending_approval with
    the manifest summary attached for the approver, and only run the vendored
    orchestrator after 4-eyes approval (the approve endpoint dispatches).
    """
    console = _console()
    if console.READ_ONLY_MODE:
        raise HTTPException(status_code=403, detail="console is in read-only mode")
    user = _require_user(request, "operator")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="request body must be JSON")

    kind = str(body.get("kind") or "")
    if kind not in cutover_config.CUTOVER_JOB_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown kind {kind!r}; expected one of {', '.join(sorted(cutover_config.CUTOVER_JOB_KINDS))}",
        )
    tier = str(body.get("tier") or "preview")
    if tier not in cutover_config.CUTOVER_TIERS:
        raise HTTPException(status_code=400, detail=f"unknown tier {tier!r}; expected one of {', '.join(cutover_config.CUTOVER_TIERS)}")
    reason = str(body.get("reason") or "").strip()
    if len(reason) < 8:
        raise HTTPException(status_code=400, detail="reason must be at least 8 characters")
    options = body.get("options") or {}
    if not isinstance(options, dict):
        raise HTTPException(status_code=400, detail="options must be an object")
    bad_options = sorted(set(options) - ALLOWED_OPTION_KEYS)
    if bad_options:
        raise HTTPException(status_code=400, detail=f"unknown option keys: {', '.join(bad_options)}")

    row = cutover_config.get_cutover_config(config_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown cutover config: {config_id}")
    if not row.get("enabled"):
        raise HTTPException(status_code=409, detail=f"cutover config {config_id} is disabled")
    config = row.get("config") or {}
    missing = [key for key in cutover_config.REQUIRED_CONFIG_KEYS if not str(config.get(key) or "").strip()]
    if missing:
        raise HTTPException(status_code=409, detail=f"config {config_id} is missing required keys: {', '.join(missing)}")
    vendor = _vendor_status()
    if not vendor["ok"]:
        raise HTTPException(status_code=503, detail=f"cutover disabled: {vendor['error']}")
    active = cutover_config.active_run_for_config(config_id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"config {config_id} already has an active run: job {active['job_id']} ({active['job_state']})",
        )

    kind_meta = cutover_config.CUTOVER_JOB_KINDS[kind]
    approver_role = kind_meta["role"]
    job_id = uuid.uuid4()
    request_id = console.request_id_from_request(request)
    payload = {
        "tier": tier,
        "config_id": config_id,
        "options": options,
        "destructive": kind_meta["destructive"],
        "required_approver_role": None if tier == "preview" else approver_role,
    }

    ddl_schema = console.schema_name()
    db = console.require_pool()
    with db.connection() as conn:
        conn.execute(
            f"""
            insert into {ddl_schema}.console_jobs
              (id, request_id, cluster_id, tenant_id, kind, state, submitted_by,
               submitted_by_sub, submitted_at, reason, target, payload)
            values (%s, %s, %s, %s, %s, 'pending', %s, %s, now(), %s, %s, %s)
            """,
            (
                job_id,
                request_id,
                row.get("prod_cluster_id"),
                user["tenant_id"],
                kind,
                user["id"],
                user["oidc_sub"],
                reason,
                config_id,
                Jsonb(payload),
            ),
        )
        conn.commit()

    workspace = await asyncio.to_thread(cutover_runner.build_workspace, job_id, config_id, config)
    run_root = str(workspace / ("preview" if tier == "preview" else "runs"))
    try:
        cutover_runner.insert_run_row(job_id, config_id, mode=kind_meta["phases"], tier=tier, run_root=run_root)
    except UniqueViolation:
        # Lost a submit race after the active_run check above.
        cutover_runner.complete_job(job_id, "cancelled", {"error": f"config {config_id} already has an active run"})
        raise HTTPException(status_code=409, detail=f"config {config_id} already has an active run")

    console.append_job_log(job_id, "event", f"submitted {kind} tier={tier} region={config_id} by {user['oidc_sub']}")
    console.write_audit(
        "job.submitted",
        "job",
        str(job_id),
        request=request,
        user=user,
        request_id=request_id,
        cluster_id=row.get("prod_cluster_id"),
        payload={"kind": kind, "tier": tier, "config_id": config_id, "reason": reason, "options": options},
        source="cutover",
    )

    preview = await cutover_runner.run_preview(job_id, kind=kind, workspace=workspace)

    if preview.get("rc") != 0:
        result = {"tier": tier, "preview": preview, "error": "submit-time engine dry-run failed"}
        cutover_runner.complete_job(job_id, "failed", result)
        console.append_job_log(job_id, "event", "submit-time dry-run failed; job not queued for approval")
        console.write_audit(
            "job.failed", "job", str(job_id),
            request=request, user=user, request_id=request_id,
            cluster_id=row.get("prod_cluster_id"),
            payload={"kind": kind, "tier": tier, "preview_rc": preview.get("rc")},
            source="cutover",
        )
        state = "failed"
    elif tier == "preview":
        cutover_runner.complete_job(job_id, "succeeded", {"tier": tier, "preview": preview})
        console.write_audit(
            "job.succeeded", "job", str(job_id),
            request=request, user=user, request_id=request_id,
            cluster_id=row.get("prod_cluster_id"),
            payload={"kind": kind, "tier": tier},
            source="cutover",
        )
        state = "succeeded"
    else:
        cutover_runner.mark_pending_approval(job_id, {**payload, "preview": preview})
        console.append_job_log(job_id, "event", f"waiting for approval (role >= {approver_role}, 4-eyes)")
        state = "pending_approval"

    return {
        "request_id": request_id,
        "job_id": job_id,
        "state": state,
        "tier": tier,
        "preview": preview,
        "location": f"/api/v1/jobs/{job_id}",
    }
