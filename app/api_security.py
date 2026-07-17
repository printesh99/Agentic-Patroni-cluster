"""Security page routes (Phase 4 read + TLS rotate action stub)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from . import pg_security
from . import sources as S
from .threads import to_thread

router = APIRouter(prefix="/api/v1/clusters/{cluster_id}", dependencies=[Depends(S.cluster_path_dependency)])


@router.get("/auth")
async def auth(cluster_id: str):
    return await to_thread(pg_security.build_auth)


@router.get("/pgaudit")
async def pgaudit(cluster_id: str):
    return await to_thread(pg_security.build_pgaudit)


@router.get("/sensitive-data")
async def sensitive_data(cluster_id: str):
    return await to_thread(pg_security.build_sensitive)


@router.get("/tls")
async def tls(cluster_id: str):
    return await to_thread(pg_security.build_tls)


@router.post("/tls/rotate")
async def tls_rotate(cluster_id: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": "tls-rotate",
            "message": "Certificate rotation is guarded; submitted as a dry-run request."}
