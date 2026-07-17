"""Replication page routes (Phase 4 read + actions stub)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from . import pg_replication
from . import sources as S
from .threads import to_thread

router = APIRouter(prefix="/api/v1/clusters/{cluster_id}/replication", dependencies=[Depends(S.cluster_path_dependency)])


@router.get("/topology")
async def topology(cluster_id: str):
    return await to_thread(pg_replication.build_topology)


@router.get("/sync")
async def sync(cluster_id: str):
    return await to_thread(pg_replication.build_sync)


@router.get("/logical")
async def logical(cluster_id: str, database: str | None = None):
    return await to_thread(pg_replication.build_logical, database)


@router.get("/fdw")
async def fdw(cluster_id: str, database: str | None = None):
    return await to_thread(pg_replication.build_fdw, database)


@router.get("/history")
async def history(cluster_id: str, limit: int = 75):
    return await to_thread(pg_replication.build_history, limit)


@router.post("/actions/{action}")
async def actions(cluster_id: str, action: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": f"replication-{action}",
            "message": f"Replication action '{action}' is guarded; submitted as dry-run."}
