"""Backups page routes (Phase 4 read + Phase 5 action stubs)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from . import pg_backups
from . import sources as S
from .threads import to_thread

router = APIRouter(prefix="/api/v1/clusters/{cluster_id}", dependencies=[Depends(S.cluster_path_dependency)])


@router.get("/backups")
async def backups(cluster_id: str):
    return await to_thread(pg_backups.build_backups)


@router.get("/backups/schedules")
async def schedules(cluster_id: str):
    return await to_thread(pg_backups.build_schedules)


@router.get("/pitr/preview")
async def pitr_preview(cluster_id: str, target: str | None = None):
    return await to_thread(pg_backups.build_pitr_preview)


# --- mutating actions: dry-run only until Phase 5 wires real execution -------
@router.post("/backups/validate")
async def backups_validate(cluster_id: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": "backup-validate",
            "message": "Validation request accepted (dry-run; execution gated until approved)."}


@router.post("/clone")
async def clone(cluster_id: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": "clone",
            "message": "Clone request validated (dry-run; not executed)."}


@router.post("/pitr/restore")
async def pitr_restore(cluster_id: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": "pitr-restore",
            "message": "PITR restore is a guarded action; submitted as dry-run only."}
