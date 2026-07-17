"""Performance page routes (Phase 4)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from . import pg_perf
from . import sources as S
from .threads import to_thread

router = APIRouter(prefix="/api/v1/clusters/{cluster_id}/perf", dependencies=[Depends(S.cluster_path_dependency)])

def _fresh(payload: dict) -> dict:
    payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    return payload


@router.get("/sessions")
async def sessions(cluster_id: str, state: str | None = None, db: str | None = None,
                   search: str | None = None, limit: int = 500):
    return _fresh(await to_thread(pg_perf.sessions, state, db, search, limit))


@router.get("/session/{pid}/insight")
async def session_insight(cluster_id: str, pid: int):
    return _fresh(await to_thread(pg_perf.session_insight, pid))


@router.get("/locks")
async def locks(cluster_id: str):
    return _fresh(await to_thread(pg_perf.lock_tree))


@router.get("/waits")
async def waits(cluster_id: str):
    return _fresh(await to_thread(pg_perf.waits))


@router.get("/slow")
async def slow(cluster_id: str, min_seconds: float = 5, limit: int = 100):
    return _fresh(await to_thread(pg_perf.slow, min_seconds, limit))


@router.get("/vacuum")
async def vacuum(cluster_id: str, database: str | None = None, limit: int = 75):
    return _fresh(await to_thread(pg_perf.vacuum, database, limit))


@router.get("/bloat")
async def bloat(cluster_id: str, database: str | None = None, limit: int = 75):
    return _fresh(await to_thread(pg_perf.bloat, database, limit))


@router.get("/topsql")
async def topsql(cluster_id: str, sort: str = "total", db: str | None = None, limit: int = 75):
    return _fresh(await to_thread(pg_perf.topsql, sort, db, limit))


@router.get("/topsql/history")
async def topsql_history(cluster_id: str, range: str = "24h", limit: int = 8):
    # No persisted statement history store yet; return an explicit empty set so
    # the card renders "no history" instead of erroring.
    return _fresh({"available": False, "history_available": False, "source": "pg_stat_statements", "series": [], "rows": [], "reason": "history store not configured"})


@router.post("/topsql/capture")
async def topsql_capture(cluster_id: str):
    # A capture would snapshot pg_stat_statements into a history table; not yet
    # persisted. Acknowledge so the UI button completes.
    return {"ok": True, "captured": False, "reason": "history store not configured"}


@router.get("/index-advisor")
async def index_advisor(cluster_id: str, database: str | None = None, limit: int = 75):
    return _fresh(await to_thread(pg_perf.index_advisor, database, limit))


@router.get("/application-activity")
async def application_activity(cluster_id: str, database: str | None = None, limit: int = 100):
    return _fresh(await to_thread(pg_perf.application_activity, database, limit))
