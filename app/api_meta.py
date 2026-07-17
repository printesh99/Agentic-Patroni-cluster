"""Auth/profile and cluster-list endpoints (Phase 2)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from . import cluster_model
from . import sources as S
from .threads import to_thread

router = APIRouter(prefix="/api/v1", dependencies=[Depends(S.cluster_path_dependency)])

# In-memory profile (local single-operator console). Persisted only for the
# process lifetime; the UI just needs a stable shape to render the shell.
_PROFILE: dict = {
    "id": "local-dba",
    "username": "dba",
    "name": "Local DBA",
    "email": "dba@local",
    "role": "platform-admin",
    "roles": ["platform-admin"],
    "tenant": "uae",
    "settings": {
        "timezone": "Asia/Dubai",
        "density": "comfortable",
        "default_view": "overview",
        "auto_refresh_seconds": 30,
        "notifications": True,
    },
}


@router.get("/me")
async def me():
    return {"user": _PROFILE}


@router.patch("/me")
async def update_me(payload: dict = Body(default={})):
    settings = payload.get("settings") or {}
    _PROFILE["settings"].update({k: v for k, v in settings.items() if v is not None})
    return {"user": _PROFILE}


@router.post("/auth/login")
async def login(payload: dict = Body(default={})):
    # Local console: accept and return the profile + a token-shaped string.
    return {"user": _PROFILE, "token": "local-session", "authenticated": True}


@router.post("/auth/logout")
async def logout():
    return {"ok": True}


@router.get("/clusters")
async def clusters():
    summary = await to_thread(cluster_model.build_summary)
    return {"clusters": [summary], "default": summary["id"]}


@router.get("/clusters/{cluster_id}")
async def cluster_detail(cluster_id: str):
    summary = await to_thread(cluster_model.build_summary)
    return summary
