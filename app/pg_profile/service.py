"""Server registration and idempotent multi-worker sample collection."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import time
from typing import Any, Iterator

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from .. import metrics
from ..db.models import PgProfileSampleRun, PgProfileServer
from ..db.session import SessionLocal, engine
from . import client
from .config import settings
from .security import sanitize_error

log = logging.getLogger("objectmonitor.pgprofile")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _server_api(row: PgProfileServer) -> dict[str, Any]:
    return {
        "id": row.id, "inventory_id": row.inventory_id, "server_name": row.server_name,
        "region": row.region, "dc": row.dc, "environment": row.environment,
        "namespace": row.namespace, "cluster_name": row.cluster_name,
        "database_name": row.database_name, "endpoint_configured": bool(row.endpoint_host),
        "credential_configured": bool(row.credential_reference), "endpoint_port": row.endpoint_port,
        "sslmode": row.sslmode, "enabled": row.enabled,
        "registration_status": row.registration_status,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "last_sample_at": row.last_sample_at.isoformat() if row.last_sample_at else None,
        "last_successful_sample_id": row.last_successful_sample_id,
        "last_error": row.last_error, "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _run_api(row: PgProfileSampleRun) -> dict[str, Any]:
    return {
        "id": row.id, "pgprofile_server_id": row.pgprofile_server_id,
        "trigger_type": row.trigger_type, "triggered_by": row.triggered_by,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "status": row.status, "sample_id": row.sample_id,
        "sample_time": row.sample_time.isoformat() if row.sample_time else None,
        "duration_ms": row.duration_ms, "incident_id": row.incident_id,
        "error_code": row.error_code, "sanitized_error_message": row.sanitized_error_message,
        "evidence": row.evidence,
    }


def _allowed(row: PgProfileServer) -> bool:
    return not settings.allowed_environments or (row.environment or "").lower() in settings.allowed_environments


def get_server(server_id: int, *, include_private: bool = False) -> PgProfileServer | dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.get(PgProfileServer, server_id)
        if row is None or not _allowed(row):
            return None
        if include_private:
            db.expunge(row)
            return row
        return _server_api(row)


def list_servers(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    with SessionLocal() as db:
        stmt = select(PgProfileServer).order_by(PgProfileServer.server_name)
        if settings.allowed_environments:
            stmt = stmt.where(func.lower(PgProfileServer.environment).in_(settings.allowed_environments))
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
        return {"items": [_server_api(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def create_server(payload: Any, actor: str) -> dict[str, Any]:
    if not settings.enabled:
        return {"available": False, "status": "UNAVAILABLE", "reason": "PGPROFILE_ENABLED is false"}
    if settings.allowed_environments and payload.environment.lower() not in settings.allowed_environments:
        raise ValueError("environment is not authorized for pg_profile")
    if settings.require_ssl and payload.sslmode not in settings.allowed_sslmodes:
        raise ValueError("sslmode is not permitted")
    with SessionLocal() as db:
        row = PgProfileServer(**payload.model_dump())
        db.add(row)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ValueError("server_name is already registered") from exc
        db.refresh(row)
        server_id = row.id
    row = get_server(server_id, include_private=True)
    assert isinstance(row, PgProfileServer)
    try:
        result = client.register_server(row)
        status, error = "REGISTERED", None
    except Exception as exc:
        result = {"ok": False, "registered": False}
        status, error = "FAILED", sanitize_error(exc)
    with SessionLocal() as db:
        stored = db.get(PgProfileServer, server_id)
        stored.registration_status = status
        stored.last_error = error
        stored.updated_at = _utcnow()
        db.commit()
        db.refresh(stored)
        return {"available": True, "server": _server_api(stored), "registration": result,
                "actor": actor, "status": status}


def _lock_key(server_id: int) -> int:
    raw = hashlib.sha256(f"pgprofile-server:{server_id}".encode()).digest()[:8]
    value = int.from_bytes(raw, "big", signed=False)
    return value - 2**64 if value >= 2**63 else value


@contextmanager
def advisory_lock(server_id: int) -> Iterator[bool]:
    if engine.dialect.name != "postgresql":
        yield False
        return
    conn = engine.connect()
    acquired = False
    try:
        acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _lock_key(server_id)}).scalar())
        yield acquired
    finally:
        if acquired:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _lock_key(server_id)})
                conn.commit()
            except Exception:
                pass
        conn.close()


def _bucket_key(server_id: int, trigger: str, incident_id: int | None = None) -> str:
    now = int(time.time())
    seconds = settings.sample_interval_minutes * 60
    bucket = now - now % seconds
    suffix = f":incident:{incident_id}" if incident_id else ""
    return f"pgprofile:{server_id}:{trigger}:{bucket}{suffix}"


def recover_stale_runs() -> int:
    cutoff = _utcnow() - timedelta(seconds=settings.sample_timeout_seconds * 2)
    with SessionLocal() as db:
        rows = db.execute(select(PgProfileSampleRun).where(
            PgProfileSampleRun.status == "RUNNING", PgProfileSampleRun.started_at < cutoff
        )).scalars().all()
        for row in rows:
            row.status = "FAILED"
            row.finished_at = _utcnow()
            row.error_code = "STALE_RUN"
            row.sanitized_error_message = "Recovered stale RUNNING sample job"
        db.commit()
        return len(rows)


def collect_sample(server_id: int, trigger_type: str = "SCHEDULED", triggered_by: str = "scheduler",
                   incident_id: int | None = None, idempotency_key: str | None = None,
                   retries: int = 2) -> dict[str, Any]:
    started_mono = time.monotonic()
    if not settings.enabled:
        return {"available": False, "status": "UNAVAILABLE", "reason": "PGPROFILE_ENABLED is false"}
    server = get_server(server_id, include_private=True)
    if not isinstance(server, PgProfileServer):
        raise ValueError("pg_profile server not found or unauthorized")
    if not server.enabled:
        return {"available": True, "status": "SKIPPED", "reason": "server disabled"}
    if engine.dialect.name != "postgresql":
        return {"available": False, "status": "UNSUPPORTED", "reason": "pg_profile collection requires PostgreSQL metadata storage"}
    key = idempotency_key or _bucket_key(server_id, trigger_type, incident_id)
    with SessionLocal() as db:
        existing = db.execute(select(PgProfileSampleRun).where(PgProfileSampleRun.idempotency_key == key)).scalar_one_or_none()
        if existing:
            return {"available": True, "status": existing.status, "idempotent": True, "run": _run_api(existing)}
        run = PgProfileSampleRun(pgprofile_server_id=server_id, trigger_type=trigger_type,
                                 triggered_by=triggered_by[:255], status="RUNNING",
                                 incident_id=incident_id, idempotency_key=key,
                                 evidence={"source": "pg_profile", "operation": "take_sample"})
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.execute(select(PgProfileSampleRun).where(PgProfileSampleRun.idempotency_key == key)).scalar_one()
            return {"available": True, "status": existing.status, "idempotent": True, "run": _run_api(existing)}
        db.refresh(run)
        run_id = run.id

    with advisory_lock(server_id) as acquired:
        if not acquired:
            metrics.PGPROFILE_LOCK_CONTENTION.inc()
            with SessionLocal() as db:
                run = db.get(PgProfileSampleRun, run_id)
                run.status, run.finished_at = "SKIPPED", _utcnow()
                run.error_code, run.sanitized_error_message = "LOCK_BUSY", "Another collector owns the server advisory lock"
                db.commit(); db.refresh(run)
                return {"available": True, "status": "SKIPPED", "run": _run_api(run)}
        result = None
        for attempt in range(max(0, min(retries, 4)) + 1):
            result = client.take_sample(server.server_name, skip_sizes=True)
            if result.ok or attempt >= retries:
                break
            time.sleep(min(2 ** attempt, 8))
        assert result is not None

    now = _utcnow()
    with SessionLocal() as db:
        run = db.get(PgProfileSampleRun, run_id)
        stored = db.get(PgProfileServer, server_id)
        run.status, run.finished_at = result.status, now
        run.sample_id, run.sample_time, run.duration_ms = result.sample_id, result.sample_time, result.duration_ms
        run.error_code, run.sanitized_error_message = result.error_code, result.error
        run.evidence = {"source": "pg_profile", "operation": "take_sample", "sample_id": result.sample_id,
                        "attempts": attempt + 1, "duration_ms": result.duration_ms}
        stored.last_sample_at = now
        if result.ok:
            stored.last_successful_sample_id, stored.last_error = result.sample_id, None
            stored.registration_status, stored.last_verified_at = "VERIFIED", now
        else:
            stored.last_error, stored.registration_status = result.error, "FAILED"
        db.commit(); db.refresh(run)
        api = _run_api(run)

    metrics.PGPROFILE_SAMPLE_RUNS.labels(server=server.server_name, status=result.status, trigger=trigger_type).inc()
    metrics.PGPROFILE_SAMPLE_DURATION.labels(server=server.server_name).observe(result.duration_ms / 1000.0)
    if result.ok:
        metrics.PGPROFILE_LAST_SUCCESS.labels(server=server.server_name).set(time.time())
    else:
        metrics.PGPROFILE_COLLECTION_FAILURES.labels(server=server.server_name).inc()
    log.info("pg_profile sample run_id=%s server_id=%s cluster_id=%s operation=sample status=%s duration_ms=%s",
             run_id, server_id, server.cluster_name, result.status, int((time.monotonic() - started_mono) * 1000))
    return {"available": True, "status": result.status, "idempotent": False, "run": api}


def verify_server(server_id: int, actor: str) -> dict[str, Any]:
    # pg_profile has no documented non-collecting connection-test API. A small,
    # explicit sample is the supported end-to-end verification mechanism.
    server = get_server(server_id, include_private=True)
    if not isinstance(server, PgProfileServer):
        raise ValueError("pg_profile server not found or unauthorized")
    try:
        registered = {str(row.get("server_name") or row.get("server")) for row in client.list_registered_servers()}
        if server.server_name not in registered:
            client.register_server(server)
    except Exception as exc:
        with SessionLocal() as db:
            stored = db.get(PgProfileServer, server_id)
            stored.registration_status = "FAILED"
            stored.last_error = sanitize_error(exc)
            db.commit()
        return {"available": False, "status": "UNAVAILABLE", "reason": sanitize_error(exc)}
    return collect_sample(server_id, trigger_type="MANUAL", triggered_by=actor,
                          idempotency_key=f"verify:{server_id}:{int(time.time() // 60)}", retries=0)


def list_runs(limit: int = 100, offset: int = 0, server_id: int | None = None) -> dict[str, Any]:
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    with SessionLocal() as db:
        stmt = select(PgProfileSampleRun).join(
            PgProfileServer, PgProfileServer.id == PgProfileSampleRun.pgprofile_server_id
        ).order_by(PgProfileSampleRun.id.desc())
        if settings.allowed_environments:
            stmt = stmt.where(func.lower(PgProfileServer.environment).in_(settings.allowed_environments))
        if server_id:
            stmt = stmt.where(PgProfileSampleRun.pgprofile_server_id == server_id)
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
        return {"items": [_run_api(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def run_status_counts() -> dict[str, int]:
    with SessionLocal() as db:
        stmt = select(PgProfileSampleRun.status, func.count()).join(
            PgProfileServer, PgProfileServer.id == PgProfileSampleRun.pgprofile_server_id
        )
        if settings.allowed_environments:
            stmt = stmt.where(func.lower(PgProfileServer.environment).in_(settings.allowed_environments))
        return {str(status): int(count) for status, count in db.execute(
            stmt.group_by(PgProfileSampleRun.status)).all()}


def scheduled_collect_all() -> dict[str, Any]:
    recover_stale_runs()
    rows = list_servers(limit=200)["items"]
    results = []
    for row in rows:
        if row["enabled"]:
            try:
                results.append(collect_sample(row["id"], "SCHEDULED", "scheduler"))
            except Exception as exc:
                results.append({"available": False, "server_id": row["id"], "status": "FAILED",
                                "error": sanitize_error(exc)})
    statuses = {r.get("status") for r in results}
    return {"available": True, "status": "PARTIAL" if "FAILED" in statuses else "SUCCEEDED", "results": results}
