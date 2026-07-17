#!/usr/bin/env python3
"""
prod_dr_cutover_uk_orchestrate.py — hands-off DR-DRILL driver for the UK
prod/dr Patroni cutover lifecycle.

WHAT THIS IS
------------
A 4-phase state machine that runs the existing, audited cutover engine for an
*unattended DR rehearsal*:

    1. planned-switchover   DC1 (prod) -> DC2 (dr)
    2. rebuild-dc1-standby  rebuild old DC1 from active DC2 / S3
    3. switchback           DC2 (dr) -> rebuilt DC1
    4. rebuild-dc2-standby  rebuild old DC2 from active DC1 / S3

WHAT IT DOES *NOT* DO (by design)
---------------------------------
* It never forks or weakens the engine's safety logic. All mechanics
  (context-check / precheck / generate / execute) go through the UK wrapper
  `prod_dr_cutover_uk.sh`, so the "is this a UK manifest" guard, the approval
  token, the `--execute-state-changing` / `--confirm-prod-impact` flags and the
  in-engine gate checks all still apply.
* It never writes a `.approved` gate file unconditionally. Each manual human
  gate is replaced by a *machine proof* — a read-only assertion against the live
  cluster. The gate file is written only when the proof passes, and the proof
  evidence is embedded in the gate file.
* `disaster-failover` is intentionally NOT part of the chain. That path stays
  human-only.

SAFETY MODEL
------------
* Dry-run is the DEFAULT. State-changing execution requires `--arm`.
* Every phase aborts the whole chain on the first non-PASS precheck, failed
  proof, or skipped/failed execute step.
* `execute` is run one `--step` at a time and its output is scanned for
  `SKIP <id>` / `ERROR <id>` — the engine *silently skips* a state-changing step
  whose gate/token is missing (exit 0), so exit code alone is not trusted.
* Resumable: phase/step progress is persisted to `orchestrator_state.json`, so a
  crash resumes at the last incomplete step instead of, e.g., re-shutting-down a
  primary.
* Inter-phase settle: after each phase the driver polls (does not sleep) until
  the new topology is healthy before starting the next phase.

CONTEXT REQUIREMENT
-------------------
A real cross-site switchover needs ONE session that can see BOTH
prod-pgcluster-uk and dr-pgcluster-uk. Per the handoff that means named-context
mode: run with `UK_USE_CURRENT_CONTEXT=0` and both APIs logged in. The driver
passes the environment straight through to the wrapper and warns if current
single-context mode is selected.

USAGE
-----
    # 1. Full dry-run of the whole chain (no gate files, no state change):
    UK_USE_CURRENT_CONTEXT=0 ./prod_dr_cutover_uk_orchestrate.py --dry-run

    # 2. Armed drill, fully unattended, app-freeze driven by a hook command:
    UK_USE_CURRENT_CONTEXT=0 ./prod_dr_cutover_uk_orchestrate.py --arm \
        --freeze-hook './freeze_uk_apps.sh' --unfreeze-hook './unfreeze_uk_apps.sh'

    # 3. Resume an interrupted run (same run-root):
    UK_USE_CURRENT_CONTEXT=0 ./prod_dr_cutover_uk_orchestrate.py --arm \
        --run-root cutover_runs_uk_orchestrated/20260607_2200_drill
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any, Callable

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
WRAPPER = SCRIPT_DIR / "prod_dr_cutover_uk.sh"

# Import the engine purely for its already-audited, read-only probe helpers and
# the RuntimeConfig/Audit/gate-writer utilities. No engine state-changing code
# path is invoked from here; state changes go through the wrapper subprocess.
sys.path.insert(0, str(SCRIPT_DIR))
import prod_dr_cutover as engine  # noqa: E402


class OrchestratorError(Exception):
    """Fatal orchestration error — abort the whole chain."""


# --------------------------------------------------------------------------- #
# Phase definitions
# --------------------------------------------------------------------------- #
# health: which post-phase topology we poll for before moving on.
PHASES: list[dict[str, Any]] = [
    {"key": "01_planned_switchover", "mode": "planned-switchover",
     "destructive_rebuild": False, "health": ("active", "dr")},
    {"key": "02_rebuild_dc1", "mode": "rebuild-dc1-standby",
     "destructive_rebuild": True, "health": ("standby_ready", "prod")},
    {"key": "03_switchback", "mode": "switchback",
     "destructive_rebuild": False, "health": ("active", "prod")},
    {"key": "04_rebuild_dc2", "mode": "rebuild-dc2-standby",
     "destructive_rebuild": True, "health": ("standby_ready", "dr")},
]

# Manual-gate steps that carry no downstream gate and need no proof: we assume
# the operator already logged both APIs into one session (handoff requirement),
# which context-check verifies.
MANUAL_NOOP_STEPS = {"login-prod", "login-dr"}

# Sentinel for manual-gate steps that perform an application-routing action but
# produce no .approved gate file consumed by a later step.
ROUTE_GATE = "__route__"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Side helpers (prod == DC1, dr == DC2)
# --------------------------------------------------------------------------- #
def side_ctx(cfg: "engine.RuntimeConfig", side: str) -> tuple[str, str, str]:
    if side == "prod":
        return cfg.prod_context, cfg.prod_namespace, cfg.prod_cluster
    if side == "dr":
        return cfg.dr_context, cfg.dr_namespace, cfg.dr_cluster
    raise OrchestratorError(f"unknown side {side!r}")


def live_states(cfg: "engine.RuntimeConfig", audit: "engine.Audit", side: str) -> list[dict[str, str]]:
    context, namespace, cluster = side_ctx(cfg, side)
    return engine.discover_database_recovery_states(
        cfg, context=context, namespace=namespace, cluster=cluster, audit=audit
    )


def primaries(states: list[dict[str, str]]) -> list[str]:
    return [r["name"] for r in states if r.get("pg_is_in_recovery") == "f"]


def recovery_pods(states: list[dict[str, str]]) -> list[str]:
    return [r["name"] for r in states if r.get("pg_is_in_recovery") == "t"]


# --------------------------------------------------------------------------- #
# Gate proofs — each returns (ok: bool, evidence: dict)
# --------------------------------------------------------------------------- #
def proof_active(cfg, audit, side: str, opts) -> tuple[bool, dict[str, Any]]:
    """Side has exactly one live, writable primary."""
    context, namespace, _ = side_ctx(cfg, side)
    states = live_states(cfg, audit, side)
    prim = primaries(states)
    ev: dict[str, Any] = {"side": side, "states": states, "primaries": prim}
    if len(prim) != 1:
        ev["detail"] = f"expected exactly 1 live primary, found {len(prim)}"
        return False, ev
    pod = prim[0]
    read_only = engine.psql_scalar(
        cfg, context=context, namespace=namespace, pod=pod,
        sql="select current_setting('transaction_read_only');",
        audit=audit, purpose=f"Confirm {side} primary {pod} is writable", allow_fail=True,
    ).strip()
    ev["pod"] = pod
    ev["transaction_read_only"] = read_only
    ok = read_only.lower() == "off"
    if not ok:
        ev["detail"] = f"primary is not writable (transaction_read_only={read_only!r})"
    return ok, ev


def proof_stopped(cfg, audit, side: str, opts) -> tuple[bool, dict[str, Any]]:
    """Side has zero Running database pods (fenced / shut down)."""
    states = live_states(cfg, audit, side)
    ev = {"side": side, "running_db_pods": [r["name"] for r in states], "count": len(states)}
    ok = len(states) == 0
    if not ok:
        ev["detail"] = "database pods still running; side is not fenced/stopped"
    return ok, ev


def lsn_byte_lag(cfg, audit, active_side: str, active_pod: str,
                 standby_side: str, standby_pod: str) -> int | None:
    """Byte lag = active primary current WAL LSN minus standby replay LSN.

    LSN-based, like the engine: robust on an idle database (where time-based
    replay-delay is meaningless) and the only valid catch-up measure when the
    standby follows via S3 archive rather than streaming.
    """
    a_ctx, a_ns, _ = side_ctx(cfg, active_side)
    s_ctx, s_ns, _ = side_ctx(cfg, standby_side)
    active_lsn = engine.psql_scalar(
        cfg, context=a_ctx, namespace=a_ns, pod=active_pod,
        sql="select pg_current_wal_lsn()::text;",
        audit=audit, purpose=f"Active {active_side} current WAL LSN", allow_fail=True).strip()
    standby_lsn = engine.psql_scalar(
        cfg, context=s_ctx, namespace=s_ns, pod=standby_pod,
        sql="select pg_last_wal_replay_lsn()::text;",
        audit=audit, purpose=f"Standby {standby_side} replay LSN", allow_fail=True).strip()
    if not active_lsn or not standby_lsn:
        return None
    diff = engine.psql_scalar(
        cfg, context=a_ctx, namespace=a_ns, pod=active_pod,
        sql=f"select pg_wal_lsn_diff({engine.sql_literal(active_lsn)}, {engine.sql_literal(standby_lsn)})::bigint;",
        audit=audit, purpose=f"LSN byte lag {active_side}->{standby_side}", allow_fail=True).strip()
    return engine.to_int(diff)


def proof_standby_ready(cfg, audit, side: str, opts) -> tuple[bool, dict[str, Any]]:
    """`side` is a healthy standby of the active site: in recovery, not paused,
    and caught up by LSN byte-lag. A live WAL receiver is required only when
    streaming is expected; with --allow-archive-only it may follow via S3."""
    active = "dr" if side == "prod" else "prod"
    context, namespace, _ = side_ctx(cfg, side)
    states = live_states(cfg, audit, side)
    prim = primaries(states)
    rec = recovery_pods(states)
    ev: dict[str, Any] = {"side": side, "active_side": active, "states": states,
                          "allow_archive_only": opts.allow_archive_only}
    if prim:
        ev["detail"] = f"side still has a live primary {prim}; not a pure standby"
        return False, ev
    if not rec:
        ev["detail"] = "no in-recovery database pod found"
        return False, ev
    pod = rec[0]
    summary = engine.psql_json(
        cfg, context=context, namespace=namespace, pod=pod,
        sql=engine.sql_wal_receiver_summary(),
        audit=audit, purpose=f"WAL receiver summary on {side} standby {pod}", allow_fail=True)
    ev["pod"] = pod
    ev["wal_receiver_summary"] = summary
    if not summary:
        ev["detail"] = "wal receiver summary unavailable"
        return False, ev
    in_recovery = bool(summary.get("in_recovery"))
    wal_count = engine.to_int(summary.get("wal_receiver_count")) or 0
    replay_paused = summary.get("replay_paused")

    # Caught-up check: need the active side to have a single primary to diff against.
    active_states = live_states(cfg, audit, active)
    active_prims = primaries(active_states)
    if len(active_prims) != 1:
        ev["detail"] = f"active side {active} has {len(active_prims)} primaries; cannot measure lag"
        return False, ev
    lag = lsn_byte_lag(cfg, audit, active, active_prims[0], side, pod)
    ev["lag_bytes"] = lag
    ev["standby_ready_max_lag_bytes"] = opts.standby_ready_max_lag_bytes

    streaming_ok = wal_count >= 1
    if not streaming_ok and not opts.allow_archive_only:
        ev["detail"] = ("no active WAL receiver and --allow-archive-only not set "
                        "(DR->PROD streaming blocked? pass --allow-archive-only for S3 mode)")
        return False, ev
    caught_up = lag is not None and 0 <= lag <= opts.standby_ready_max_lag_bytes
    ok = in_recovery and replay_paused is False and caught_up
    if not ok:
        ev["detail"] = (f"in_recovery={in_recovery} replay_paused={replay_paused} "
                        f"wal_receiver_count={wal_count} lag_bytes={lag} "
                        f"max={opts.standby_ready_max_lag_bytes} "
                        f"(streaming={'yes' if streaming_ok else 'no/archive-only'})")
    ev["mode"] = "streaming" if streaming_ok else "archive-only"
    return ok, ev


def proof_app_frozen(cfg, audit, side: str, opts) -> tuple[bool, dict[str, Any]]:
    """Application writes are frozen on `side`: zero active client sessions.

    Read-only assertion that the live primary has no active / idle-in-transaction
    client sessions. The freeze hook itself is run ONCE by satisfy_gate before
    this proof is polled (a retried proof must not re-fire the hook — hooks may
    not be idempotent, e.g. one that scales apps down remembering replica counts).
    """
    ev: dict[str, Any] = {"side": side}
    context, namespace, _ = side_ctx(cfg, side)
    states = live_states(cfg, audit, side)
    prim = primaries(states)
    if len(prim) != 1:
        ev["detail"] = f"expected exactly 1 live primary to inspect sessions, found {len(prim)}"
        ev["states"] = states
        return False, ev
    pod = prim[0]
    summary = engine.psql_json(
        cfg, context=context, namespace=namespace, pod=pod,
        sql=engine.sql_application_session_summary(opts.freeze_ignore_users),
        audit=audit, purpose=f"Application session summary on {side} primary {pod}", allow_fail=True,
    )
    ev["pod"] = pod
    ev["session_summary"] = summary
    if summary is None:
        ev["detail"] = "session summary unavailable"
        return False, ev
    active = engine.to_int(summary.get("active_application_sessions"))
    ok = active == 0
    if not ok:
        ev["detail"] = f"{active} active application session(s) still connected to {side}"
    return ok, ev


def proof_rebuild_approved(cfg, audit, stale: str, active: str, opts) -> tuple[bool, dict[str, Any]]:
    """The destructive PVC-delete gate. Require all three, fail closed:
        1. the stale side has NO running database pods (already shut down),
        2. the active side has exactly one live writable primary,
        3. a fresh pgBackRest backup exists on the active side / S3.
    """
    ev: dict[str, Any] = {"stale_side": stale, "active_side": active}

    stale_states = live_states(cfg, audit, stale)
    ev["stale_running_db_pods"] = [r["name"] for r in stale_states]
    if len(stale_states) != 0:
        ev["detail"] = "stale side still has running DB pods; refusing PVC delete"
        return False, ev

    ok_active, active_ev = proof_active(cfg, audit, active, opts)
    ev["active_side_check"] = active_ev
    if not ok_active:
        ev["detail"] = "active side is not a single writable primary; refusing PVC delete"
        return False, ev

    a_ctx, a_ns, _ = side_ctx(cfg, active)
    active_pod = active_ev["pod"]
    info = engine.pgbackrest_info(
        cfg, context=a_ctx, namespace=a_ns, pod=active_pod,
        audit=audit, purpose=f"pgBackRest info on active {active} pod {active_pod}",
    )
    summary = engine.summarize_pgbackrest(info)
    ev["pgbackrest"] = {k: summary.get(k) for k in ("ok", "name", "backup_count", "latest_backup")}
    if not summary.get("ok"):
        ev["detail"] = "pgBackRest stanza not ok on active side"
        return False, ev
    latest = summary.get("latest_backup")
    if not latest:
        ev["detail"] = "no pgBackRest backup found on active side"
        return False, ev
    stop_epoch = (latest.get("timestamp") or {}).get("stop")
    if stop_epoch is None:
        ev["detail"] = "latest backup has no stop timestamp"
        return False, ev
    age = time.time() - float(stop_epoch)
    ev["latest_backup_age_seconds"] = int(age)
    ev["backup_max_age_seconds"] = opts.backup_max_age_seconds
    if age > opts.backup_max_age_seconds:
        ev["detail"] = (
            f"latest backup is {int(age)}s old, older than max {opts.backup_max_age_seconds}s"
        )
        return False, ev
    return True, ev


# Map: manual-gate step id -> (gate_name_to_write_or_ROUTE, proof_callable, poll_kind).
# proof_callable(cfg, audit, opts) -> (ok, evidence).
# poll_kind selects how long the proof is retried while a just-issued state change
# converges: "settle" (pods terminating / standby catching up / role flipping) or
# "freeze" (client sessions draining after a freeze hook). Route steps use None.
def build_gate_proofs() -> dict[str, tuple[str, Callable | None, str | None]]:
    return {
        # ---- planned-switchover --------------------------------------------
        "manual-freeze-apps": (
            engine.APPLICATION_WRITES_FROZEN_GATE,
            lambda cfg, a, o: proof_app_frozen(cfg, a, "prod", o), "freeze"),
        "manual-route-apps-dr": (ROUTE_GATE, None, None),
        # ---- rebuild-dc1-standby (stale=prod/DC1, active=dr/DC2) -----------
        "manual-confirm-dc2-active-before-dc1-rebuild": (
            engine.DC2_ACTIVE_CONFIRMED_GATE,
            lambda cfg, a, o: proof_active(cfg, a, "dr", o), "settle"),
        "manual-approve-dc1-pvc-delete": (
            engine.DC1_REBUILD_APPROVED_GATE,
            lambda cfg, a, o: proof_rebuild_approved(cfg, a, "prod", "dr", o), "settle"),
        "manual-confirm-dc1-standby-ready": (
            engine.DC1_STANDBY_READY_GATE,
            lambda cfg, a, o: proof_standby_ready(cfg, a, "prod", o), "settle"),
        # ---- switchback (active=dr/DC2 being shut down, target=prod/DC1) ----
        "manual-freeze-apps-dc2": (
            engine.APPLICATION_WRITES_FROZEN_GATE,
            lambda cfg, a, o: proof_app_frozen(cfg, a, "dr", o), "freeze"),
        "manual-confirm-dc2-stopped": (
            engine.DC2_FENCED_OR_SHUTDOWN_GATE,
            lambda cfg, a, o: proof_stopped(cfg, a, "dr", o), "settle"),
        "manual-route-apps-dc1": (ROUTE_GATE, None, None),
        # ---- rebuild-dc2-standby (stale=dr/DC2, active=prod/DC1) -----------
        "manual-confirm-dc1-active-before-dc2-rebuild": (
            engine.DC1_ACTIVE_CONFIRMED_GATE,
            lambda cfg, a, o: proof_active(cfg, a, "prod", o), "settle"),
        "manual-approve-dc2-pvc-delete": (
            engine.DC2_REBUILD_APPROVED_GATE,
            lambda cfg, a, o: proof_rebuild_approved(cfg, a, "dr", "prod", o), "settle"),
        "manual-confirm-dc2-standby-ready": (
            engine.DC2_STANDBY_READY_GATE,
            lambda cfg, a, o: proof_standby_ready(cfg, a, "dr", o), "settle"),
    }


def prove_with_retry(proof: Callable, cfg, audit, opts, timeout: int) -> tuple[bool, dict[str, Any]]:
    """Poll a gate proof until it passes or `timeout` elapses (sleeping
    settle_interval between tries). State changes issued by the prior step take
    time to converge, so a transient miss is retried rather than treated as
    fatal. The last evidence is returned on timeout for the abort message."""
    deadline = time.time() + max(timeout, 0)
    attempt = 0
    ok, evidence = False, {}
    while True:
        attempt += 1
        ok, evidence = proof(cfg, audit, opts)
        if ok or time.time() >= deadline:
            evidence["proof_attempts"] = attempt
            return ok, evidence
        print(f"    proof not yet satisfied (attempt {attempt}): {evidence.get('detail')}; "
              f"retry in {opts.settle_interval}s")
        time.sleep(opts.settle_interval)


# --------------------------------------------------------------------------- #
# Subprocess helpers (always via the UK wrapper)
# --------------------------------------------------------------------------- #
def run_wrapper(subcmd: str, extra: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = [str(WRAPPER), subcmd, *extra]
    print(f"\n>>> {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, text=True, capture_output=capture, env=os.environ.copy())


def run_hook(hook: str, purpose: str) -> None:
    print(f"\n>>> HOOK ({purpose}): {hook}", flush=True)
    proc = subprocess.run(hook, shell=True, text=True, env=os.environ.copy())
    if proc.returncode != 0:
        raise OrchestratorError(f"hook failed (rc={proc.returncode}) for: {purpose}")


# --------------------------------------------------------------------------- #
# Phase mechanics
# --------------------------------------------------------------------------- #
def wrapper_context_check(run_root: pathlib.Path, mode: str) -> None:
    proc = run_wrapper("context-check", ["--mode", mode, "--run-root", str(run_root)])
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    if proc.returncode != 0 or "Context check status: PASS" not in (proc.stdout or ""):
        raise OrchestratorError("context-check did not PASS; aborting (need both prod+dr visible)")


def wrapper_precheck(mode: str, run_root: pathlib.Path, opts) -> pathlib.Path:
    extra = ["--mode", mode, "--run-root", str(run_root)]
    extra += ["--max-lag-bytes", str(opts.max_lag_bytes)]
    if opts.allow_archive_only:
        # DR follows PROD over S3 (DR->PROD streaming blocked). The engine accepts
        # an inactive WAL receiver only with this flag AND zero LSN lag.
        extra.append("--allow-archive-only-catchup")
    proc = run_wrapper("precheck", extra)
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    out = proc.stdout or ""
    if proc.returncode != 0 or "Precheck status: PASS" not in out:
        raise OrchestratorError(f"precheck for mode={mode} did not PASS; aborting")
    run_dir = None
    for line in out.splitlines():
        if line.startswith("Precheck file:"):
            run_dir = pathlib.Path(line.split(":", 1)[1].strip()).parent
            break
    if not run_dir:
        raise OrchestratorError("could not parse precheck run directory from wrapper output")
    return run_dir


def wrapper_generate(mode: str, run_dir: pathlib.Path, destructive_rebuild: bool) -> dict[str, Any]:
    extra = ["--mode", mode, "--run-dir", str(run_dir)]
    if destructive_rebuild:
        extra.append("--include-destructive-rebuild")
    proc = run_wrapper("generate", extra)
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    if proc.returncode != 0:
        raise OrchestratorError(f"generate for mode={mode} failed; aborting")
    manifest_path = run_dir / "command_manifest.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def wrapper_execute_step(step: dict[str, Any], manifest_path: pathlib.Path,
                         token: str, opts) -> None:
    step_id = step["id"]
    extra = ["--manifest", str(manifest_path), "--step", step_id]
    if opts.dry_run:
        extra.append("--dry-run-execute")
    else:
        if step.get("state_changing"):
            extra += ["--execute-state-changing", "--approval-token", token]
            if step.get("risk") == "High":
                extra.append("--confirm-prod-impact")
    if step.get("internal_action") in ("final_lag_check", "reverse_final_lag_check"):
        extra += ["--max-lag-bytes", str(opts.max_lag_bytes)]
    proc = run_wrapper("execute", extra)
    out = proc.stdout or ""
    err = proc.stderr or ""
    sys.stdout.write(out)
    sys.stderr.write(err)
    if proc.returncode != 0:
        raise OrchestratorError(f"execute step {step_id} returned rc={proc.returncode}")
    # The engine silently SKIPs a state-changing step whose gate/token is missing
    # (stdout, rc=0). ERROR <id> goes to STDERR (normally with rc=1, which the rc
    # check above catches; scanning both streams keeps this redundant on purpose,
    # e.g. for --continue-on-error style engine changes).
    if not opts.dry_run:
        combined = out + "\n" + err
        if f"SKIP {step_id}" in combined:
            raise OrchestratorError(f"execute step {step_id} was SKIPPED by the engine: {combined.strip()[-400:]}")
        if f"ERROR {step_id}" in combined:
            raise OrchestratorError(f"execute step {step_id} reported ERROR: {combined.strip()[-400:]}")


def satisfy_gate(step: dict[str, Any], cfg, audit, run_dir: pathlib.Path,
                 gate_proofs: dict[str, tuple[str, Callable | None, str | None]], opts) -> None:
    step_id = step["id"]
    gate_name, proof, poll_kind = gate_proofs[step_id]
    if gate_name == ROUTE_GATE:
        # Application routing flip. No downstream gate file; perform the hook if
        # provided, otherwise record a drill no-op.
        if opts.route_hook and opts.arm:
            run_hook(opts.route_hook, f"route applications for {step_id}")
        else:
            print(f"[{step_id}] routing step — no route-hook configured; drill no-op recorded.")
        audit.append(f"ROUTE STEP {step_id} handled at {now()} (hook={'yes' if opts.route_hook else 'no'})")
        return

    if opts.dry_run:
        print(f"[DRY-RUN] would prove gate '{gate_name}' for step {step_id} "
              f"(no proof run, no gate file written).")
        return

    timeout = opts.freeze_drain_seconds if poll_kind == "freeze" else opts.settle_timeout
    if poll_kind == "freeze" and opts.freeze_hook and opts.arm:
        # Fire the (state-changing) freeze hook exactly once; the polled proof
        # below stays read-only so retries never re-trigger the hook.
        run_hook(opts.freeze_hook, f"freeze application writes before {step_id}")
        audit.append(f"FREEZE HOOK run once for {step_id} at {now()}: {opts.freeze_hook}")
    print(f"[{step_id}] proving gate '{gate_name}' (poll up to {timeout}s) ...")
    ok, evidence = prove_with_retry(proof, cfg, audit, opts, timeout)
    evidence = {**evidence, "step_id": step_id, "proved_at": now()}
    if not ok:
        engine.safe_json_dump(evidence, run_dir / f"{gate_name}.proof_failed.json")
        raise OrchestratorError(
            f"gate proof FAILED for {step_id} -> {gate_name}: {evidence.get('detail', 'see proof_failed.json')}"
        )
    gate_file = engine.write_approval_gate(run_dir, gate_name, evidence)
    print(f"[{step_id}] proof PASS -> wrote gate {gate_file}")


def run_phase(phase: dict[str, Any], opts, state: dict[str, Any],
              gate_proofs: dict[str, tuple[str, Callable]]) -> None:
    key, mode = phase["key"], phase["mode"]
    phase_root = opts.run_root / key
    phase_root.mkdir(parents=True, exist_ok=True)
    pstate = state["phases"].setdefault(key, {"status": "pending", "executed_steps": []})

    print(f"\n{'#' * 70}\n# PHASE {key}  (mode={mode}, arm={opts.arm}, dry_run={opts.dry_run})\n{'#' * 70}")

    wrapper_context_check(phase_root, mode)

    # Reuse the precheck/manifest from a resumed run if present; else build fresh.
    run_dir = pathlib.Path(pstate["run_dir"]) if pstate.get("run_dir") else None
    if run_dir and (run_dir / "command_manifest.json").exists():
        print(f"[resume] reusing existing phase run dir: {run_dir}")
        manifest = json.loads((run_dir / "command_manifest.json").read_text(encoding="utf-8"))
    else:
        run_dir = wrapper_precheck(mode, phase_root, opts)
        manifest = wrapper_generate(mode, run_dir, phase["destructive_rebuild"])
        pstate["run_dir"] = str(run_dir)
        save_state(opts.run_root, state)

    cfg = engine.config_from_manifest(manifest)
    audit = engine.Audit(run_dir)
    token = manifest.get("approval_token", "")
    manifest_path = run_dir / "command_manifest.json"
    done = set(pstate["executed_steps"])

    for step in manifest.get("steps", []):
        step_id = step["id"]
        if step_id in done:
            print(f"[skip-resume] {step_id} already completed")
            continue

        if step_id in MANUAL_NOOP_STEPS:
            print(f"[{step_id}] manual login step — assumed satisfied (both APIs in session).")
        elif step_id in gate_proofs:
            satisfy_gate(step, cfg, audit, run_dir, gate_proofs, opts)
        elif step.get("manual_gate") or not step.get("automatically_executable", True):
            # An unmapped manual step would otherwise stall the chain silently.
            raise OrchestratorError(
                f"unmapped manual/non-auto step {step_id!r} in mode {mode}; "
                "refusing to proceed (no proof defined)."
            )
        else:
            wrapper_execute_step(step, manifest_path, token, opts)

        pstate["executed_steps"].append(step_id)
        save_state(opts.run_root, state)

    # Inter-phase settle: poll until the post-phase topology is healthy.
    if not opts.dry_run:
        settle(cfg, audit, phase["health"], opts)

    pstate["status"] = "completed"
    save_state(opts.run_root, state)
    print(f"# PHASE {key} COMPLETE")


def settle(cfg, audit, health: tuple[str, str], opts) -> None:
    kind, side = health
    proof = {"active": proof_active, "standby_ready": proof_standby_ready}[kind]
    deadline = time.time() + opts.settle_timeout
    attempt = 0
    while True:
        attempt += 1
        ok, ev = proof(cfg, audit, side, opts)
        if ok:
            print(f"[settle] {kind} on {side} healthy after {attempt} check(s).")
            return
        if time.time() >= deadline:
            raise OrchestratorError(
                f"settle timeout: {kind} on {side} not healthy after "
                f"{opts.settle_timeout}s — last: {ev.get('detail')}"
            )
        print(f"[settle] waiting for {kind} on {side} ({ev.get('detail')}); "
              f"retry in {opts.settle_interval}s")
        time.sleep(opts.settle_interval)


# --------------------------------------------------------------------------- #
# State persistence
# --------------------------------------------------------------------------- #
def state_path(run_root: pathlib.Path) -> pathlib.Path:
    return run_root / "orchestrator_state.json"


def load_state(run_root: pathlib.Path) -> dict[str, Any]:
    p = state_path(run_root)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"created_at": now(), "phases": {}}


def save_state(run_root: pathlib.Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now()
    engine.safe_json_dump(state, state_path(run_root))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Hands-off DR-drill orchestrator for the UK prod/dr cutover lifecycle.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--arm", action="store_true",
                      help="Actually execute state-changing steps and write proven gate files.")
    mode.add_argument("--dry-run", action="store_true", default=False,
                      help="Walk all phases with execute --dry-run-execute; write no gate files (DEFAULT).")
    p.add_argument("--run-root", type=pathlib.Path,
                   default=SCRIPT_DIR / "cutover_runs_uk_orchestrated" / dt.datetime.now().strftime("%Y%m%d_%H%M%S"),
                   help="Run root holding per-phase dirs + orchestrator_state.json (reuse to resume).")
    p.add_argument("--phases", default="all",
                   help="Comma-separated subset of phase keys, or 'all' (default).")
    p.add_argument("--max-lag-bytes", type=int, default=0,
                   help="Allowed final cutover lag (no data loss). Keep 0.")
    p.add_argument("--allow-archive-only", action="store_true",
                   help="DR follows PROD via S3 archive (DR->PROD streaming blocked). "
                        "Passes --allow-archive-only-catchup to precheck and lets standby-ready "
                        "pass without a live WAL receiver when LSN lag is within tolerance.")
    p.add_argument("--standby-ready-max-lag-bytes", type=int, default=16 * 1024 * 1024,
                   help="LSN byte-lag tolerance for the standby-ready health gate "
                        "(default 16 MiB ~ one WAL segment; archive-only idle catch-up is "
                        "segment-granular). The zero-data-loss cutover lag stays --max-lag-bytes.")
    p.add_argument("--backup-max-age-seconds", type=int, default=86400,
                   help="Max age of the latest pgBackRest backup before a PVC-delete gate.")
    p.add_argument("--settle-timeout", type=int, default=1800,
                   help="Seconds to poll for post-phase topology health.")
    p.add_argument("--settle-interval", type=int, default=15,
                   help="Seconds between settle / gate-proof polls.")
    p.add_argument("--freeze-drain-seconds", type=int, default=120,
                   help="Max time to poll for client sessions to drain after a freeze hook.")
    p.add_argument("--freeze-hook", default=None,
                   help="Shell command to freeze application writes (run before the app-freeze proof when armed).")
    p.add_argument("--unfreeze-hook", default=None,
                   help="Shell command to unfreeze application writes (run on abort/cleanup).")
    p.add_argument("--route-hook", default=None,
                   help="Shell command to flip application routing to the new primary (run for route steps when armed).")
    p.add_argument("--freeze-ignore-users",
                   default="postgres,replication,_crunchyrepl,ccp_monitoring,rewind",
                   help="Comma-separated DB users excluded from the active-session freeze proof.")
    args = p.parse_args(argv)
    args.dry_run = not args.arm  # dry-run is the default unless explicitly armed
    args.freeze_ignore_users = engine.parse_csv_list(args.freeze_ignore_users)
    return args


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    if not WRAPPER.exists():
        print(f"ERROR: UK wrapper not found next to this script: {WRAPPER}", file=sys.stderr)
        return 2

    opts.run_root.mkdir(parents=True, exist_ok=True)
    if os.environ.get("UK_USE_CURRENT_CONTEXT", "1") != "0":
        print("WARNING: UK_USE_CURRENT_CONTEXT != 0. A real cross-site switchover needs "
              "named-context mode (set UK_USE_CURRENT_CONTEXT=0 with both APIs logged in).",
              file=sys.stderr)

    selected = PHASES if opts.phases == "all" else [
        ph for ph in PHASES if ph["key"] in set(engine.parse_csv_list(opts.phases))]
    if not selected:
        print(f"ERROR: no phases matched --phases={opts.phases}", file=sys.stderr)
        return 2

    state = load_state(opts.run_root)
    gate_proofs = build_gate_proofs()

    print(f"Orchestrator run-root: {opts.run_root}")
    print(f"Mode: {'ARMED (state-changing)' if opts.arm else 'DRY-RUN (no changes)'}")
    print(f"Phases: {', '.join(ph['key'] for ph in selected)}")

    try:
        for phase in selected:
            if state["phases"].get(phase["key"], {}).get("status") == "completed":
                print(f"[skip] phase {phase['key']} already completed in this run-root.")
                continue
            run_phase(phase, opts, state, gate_proofs)
    except OrchestratorError as exc:
        print(f"\nABORT: {exc}", file=sys.stderr)
        if opts.unfreeze_hook and opts.arm:
            try:
                run_hook(opts.unfreeze_hook, "unfreeze applications after abort")
            except OrchestratorError as hook_exc:
                print(f"  (unfreeze hook also failed: {hook_exc})", file=sys.stderr)
        print(f"State saved at {state_path(opts.run_root)} — fix the cause and re-run with the "
              f"same --run-root to resume.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted; state saved. Re-run with the same --run-root to resume.", file=sys.stderr)
        if opts.unfreeze_hook and opts.arm:
            try:
                run_hook(opts.unfreeze_hook, "unfreeze applications after interrupt")
            except OrchestratorError as hook_exc:
                print(f"  (unfreeze hook also failed: {hook_exc})", file=sys.stderr)
        return 130

    # End-of-chain unfreeze: phase 3 freezes writes on DC2 and nothing downstream
    # releases it; without this a fully successful drill ends with apps frozen.
    if opts.unfreeze_hook and opts.arm:
        try:
            run_hook(opts.unfreeze_hook, "unfreeze applications after successful chain")
        except OrchestratorError as hook_exc:
            print(f"WARNING: chain completed but unfreeze hook failed: {hook_exc}", file=sys.stderr)

    print("\nAll selected phases completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
