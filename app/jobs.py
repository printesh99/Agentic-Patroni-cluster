"""Job registry, RBAC, and safety gating for mutating actions.

Design goals for operating against a *live* Postgres/Patroni cluster:

* **Dry-run by default.** Every action returns a plan unless explicitly confirmed.
* **Global mutation guard.** Real execution also requires the env flag
  ``PGC_ALLOW_MUTATIONS=1``. With it unset (the default) nothing can change the
  cluster, no matter what the client sends — a hard safety floor.
* **RBAC.** Execution requires a privileged role.
* **Audit.** Every action (dry-run or executed) is recorded.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any
from . import ai_config

ALLOWED_ROLES = {"platform-admin", "dba", "sre"}

JOBS: list[dict[str, Any]] = []
AUDIT: list[dict[str, Any]] = []


def mutations_enabled() -> bool:
    return os.environ.get("PGC_ALLOW_MUTATIONS", "0") == "1"


def _audit(kind: str, actor: str, dry_run: bool, executed: bool, detail: str) -> None:
    AUDIT.insert(0, {
        "id": uuid.uuid4().hex[:12],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": kind, "actor": actor,
        "dry_run": dry_run, "executed": executed, "detail": detail,
    })
    del AUDIT[500:]


def submit(kind: str, payload: dict[str, Any], *, actor: str = "dba",
           actor_roles: list[str] | None = None, dry_run: bool = True,
           plan: list[str] | None = None, executor=None) -> dict[str, Any]:
    """Create a job. Execute only when confirmed AND globally enabled AND authorised.

    ``executor`` is an optional zero-arg callable that performs the real change;
    it is invoked only when all gates pass.
    """
    actor_roles = actor_roles or []
    job = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "type": kind,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "submitted_by": actor,
        "dry_run": dry_run,
        "payload": payload,
        "plan": plan or [],
        "state": "planned",
        "status": "planned",
        "result": None,
    }

    if dry_run:
        job["state"] = job["status"] = "validated"
        job["message"] = "Dry-run: validated, not executed."
        _audit(kind, actor, True, False, "validated (dry-run)")
    elif not ai_config.action_execution_allowed():
        job["state"] = job["status"] = "blocked"
        job["message"] = "Execution blocked: agentic execution is disabled by policy."
        _audit(kind, actor, False, False, "blocked by agentic execution policy")
    elif not mutations_enabled():
        job["state"] = job["status"] = "blocked"
        job["message"] = ("Execution blocked: mutations are disabled "
                          "(set PGC_ALLOW_MUTATIONS=1 on the backend to enable).")
        _audit(kind, actor, False, False, "blocked by mutation guard")
    elif not (set(actor_roles) & ALLOWED_ROLES):
        job["state"] = job["status"] = "forbidden"
        job["message"] = f"Forbidden: role {actor_roles} lacks privilege for {kind}."
        _audit(kind, actor, False, False, "forbidden by RBAC")
    else:
        try:
            from .db.session import SessionLocal
            from .services import action_control_service, inventory_service
            with SessionLocal() as db:
                inv = inventory_service.resolve(db)
                result = action_control_service.execute(db=db, inventory_id=inv.id,
                    action_level=str(payload.get("action_level") or "L5").upper(),
                    action_type=str(payload.get("action_type") or kind),
                    action_id=int(payload["action_id"]) if payload.get("action_id") is not None else None,
                    plan_sha256=str(payload.get("plan_sha256") or "") or None, executor=executor)
            job["state"] = job["status"] = "completed"
            job["result"] = result
            job["message"] = "Executed."
            _audit(kind, actor, False, True, "executed")
        except Exception as exc:  # surface, don't crash the API
            job["state"] = job["status"] = "failed"
            job["message"] = f"Execution failed: {exc}"
            _audit(kind, actor, False, False, f"failed: {exc}")

    JOBS.insert(0, job)
    del JOBS[500:]
    return job
