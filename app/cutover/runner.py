"""Subprocess supervisor for the vendored cutover engine/orchestrator.

Console approval only grants permission to run the vendored orchestrator
(with --arm for the armed tier). Every engine-level safety mechanism —
approval token, --execute-state-changing, --confirm-prod-impact, gate files
written from live proofs — stays inside the vendored, live-tested code.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import signal
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from psycopg.types.json import Jsonb

from app import cutover as vendor_pkg
from app.cutover import config as cutover_config
from app.cutover.wrapper import render_wrapper

CANCEL_GRACE_SECONDS = int(os.environ.get("CUTOVER_CANCEL_GRACE", "120"))
TIMEOUT_SINGLE_SECONDS = int(os.environ.get("CUTOVER_TIMEOUT_SINGLE", str(6 * 3600)))
TIMEOUT_DRILL_SECONDS = int(os.environ.get("CUTOVER_TIMEOUT_DRILL", str(14 * 3600)))
PATH_PREPEND = os.environ.get("CUTOVER_PATH_PREPEND", "")
LOG_FLUSH_INTERVAL = 0.5
LOG_FLUSH_LINES = 50
EXCERPT_CHARS = 4000

# Orchestrator phase key -> (engine mode, destructive rebuild steps)
PHASE_DEFS: dict[str, tuple[str, bool]] = {
    "01_planned_switchover": ("planned-switchover", False),
    "02_rebuild_dc1": ("rebuild-dc1-standby", True),
    "03_switchback": ("switchback", False),
    "04_rebuild_dc2": ("rebuild-dc2-standby", True),
}


@dataclass
class ActiveRun:
    job_id: str
    proc: asyncio.subprocess.Process | None = None
    cancel_requested: bool = False
    task: Any | None = None


ACTIVE: dict[str, ActiveRun] = {}

# The uvicorn event loop, captured at startup. Sync endpoints run in a
# threadpool where get_event_loop() raises; dispatch must target this loop.
MAIN_LOOP: asyncio.AbstractEventLoop | None = None


def capture_loop() -> None:
    global MAIN_LOOP
    try:
        MAIN_LOOP = asyncio.get_running_loop()
    except RuntimeError:
        MAIN_LOOP = None


def _console():
    import app.main as console

    return console


# ─── workspace ───────────────────────────────────────────────────────────────

def workspace_dir(job_id: str | uuid.UUID) -> pathlib.Path:
    return pathlib.Path(cutover_config.CUTOVER_RUN_ROOT) / str(job_id)


def build_workspace(job_id: str | uuid.UUID, region_id: str, config: dict[str, Any]) -> pathlib.Path:
    """Create the per-job workspace: verified vendored scripts + rendered wrapper."""
    ws = workspace_dir(job_id)
    ws.mkdir(parents=True, exist_ok=True)
    for name in (vendor_pkg.ENGINE_FILE, vendor_pkg.ORCHESTRATOR_FILE):
        shutil.copy2(vendor_pkg.VENDOR_DIR / name, ws / name)
    vendor_pkg.verify_vendor(directory=ws)
    wrapper_path = ws / vendor_pkg.WRAPPER_FILE
    wrapper_path.write_text(render_wrapper(region_id, config), encoding="utf-8")
    wrapper_path.chmod(0o755)
    (ws / "runs").mkdir(exist_ok=True)
    (ws / "preview").mkdir(exist_ok=True)
    return ws


# ─── job log batching ────────────────────────────────────────────────────────

def append_job_log_batch(job_id: uuid.UUID, entries: list[tuple[str, str]]) -> None:
    """Insert many (stream, line) rows in one commit; per-line commits would
    hammer the metadata DB during multi-hour restores."""
    if not entries:
        return
    console = _console()
    ddl_schema = console.schema_name()
    db = console.require_pool()
    with db.connection() as conn, conn.cursor() as cur:
        cur.executemany(
            f"insert into {ddl_schema}.console_job_logs (job_id, stream, line) values (%s, %s, %s)",
            [(job_id, stream, line[:8000]) for stream, line in entries],
        )
        conn.commit()


class LogPump:
    """Buffers subprocess output lines and flushes them to console_job_logs."""

    def __init__(self, job_id: uuid.UUID) -> None:
        self.job_id = job_id
        self.buffer: list[tuple[str, str]] = []
        self.tail: dict[str, list[str]] = {"stdout": [], "stderr": []}
        self.lock = asyncio.Lock()

    async def add(self, stream: str, line: str) -> None:
        async with self.lock:
            self.buffer.append((stream, line))
            tail = self.tail.setdefault(stream, [])
            tail.append(line)
            if len(tail) > 80:
                del tail[: len(tail) - 80]
            if len(self.buffer) >= LOG_FLUSH_LINES:
                await self._flush_locked()

    async def _flush_locked(self) -> None:
        batch, self.buffer = self.buffer, []
        if batch:
            await asyncio.to_thread(append_job_log_batch, self.job_id, batch)

    async def flush(self) -> None:
        async with self.lock:
            await self._flush_locked()

    def excerpt(self, stream: str) -> str:
        return "\n".join(self.tail[stream])[-EXCERPT_CHARS:]


async def _pump_stream(
    reader: asyncio.StreamReader,
    stream: str,
    pump: LogPump,
    on_line=None,
) -> None:
    while True:
        raw = await reader.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        await pump.add(stream, line)
        if on_line is not None:
            on_line(line)


async def _flusher(pump: LogPump, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=LOG_FLUSH_INTERVAL)
        except asyncio.TimeoutError:
            pass
        await pump.flush()


# ─── DB state helpers ────────────────────────────────────────────────────────

def _update_job(job_id: uuid.UUID, **fields: Any) -> None:
    console = _console()
    ddl_schema = console.schema_name()
    sets = []
    values: list[Any] = []
    for key, value in fields.items():
        if key in {"payload", "result"} and isinstance(value, dict):
            value = Jsonb(value)
        sets.append(f"{key} = %s")
        values.append(value)
    values.append(job_id)
    db = console.require_pool()
    with db.connection() as conn:
        conn.execute(
            f"update {ddl_schema}.console_jobs set {', '.join(sets)} where id = %s",
            values,
        )
        conn.commit()


def _update_run(job_id: uuid.UUID, **fields: Any) -> None:
    console = _console()
    ddl_schema = console.schema_name()
    sets = []
    values: list[Any] = []
    for key, value in fields.items():
        if key == "progress" and isinstance(value, dict):
            value = Jsonb(value)
        sets.append(f"{key} = %s")
        values.append(value)
    values.append(job_id)
    db = console.require_pool()
    with db.connection() as conn:
        conn.execute(
            f"update {ddl_schema}.console_cutover_runs set {', '.join(sets)} where job_id = %s",
            values,
        )
        conn.commit()


def insert_run_row(
    job_id: uuid.UUID,
    config_id: str,
    mode: str,
    tier: str,
    run_root: str,
    resumes_job: uuid.UUID | None = None,
) -> None:
    console = _console()
    ddl_schema = console.schema_name()
    db = console.require_pool()
    with db.connection() as conn:
        conn.execute(
            f"""
            insert into {ddl_schema}.console_cutover_runs
              (job_id, config_id, mode, tier, run_root, resumes_job)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (job_id, config_id, mode, tier, run_root, resumes_job),
        )
        conn.commit()


# ─── preview (submit-time engine dry-run; calls no oc) ───────────────────────

def _phases_for_kind(kind: str) -> list[str]:
    meta = cutover_config.CUTOVER_JOB_KINDS[kind]
    if meta["phases"] == "all":
        return list(PHASE_DEFS)
    return [meta["phases"]]


def _summarize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    steps = manifest.get("steps") or []
    return {
        "mode": manifest.get("mode"),
        "step_count": len(steps),
        "state_changing": sum(1 for s in steps if s.get("state_changing")),
        "high_risk": sum(1 for s in steps if s.get("risk") == "High"),
        "steps": [
            {
                "id": s.get("id"),
                "purpose": s.get("purpose"),
                "state_changing": bool(s.get("state_changing")),
                "risk": s.get("risk"),
                "required_gates": s.get("required_gate_files") or [],
                "internal_action": s.get("internal_action"),
            }
            for s in steps
        ],
    }


async def run_preview(
    job_id: uuid.UUID,
    *,
    kind: str,
    workspace: pathlib.Path,
) -> dict[str, Any]:
    """Run the engine's `dry-run` subcommand per phase (manifest generation
    only, no oc) and return a per-phase step summary for the approver."""
    pump = LogPump(job_id)
    preview_root = workspace / "preview"
    wrapper = workspace / vendor_pkg.WRAPPER_FILE
    phases: dict[str, Any] = {}
    overall_rc = 0
    for phase_key in _phases_for_kind(kind):
        mode, destructive = PHASE_DEFS[phase_key]
        argv = [str(wrapper), "dry-run", "--mode", mode, "--run-root", str(preview_root)]
        if destructive:
            argv.append("--include-destructive-rebuild")
        await pump.add("event", f"preview: generating manifest for phase {phase_key} (mode {mode})")
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
            env=_subprocess_env(),
        )
        assert proc.stdout is not None and proc.stderr is not None
        await asyncio.gather(
            _pump_stream(proc.stdout, "stdout", pump),
            _pump_stream(proc.stderr, "stderr", pump),
        )
        rc = await proc.wait()
        overall_rc = overall_rc or rc
        manifest_path = _latest_manifest(preview_root, mode)
        summary: dict[str, Any] = {"rc": rc, "mode": mode, "destructive": destructive}
        if manifest_path is not None:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                summary.update(_summarize_manifest(manifest))
                summary["manifest_path"] = str(manifest_path)
            except Exception as exc:
                summary["manifest_error"] = str(exc)
        phases[phase_key] = summary
    await pump.flush()
    return {"rc": overall_rc, "phases": phases}


def _latest_manifest(preview_root: pathlib.Path, mode: str) -> pathlib.Path | None:
    candidates = sorted(
        (p for p in preview_root.glob(f"*{mode}*/command_manifest.json")),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def _subprocess_env(kubeconfig_path: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # Named-context mode: the console pod has no interactive oc login session.
    env["UK_USE_CURRENT_CONTEXT"] = "0"
    if kubeconfig_path:
        env["KUBECONFIG"] = kubeconfig_path
    if PATH_PREPEND:
        env["PATH"] = PATH_PREPEND + os.pathsep + env.get("PATH", "")
    return env


# ─── progress parsing ────────────────────────────────────────────────────────

class ProgressTracker:
    def __init__(self, job_id: uuid.UUID) -> None:
        self.job_id = job_id
        self.progress: dict[str, Any] = {"phases": {}, "current_phase": None, "settling": False}
        self._dirty = False

    def on_line(self, line: str) -> None:
        stripped = line.strip()
        if stripped.startswith("# PHASE ") and stripped.endswith(" COMPLETE"):
            key = stripped[len("# PHASE "):-len(" COMPLETE")].strip()
            self.progress["phases"].setdefault(key, {})["status"] = "completed"
            self.progress["settling"] = False
            self._dirty = True
        elif stripped.startswith("# PHASE "):
            key = stripped[len("# PHASE "):].split()[0]
            self.progress["current_phase"] = key
            self.progress["phases"].setdefault(key, {})["status"] = "running"
            self._dirty = True
        elif "--step" in stripped and " execute " in stripped:
            parts = stripped.split()
            if "--step" in parts:
                step = parts[parts.index("--step") + 1] if parts.index("--step") + 1 < len(parts) else None
                if step:
                    key = self.progress.get("current_phase")
                    if key:
                        self.progress["phases"].setdefault(key, {})["last_step"] = step
                        self._dirty = True
        elif stripped.startswith("[settle]"):
            self.progress["settling"] = True
            self._dirty = True

    def flush_if_dirty(self) -> None:
        if self._dirty:
            self._dirty = False
            _update_run(self.job_id, progress=self.progress)


# ─── execution (post-approval) ───────────────────────────────────────────────

async def execute_job(job_id: uuid.UUID) -> None:
    console = _console()
    job_id_str = str(job_id)
    active = ACTIVE.setdefault(job_id_str, ActiveRun(job_id=job_id_str))
    pump = LogPump(job_id)
    stop_flush = asyncio.Event()
    flusher = asyncio.create_task(_flusher(pump, stop_flush))
    final_state = "failed"
    result: dict[str, Any] = {}
    try:
        job = await asyncio.to_thread(console.get_job, job_id_str)
        run_row = await asyncio.to_thread(cutover_config.get_cutover_run, job_id_str)
        if run_row is None:
            raise RuntimeError("console_cutover_runs row missing")
        config_row = await asyncio.to_thread(cutover_config.get_cutover_config, run_row["config_id"])
        if config_row is None or not config_row.get("enabled"):
            raise RuntimeError(f"cutover config {run_row['config_id']} missing or disabled")

        await asyncio.to_thread(vendor_pkg.verify_vendor)
        ws = workspace_dir(job_id)
        if not ws.is_dir():
            ws = await asyncio.to_thread(
                build_workspace, job_id, config_row["id"], config_row.get("config") or {}
            )

        payload = job.get("payload") or {}
        options = payload.get("options") or {}
        tier = run_row["tier"]
        kind = job["kind"]
        phases = ",".join(_phases_for_kind(kind))
        run_root = run_row["run_root"]

        argv = [
            sys.executable,
            str(ws / vendor_pkg.ORCHESTRATOR_FILE),
            "--phases", phases,
            "--run-root", run_root,
            "--max-lag-bytes", str(int(options.get("max_lag_bytes", 0))),
            "--settle-timeout", str(int(options.get("settle_timeout", 7200))),
        ]
        if tier == "armed":
            argv.append("--arm")
        if options.get("allow_archive_only"):
            argv.append("--allow-archive-only")
        hooks = config_row.get("hooks") or {}
        # Hook commands come only from the admin-managed region config; the
        # submitter can merely toggle them.
        if options.get("freeze_hook_enabled") and str(hooks.get("freeze_hook") or "").strip():
            argv += ["--freeze-hook", hooks["freeze_hook"]]
            if str(hooks.get("unfreeze_hook") or "").strip():
                argv += ["--unfreeze-hook", hooks["unfreeze_hook"]]
        if options.get("route_hook_enabled") and str(hooks.get("route_hook") or "").strip():
            argv += ["--route-hook", hooks["route_hook"]]

        timeout_default = TIMEOUT_DRILL_SECONDS if kind == "cutover_full_drill" else TIMEOUT_SINGLE_SECONDS
        timeout_seconds = int(options.get("timeout_seconds", timeout_default))

        await pump.add("event", f"starting orchestrator: tier={tier} phases={phases} timeout={timeout_seconds}s")
        await pump.add("event", f">>> {' '.join(argv)}")

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ws),
            env=_subprocess_env(config_row.get("kubeconfig_path")),
            start_new_session=True,
        )
        active.proc = proc
        await asyncio.to_thread(_update_run, job_id, pid=proc.pid, started_at=console.datetime.now(console.timezone.utc))

        tracker = ProgressTracker(job_id)

        def on_line(line: str) -> None:
            tracker.on_line(line)

        assert proc.stdout is not None and proc.stderr is not None
        pumps = asyncio.gather(
            _pump_stream(proc.stdout, "stdout", pump, on_line),
            _pump_stream(proc.stderr, "stderr", pump),
        )

        async def progress_flusher() -> None:
            while proc.returncode is None:
                await asyncio.sleep(2)
                await asyncio.to_thread(tracker.flush_if_dirty)

        progress_task = asyncio.create_task(progress_flusher())
        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
            await pump.add("event", f"timeout after {timeout_seconds}s; cancelling orchestrator")
            await _terminate(proc, pump)
        await pumps
        progress_task.cancel()
        await asyncio.to_thread(tracker.flush_if_dirty)

        rc = proc.returncode
        state_file = pathlib.Path(run_root) / "orchestrator_state.json"
        phases_state: dict[str, Any] = {}
        if state_file.is_file():
            try:
                phases_state = json.loads(state_file.read_text(encoding="utf-8")).get("phases", {})
            except Exception:
                phases_state = {}

        if timed_out:
            final_state = "timed_out"
        elif active.cancel_requested:
            final_state = "cancelled"
        elif rc == 0:
            final_state = "succeeded"
        else:
            final_state = "failed"
        result = {
            "rc": rc,
            "tier": tier,
            "phases": phases_state,
            "run_root": run_root,
            "workspace": str(ws),
            "timed_out": timed_out,
            "cancelled": active.cancel_requested,
        }
        await pump.add("event", f"orchestrator finished rc={rc} -> job {final_state}")
    except Exception as exc:
        result = {"error": str(exc)}
        await pump.add("event", f"runner error: {exc}")
        final_state = "failed"
    finally:
        stop_flush.set()
        await flusher
        await pump.flush()
        now = console.datetime.now(console.timezone.utc)
        await asyncio.to_thread(
            _update_job,
            job_id,
            state=final_state,
            completed_at=now,
            result=result,
            stdout_excerpt=pump.excerpt("stdout"),
            stderr_excerpt=pump.excerpt("stderr"),
        )
        await asyncio.to_thread(_update_run, job_id, finished_at=now)
        await asyncio.to_thread(
            console.write_audit,
            f"job.{final_state}",
            "job",
            job_id_str,
            payload={"kind": "cutover", "result_rc": result.get("rc")},
            source="cutover",
        )
        ACTIVE.pop(job_id_str, None)


async def _terminate(proc: asyncio.subprocess.Process, pump: LogPump) -> None:
    """SIGINT (orchestrator saves state + unfreezes) → SIGTERM → SIGKILL."""
    for sig, wait_s in ((signal.SIGINT, CANCEL_GRACE_SECONDS), (signal.SIGTERM, 15), (signal.SIGKILL, 10)):
        if proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            return
        await pump.add("event", f"sent {sig.name} to orchestrator process group")
        try:
            await asyncio.wait_for(proc.wait(), timeout=wait_s)
            return
        except asyncio.TimeoutError:
            continue


def mark_pending_approval(job_id: uuid.UUID, payload: dict[str, Any]) -> None:
    _update_job(job_id, state="pending_approval", payload=payload)


def complete_job(job_id: uuid.UUID, state: str, result: dict[str, Any]) -> None:
    """Terminal update for jobs that finish at submit time (preview tier or a
    failed submit-time preview); execute_job handles its own finalization."""
    console = _console()
    now = console.datetime.now(console.timezone.utc)
    _update_job(job_id, state=state, completed_at=now, result=result)
    _update_run(job_id, finished_at=now)


def start_execution(job_id: uuid.UUID) -> None:
    """Called from the approve endpoint after the job row is set to running."""
    if MAIN_LOOP is None:
        raise RuntimeError("cutover runner has no event loop; cutover_startup() did not run")
    active = ACTIVE.setdefault(str(job_id), ActiveRun(job_id=str(job_id)))
    active.task = asyncio.run_coroutine_threadsafe(execute_job(job_id), MAIN_LOOP)


async def request_cancel(job_id: str) -> bool:
    active = ACTIVE.get(job_id)
    if active is None or active.proc is None:
        return False
    active.cancel_requested = True
    pump = LogPump(uuid.UUID(job_id))
    await pump.add("event", "cancel requested by user; sending SIGINT (orchestrator saves resumable state)")
    await pump.flush()
    await _terminate(active.proc, pump)
    return True


def sweep_orphans() -> int:
    """Mark unfinished runs from a previous process as failed (subprocesses die
    with uvicorn; the PVC-backed run_root keeps them resumable)."""
    console = _console()
    ddl_schema = console.schema_name()
    db = console.require_pool()
    from psycopg.rows import dict_row

    count = 0
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            select r.job_id, r.run_root, j.state
            from {ddl_schema}.console_cutover_runs r
            join {ddl_schema}.console_jobs j on j.id = r.job_id
            where r.finished_at is null and j.state in ('pending', 'running')
            """
        )
        rows = cur.fetchall()
        for row in rows:
            if str(row["job_id"]) in ACTIVE:
                continue
            cur.execute(
                f"""
                update {ddl_schema}.console_jobs
                set state = 'failed', completed_at = now(),
                    result = coalesce(result, '{{}}'::jsonb)
                             || jsonb_build_object('orphaned', true,
                                  'hint', 'console restarted mid-run; resume from run_root')
                where id = %s
                """,
                (row["job_id"],),
            )
            cur.execute(
                f"update {ddl_schema}.console_cutover_runs set finished_at = now() where job_id = %s",
                (row["job_id"],),
            )
            count += 1
        conn.commit()
    return count
