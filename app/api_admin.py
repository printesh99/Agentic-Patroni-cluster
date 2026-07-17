"""Admin + Config page routes (Phase 4 read + Phase 5 validate/action stubs)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from . import pg_admin, pg_config
from . import sources as S
from .threads import to_thread

router = APIRouter(prefix="/api/v1/clusters/{cluster_id}", dependencies=[Depends(S.cluster_path_dependency)])


# --- Admin / DBA -----------------------------------------------------------
@router.get("/databases")
async def databases(cluster_id: str, include_objects: bool = Query(False)):
    return await to_thread(pg_admin.build_databases, include_objects)


@router.get("/roles")
async def roles(cluster_id: str):
    return await to_thread(pg_admin.build_roles)


@router.get("/privileges")
async def privileges(cluster_id: str, database: str = Query("postgres"),
                     role: str | None = Query(None), schema: str | None = Query(None)):
    return await to_thread(pg_admin.build_privileges, database, role, schema)


@router.get("/hba")
async def hba(cluster_id: str):
    return await to_thread(pg_admin.build_hba)


@router.get("/pods")
async def pods(cluster_id: str):
    return await to_thread(pg_admin.build_pods)


@router.post("/roles/create/validate")
async def roles_create_validate(cluster_id: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": "role-create",
            "message": "Role creation validated as dry-run (not executed)."}


# --- Configuration ---------------------------------------------------------
@router.get("/config/parameters")
async def config_parameters(cluster_id: str):
    return await to_thread(pg_config.build_parameters)


@router.get("/config/patroni")
async def config_patroni(cluster_id: str):
    return await to_thread(pg_config.build_patroni)


@router.get("/config/maintenance")
async def config_maintenance(cluster_id: str):
    return await to_thread(pg_config.build_maintenance)


@router.get("/config/database-settings")
async def config_db_settings(cluster_id: str):
    return await to_thread(pg_config.build_database_settings)


@router.get("/config/role-settings")
async def config_role_settings(cluster_id: str):
    return await to_thread(pg_config.build_role_settings)


# --- config validate actions (dry-run until Phase 5) -----------------------
@router.post("/config/parameters/validate")
async def config_params_validate(cluster_id: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": "config-parameters",
            "message": "Parameter change validated as a dry-run job."}


@router.post("/config/patroni/validate")
async def config_patroni_validate(cluster_id: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": "config-patroni",
            "message": "Patroni DCS change validated as a dry-run job."}


@router.post("/config/maintenance/validate")
async def config_maint_validate(cluster_id: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": "config-maintenance",
            "message": "Pause/resume validated as a guarded dry-run job."}


@router.post("/config/database-settings/validate")
async def config_db_validate(cluster_id: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": "config-database-settings",
            "message": "Database GUC change validated as a dry-run job."}


@router.post("/config/role-settings/validate")
async def config_role_validate(cluster_id: str, payload: dict = Body(default={})):
    return {"ok": True, "dry_run": True, "kind": "config-role-settings",
            "message": "Role GUC change validated as a dry-run job."}
