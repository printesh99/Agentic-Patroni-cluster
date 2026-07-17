"""APScheduler integration for Phase 7."""
from __future__ import annotations

import os
import time
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from . import alert_service, ai_agent_service, incident_service, snapshot_service

_scheduler: BackgroundScheduler | None = None
_last_run: dict[str, Any] | None = None
_last_error: str | None = None
_last_run_at: str | None = None
_last_agent_run: dict[str, Any] | None = None
_last_agent_error: str | None = None
_last_agent_run_at: str | None = None

_TRUE_VALUES = {"1", "true", "yes", "on"}
_JOB_ID = "ai-dba-health"
_AGENT_JOB_ID = "ai-agent-recommendations"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _interval_seconds() -> int:
    raw = (
        os.environ.get("AI_SCHEDULER_INTERVAL_SECONDS")
        or os.environ.get("AI_ML_SCORING_INTERVAL_SECONDS")
        or "300"
    )
    try:
        interval = int(raw)
    except (TypeError, ValueError):
        interval = 300
    return max(30, interval)


def _agent_interval_seconds() -> int:
    raw = os.environ.get("AI_AGENT_INTERVAL_MINUTES") or "30"
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = 30
    return max(1, minutes) * 60


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _last_run_summary() -> dict[str, Any] | None:
    if not _last_run:
        return None
    snapshot = _last_run.get("snapshot") if isinstance(_last_run, dict) else None
    incident_result = _last_run.get("incident") if isinstance(_last_run, dict) else None
    incident = incident_result.get("incident") if isinstance(incident_result, dict) else None
    return {
        "snapshot_available": snapshot.get("available") if isinstance(snapshot, dict) else None,
        "snapshot_id": snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None,
        "cluster_name": snapshot.get("cluster_name") if isinstance(snapshot, dict) else None,
        "incident_available": incident_result.get("available") if isinstance(incident_result, dict) else None,
        "incident_id": incident.get("id") if isinstance(incident, dict) else None,
        "incident_status": incident.get("status") if isinstance(incident, dict) else None,
        "incident_severity": incident.get("severity") if isinstance(incident, dict) else None,
    }


def tick() -> dict[str, Any]:
    global _last_run, _last_error, _last_run_at
    started_at = time.monotonic()
    run_at = _utc_now()
    result: dict[str, Any] = {
        "snapshot": None,
        "incident": None,
        "incident_evaluation_enabled": _env_bool("AI_SCHEDULER_INCIDENTS_ENABLED", True),
    }
    try:
        snapshot = snapshot_service.collect_and_persist()
        result["snapshot"] = snapshot
    except Exception as exc:
        _last_run = result
        _last_run_at = run_at
        _last_error = f"snapshot: {exc}"
        return {"available": False, "status": "error", "error": _last_error, "result": result}

    if result["incident_evaluation_enabled"]:
        try:
            incident = incident_service.evaluate_and_upsert()
            result["incident"] = incident
        except Exception as exc:
            # Keep the health snapshot visible even if downstream ML/AI incident
            # evaluation is not ready yet.
            _last_run = result
            _last_run_at = run_at
            _last_error = f"incident: {exc}"
            return {
                "available": True,
                "status": "partial",
                "error": _last_error,
                "result": result,
                "duration_ms": int((time.monotonic() - started_at) * 1000),
            }
    else:
        incident = None

    try:
        if incident and incident.get("incident"):
            alert_service.emit_for_incident(incident["incident"])
    except Exception as exc:
        _last_run = result
        _last_run_at = run_at
        _last_error = f"alert: {exc}"
        return {
            "available": True,
            "status": "partial",
            "error": _last_error,
            "result": result,
            "duration_ms": int((time.monotonic() - started_at) * 1000),
        }

    _last_run = result
    _last_run_at = run_at
    _last_error = None
    return {
        "available": True,
        "status": "ok",
        "result": _last_run,
        "duration_ms": int((time.monotonic() - started_at) * 1000),
    }


def agent_tick() -> dict[str, Any]:
    global _last_agent_run, _last_agent_error, _last_agent_run_at
    started_at = time.monotonic()
    run_at = _utc_now()
    try:
        lookback = int(os.environ.get("AI_AGENT_LOOKBACK_MINUTES") or "30")
        result = ai_agent_service.run_agent(
            {
                "cluster_name": os.environ.get("PGC_CLUSTER") or os.environ.get("CLUSTER_NAME"),
                "category": "ALL",
                "lookback_minutes": lookback,
            },
            trigger_type="SCHEDULED",
            triggered_by="scheduler",
            raise_on_overlap=False,
        )
        if _env_bool("LOG_INDEX_ENABLED", False):
            from ..ai import log_embeddings
            result["log_index"] = log_embeddings.index_cluster_logs(
                os.environ.get("PGC_CLUSTER") or os.environ.get("CLUSTER_NAME") or "uat")
        _last_agent_run = result
        _last_agent_run_at = run_at
        _last_agent_error = None if not result.get("error") else str(result.get("error"))
        return {
            "available": True,
            "status": "skipped" if result.get("skipped") else "ok",
            "result": result,
            "duration_ms": int((time.monotonic() - started_at) * 1000),
        }
    except Exception as exc:
        _last_agent_run_at = run_at
        _last_agent_error = str(exc)
        return {"available": False, "status": "error", "error": _last_agent_error}


def _ensure_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()
    return _scheduler


def start_pgprofile() -> dict[str, Any]:
    from ..pg_profile.config import settings as pgprofile_settings
    from ..pg_profile.service import scheduled_collect_all

    scheduler = _ensure_scheduler()
    job_id = "pgprofile-collector"
    if not scheduler.get_job(job_id):
        scheduler.add_job(
            scheduled_collect_all,
            "interval",
            minutes=pgprofile_settings.sample_interval_minutes,
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    return status()


def start(run_now: bool | None = None) -> dict[str, Any]:
    scheduler = _ensure_scheduler()
    if scheduler.get_job(_JOB_ID):
        return status()
    interval = _interval_seconds()
    scheduler.add_job(tick, "interval", seconds=interval, id=_JOB_ID, replace_existing=True, max_instances=1)
    should_run_now = run_now if run_now is not None else _env_bool("AI_SCHEDULER_RUN_ON_START", True)
    if should_run_now:
        tick()
    return status()


def start_agent(run_now: bool | None = None) -> dict[str, Any]:
    scheduler = _ensure_scheduler()
    if scheduler.get_job(_AGENT_JOB_ID):
        return status()
    scheduler.add_job(agent_tick, "interval", seconds=_agent_interval_seconds(), id=_AGENT_JOB_ID, replace_existing=True, max_instances=1)
    should_run_now = run_now if run_now is not None else _env_bool("AI_AGENT_RUN_ON_START", False)
    if should_run_now:
        agent_tick()
    return status()


def _job_details() -> list[dict[str, Any]]:
    if not _scheduler:
        return []
    rows: list[dict[str, Any]] = []
    for job in _scheduler.get_jobs():
        next_run = getattr(job, "next_run_time", None)
        rows.append({
            "id": job.id,
            "name": getattr(job, "name", None),
            "trigger": str(getattr(job, "trigger", "")),
            "next_run_at": next_run.isoformat() if next_run else None,
        })
    return rows


def stop() -> dict[str, Any]:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    return status()


def status() -> dict[str, Any]:
    enabled = bool(_scheduler and _scheduler.running)
    return {
        "available": True,
        "enabled": enabled,
        "env_enabled": _env_bool("AI_SCHEDULER_ENABLED", False),
        "interval_seconds": _interval_seconds(),
        "run_on_start": _env_bool("AI_SCHEDULER_RUN_ON_START", True),
        "incident_evaluation_enabled": _env_bool("AI_SCHEDULER_INCIDENTS_ENABLED", True),
        "last_error": _last_error,
        "has_last_run": _last_run is not None,
        "last_run_at": _last_run_at,
        "last_run_summary": _last_run_summary(),
        "ai_agent": {
            "env_enabled": _env_bool("AI_AGENT_SCHEDULER_ENABLED", False),
            "interval_seconds": _agent_interval_seconds(),
            "run_on_start": _env_bool("AI_AGENT_RUN_ON_START", False),
            "last_error": _last_agent_error,
            "has_last_run": _last_agent_run is not None,
            "last_run_at": _last_agent_run_at,
            "running": ai_agent_service.is_running(),
        },
        "jobs": [j.id for j in _scheduler.get_jobs()] if _scheduler else [],
        "job_details": _job_details(),
    }


def maybe_start_from_env() -> dict[str, Any]:
    result = status()
    if _env_bool("AI_SCHEDULER_ENABLED", False):
        result = start()
    if _env_bool("AI_AGENT_SCHEDULER_ENABLED", False):
        result = start_agent()
    from ..pg_profile.config import settings as pgprofile_settings
    if pgprofile_settings.enabled:
        result = start_pgprofile()
    return result
