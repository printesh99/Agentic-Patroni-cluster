"""PostgreSQL Enterprise Console — live backend.

Serves the static UI (the repo root) and exposes the ``/api/v1`` contract the
UI expects, backed by the live kind cluster (kubectl + psql-exec + Prometheus).

Run via ``server/run.sh`` which also manages the Prometheus port-forward.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

from . import sources as S
from . import api_meta
from . import api_clusters
from . import api_perf
from . import api_backups
from . import api_security
from . import api_replication
from . import api_admin
from . import api_metrics
from . import api_ops
from . import api_actions
from . import api_logs
from . import api_log_analytics
from . import api_health_check
from . import api_rules
from . import api_ml
from . import api_forecast
from . import api_ai_incidents
from . import api_scheduler
from . import api_objects
from . import api_ai_actions
from . import api_ai_agent
from . import api_ai_v1
from . import api_compat
from . import api_recommendations
from . import api_openshift
from . import api_v1_screens
from . import api_charts
from . import api_pg_profile
from . import loki as L
from . import ai_config
from .db import bootstrap as bootstrap_metadata
from .db import status as metadata_status
from .threads import to_thread
from .services import inventory_service

UI_ROOT = Path(__file__).resolve().parent.parent / "static"  # Docker layout: /app/static/

app = FastAPI(title="PostgreSQL Enterprise Console API", version="0.1.0")

app.include_router(api_meta.router)
app.include_router(api_clusters.router)
app.include_router(api_perf.router)
app.include_router(api_backups.router)
app.include_router(api_security.router)
app.include_router(api_replication.router)
app.include_router(api_admin.router)
app.include_router(api_metrics.router)
app.include_router(api_ops.router)
app.include_router(api_actions.router)
app.include_router(api_logs.router)
app.include_router(api_logs.ws_router)
app.include_router(api_log_analytics.router)
app.include_router(api_log_analytics.summary_router)
app.include_router(api_health_check.router)
app.include_router(api_rules.router)
app.include_router(api_ml.router)
app.include_router(api_forecast.router)
app.include_router(api_ai_incidents.router)
app.include_router(api_scheduler.router)
app.include_router(api_objects.router)
app.include_router(api_ai_actions.router)
app.include_router(api_ai_agent.router)
app.include_router(api_ai_v1.router)
app.include_router(api_compat.router)
app.include_router(api_recommendations.router)
app.include_router(api_openshift.router)
app.include_router(api_v1_screens.router)
app.include_router(api_charts.router)
app.include_router(api_pg_profile.router)


@app.on_event("startup")
async def startup():
    await to_thread(bootstrap_metadata)
    from .services import scheduler_service
    await to_thread(scheduler_service.maybe_start_from_env)


@app.on_event("startup")
async def _pgprofile_startup():
    # Background ASH sampler (gated by ASH_SAMPLER_ENABLED, default on) feeding the
    # live SQL Insight screens. pg_profile central collection stays off unless
    # PGPROFILE_ENABLED=true (see app/pg_profile/config.py). Sync, non-blocking.
    from . import pg_ash
    pg_ash.start_background()


@app.exception_handler(S.SourceError)
async def _source_error(_request: Request, exc: S.SourceError):
    return JSONResponse(status_code=502, content={"error": str(exc), "source": "upstream"})


@app.exception_handler(S.UnknownClusterError)
async def _unknown_cluster(_request: Request, exc: S.UnknownClusterError):
    return JSONResponse(status_code=404, content={"error": str(exc), "code": "unknown_cluster"})


@app.exception_handler(S.DisabledClusterError)
async def _disabled_cluster(_request: Request, exc: S.DisabledClusterError):
    return JSONResponse(status_code=403, content={"error": str(exc), "code": "disabled_cluster"})


@app.exception_handler(S.IncompleteClusterConfigError)
async def _incomplete_cluster(_request: Request, exc: S.IncompleteClusterConfigError):
    return JSONResponse(status_code=503, content={"error": str(exc), "code": "incomplete_cluster_config"})


@app.exception_handler(inventory_service.InventoryResolutionError)
async def _inventory_resolution(_request: Request, exc: inventory_service.InventoryResolutionError):
    return JSONResponse(status_code=409, content={"error": str(exc), "code": "inventory_resolution_failed"})


# --------------------------------------------------------------------------
# API: health (Phase 1 — validates live connectivity end to end)
# --------------------------------------------------------------------------
@app.get("/api/v1/health")
@app.get("/api/health")  # backward-compat alias for existing deploy/probe scripts
async def health():
    out: dict[str, object] = {
        "cluster_id": S.CLUSTER_ID,
        "namespace": S.NS,
        "ai_ml": metadata_status(),
    }
    try:
        pods = await to_thread(S.pods)
        out["pods"] = pods
        out["primary"] = next((p["name"] for p in pods if p["role"] == "master"), None)
        out["k8s"] = "ok"
    except S.SourceError as exc:
        out["k8s"] = f"error: {exc}"
    try:
        out["prometheus"] = "ok" if await to_thread(S.prom_up) else "down"
    except S.SourceError as exc:
        out["prometheus"] = f"error: {exc}"
    try:
        row = await to_thread(S.sql_one, "select current_setting('server_version')")
        out["postgres"] = {"server_version": row[0] if row else None, "sql": "ok"}
    except S.SourceError as exc:
        out["postgres"] = {"sql": f"error: {exc}"}
    try:
        out["loki"] = "ok" if await to_thread(L.up) else "down"
    except S.SourceError as exc:
        out["loki"] = f"error: {exc}"
    return out


@app.get("/livez")
@app.get("/healthz")
@app.get("/api/v1/livez")
async def livez():
    """Lightweight liveness/readiness probe — process-only, NO upstream I/O
    (no kubectl / DB / Loki / Prometheus). k8s liveness & readiness probes MUST
    point here; ``/api/health`` does full end-to-end connectivity checks and would
    NotReady the pod (then 503 every route) if a probe timed out on a slow upstream."""
    return {"status": "ok"}


@app.get("/metrics")
@app.get("/api/v1/metrics")  # backward-compat alias for existing deploy/probe scripts
async def prometheus_metrics():
    from fastapi.responses import Response
    from . import metrics as _metrics
    body, content_type = await to_thread(_metrics.render)
    return Response(content=body, media_type=content_type)


@app.get("/api/v1/ai/config")
async def ai_ml_config():
    try:
        validated = await to_thread(ai_config.validate)
        return {"available": True, **validated, "metadata": metadata_status()}
    except ai_config.ConfigError as exc:
        return JSONResponse(status_code=500, content={"available": False, "error": str(exc)})


# --------------------------------------------------------------------------
# Static UI — must be mounted last so /api/* wins. The UI uses absolute asset
# paths (/vendor, /dist, /styles.css, /assets) and client-side routing, so we
# serve files directly and fall back to index.html for unknown non-API paths.
# --------------------------------------------------------------------------
_STATIC_DIRS = {"vendor", "dist", "assets", "static"}


@app.get("/")
async def root_index():
    return FileResponse(UI_ROOT / "index.html")


@app.get("/{full_path:path}")
async def spa(full_path: str):
    # Unimplemented API routes must never fall through to the HTML shell, or
    # JSON fetches would receive index.html and fail to parse.
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404,
                            content={"error": "not implemented", "path": "/" + full_path})
    candidate = (UI_ROOT / full_path).resolve()
    # prevent path traversal outside the UI root
    if UI_ROOT in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    # asset dirs that don't exist -> 404 (don't masquerade as the app)
    first = full_path.split("/", 1)[0]
    if first in _STATIC_DIRS:
        return JSONResponse(status_code=404, content={"error": "not found"})
    # otherwise it's a client-side route -> serve the app shell
    return FileResponse(UI_ROOT / "index.html")
