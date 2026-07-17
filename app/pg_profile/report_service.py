"""Idempotent pg_profile report generation and safe database storage."""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import logging
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import metrics
from ..db.models import IncidentPgProfileReport, PgProfileReport, PgProfileServer
from ..db.session import SessionLocal, engine
from . import client
from .config import settings
from .security import sanitize_error, sanitize_html
from .service import _allowed, advisory_lock

log = logging.getLogger("objectmonitor.pgprofile")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _api(row: PgProfileReport, include_content: bool = False) -> dict[str, Any]:
    out = {
        "id": row.id, "pgprofile_server_id": row.pgprofile_server_id,
        "start_sample_id": row.start_sample_id, "end_sample_id": row.end_sample_id,
        "period_start": row.period_start.isoformat() if row.period_start else None,
        "period_end": row.period_end.isoformat() if row.period_end else None,
        "report_type": row.report_type, "generation_status": row.generation_status,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        "incident_id": row.incident_id, "report_hash": row.report_hash,
        "html_compressed": row.html_compressed, "original_size_bytes": row.original_size_bytes,
        "stored_size_bytes": row.stored_size_bytes, "content_available": bool(row.html_content),
        "sanitized": row.sanitized, "error_message": row.error_message,
        "created_by": row.created_by, "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if include_content and row.html_content:
        raw = gzip.decompress(row.html_content) if row.html_compressed else row.html_content
        out["html"] = raw.decode("utf-8", errors="replace")
    return out


def _validate_range(start: datetime | None, end: datetime | None) -> None:
    if start and end:
        if start >= end:
            raise ValueError("period_start must be before period_end")
        if (end - start).total_seconds() > settings.max_report_range_hours * 3600:
            raise ValueError("report range exceeds PGPROFILE_MAX_REPORT_RANGE_HOURS")


def prepare_html(html: str) -> dict[str, Any]:
    raw = html.encode("utf-8")
    original = len(raw)
    if original > settings.max_html_bytes:
        return {"status": "TOO_LARGE", "content": None, "compressed": False, "digest": None,
                "original": original, "stored": 0,
                "error": "Report exceeds PGPROFILE_MAX_HTML_BYTES"}
    safe = sanitize_html(html).encode("utf-8")
    if len(safe) > settings.max_html_bytes:
        return {"status": "TOO_LARGE", "content": None, "compressed": False, "digest": None,
                "original": original, "stored": 0,
                "error": "Sanitized report exceeds PGPROFILE_MAX_HTML_BYTES"}
    digest = hashlib.sha256(safe).hexdigest()
    content = gzip.compress(safe, compresslevel=6) if settings.compress_html else safe
    stored = len(content)
    if not settings.store_html or settings.report_storage == "metadata-only":
        content, stored = None, 0
    return {"status": "SUCCEEDED", "content": content, "compressed": settings.compress_html,
            "digest": digest, "original": original, "stored": stored, "error": None,
            "sanitized_size": len(safe)}


def generate(payload: Any, actor: str) -> dict[str, Any]:
    if not settings.enabled:
        return {"available": False, "status": "UNAVAILABLE", "reason": "PGPROFILE_ENABLED is false"}
    if engine.dialect.name != "postgresql":
        return {"available": False, "status": "UNSUPPORTED", "reason": "pg_profile reports require PostgreSQL metadata storage"}
    _validate_range(payload.period_start, payload.period_end)
    with SessionLocal() as db:
        server = db.get(PgProfileServer, payload.pgprofile_server_id)
        if not server or not _allowed(server):
            raise ValueError("pg_profile server not found")
        start_id, end_id = payload.start_sample_id, payload.end_sample_id
        period_start, period_end = payload.period_start, payload.period_end
        if start_id is None or end_id is None:
            if not period_start or not period_end:
                raise ValueError("provide a sample range or a time range")
            try:
                bounds = client.find_samples_for_time_range(server.server_name, period_start, period_end)
            except Exception as exc:
                return {"available": False, "status": "UNAVAILABLE", "reason": sanitize_error(exc)}
            if not bounds:
                return {"available": True, "status": "PARTIAL_DATA", "reason": "bounding samples unavailable"}
            start_id, end_id = int(bounds[0]["sample"]), int(bounds[1]["sample"])
            period_start, period_end = bounds[0]["sample_time"], bounds[1]["sample_time"]
        if start_id >= end_id:
            raise ValueError("start_sample_id must be less than end_sample_id")
        if period_start is None or period_end is None:
            try:
                samples = client.list_samples(server.server_name, days=settings.retention_days)
            except Exception as exc:
                return {"available": False, "status": "UNAVAILABLE", "reason": sanitize_error(exc)}
            sample_times = {int(item["sample"]): item.get("sample_time") for item in samples if item.get("sample") is not None}
            period_start, period_end = sample_times.get(start_id), sample_times.get(end_id)
        existing = db.execute(select(PgProfileReport).where(
            PgProfileReport.pgprofile_server_id == server.id,
            PgProfileReport.start_sample_id == start_id,
            PgProfileReport.end_sample_id == end_id,
            PgProfileReport.report_type == payload.report_type,
        )).scalar_one_or_none()
        if existing:
            return {"available": True, "status": existing.generation_status, "idempotent": True,
                    "report": _api(existing)}
        row = PgProfileReport(pgprofile_server_id=server.id, start_sample_id=start_id, end_sample_id=end_id,
                              period_start=period_start, period_end=period_end, report_type=payload.report_type,
                              generation_status="RUNNING", incident_id=payload.incident_id, created_by=actor[:255])
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.execute(select(PgProfileReport).where(
                PgProfileReport.pgprofile_server_id == server.id,
                PgProfileReport.start_sample_id == start_id, PgProfileReport.end_sample_id == end_id,
                PgProfileReport.report_type == payload.report_type)).scalar_one()
            return {"available": True, "status": existing.generation_status, "idempotent": True,
                    "report": _api(existing)}
        db.refresh(row)
        report_id, server_name = row.id, server.server_name

    started = time.monotonic()
    with advisory_lock(payload.pgprofile_server_id) as acquired:
        if not acquired:
            metrics.PGPROFILE_LOCK_CONTENTION.inc()
            with SessionLocal() as db:
                row = db.get(PgProfileReport, report_id)
                row.generation_status, row.error_message = "SKIPPED", "Another pg_profile operation owns the server lock"
                db.commit(); db.refresh(row)
                return {"available": True, "status": "SKIPPED", "report": _api(row)}
        if payload.report_type == "DIFF":
            if not payload.compare_start_sample_id or not payload.compare_end_sample_id:
                result = client.ReportResult(False, None, 0, "DIFF", "INVALID_RANGE", "comparison sample range required")
            else:
                result = client.generate_diff_report(server_name, start_id, end_id,
                                                     payload.compare_start_sample_id, payload.compare_end_sample_id)
        else:
            result = client.generate_regular_report(server_name, start_id, end_id)

    status, content, compressed, digest, original, stored = "FAILED", None, False, None, None, None
    error = result.error
    if result.ok and result.html is not None:
        prepared = prepare_html(result.html)
        status, content, compressed = prepared["status"], prepared["content"], prepared["compressed"]
        digest, original, stored, error = prepared["digest"], prepared["original"], prepared["stored"], prepared["error"]
        if status == "SUCCEEDED":
            metrics.PGPROFILE_REPORT_SIZE.observe(prepared["sanitized_size"])

    with SessionLocal() as db:
        row = db.get(PgProfileReport, report_id)
        row.generation_status, row.generated_at = status, _utcnow()
        row.report_hash, row.html_content, row.html_compressed = digest, content, compressed
        row.original_size_bytes, row.stored_size_bytes = original, stored
        row.sanitized, row.error_message = status == "SUCCEEDED", sanitize_error(error) if error else None
        if payload.incident_id and status == "SUCCEEDED":
            link = db.execute(select(IncidentPgProfileReport).where(
                IncidentPgProfileReport.incident_id == payload.incident_id,
                IncidentPgProfileReport.report_id == report_id)).scalar_one_or_none()
            if not link:
                db.add(IncidentPgProfileReport(incident_id=payload.incident_id, report_id=report_id))
                metrics.PGPROFILE_INCIDENT_LINKS.inc()
        db.commit(); db.refresh(row)
        api = _api(row)
    metrics.PGPROFILE_REPORT_RUNS.labels(status=status, type=payload.report_type).inc()
    metrics.PGPROFILE_REPORT_DURATION.labels(type=payload.report_type).observe(result.duration_ms / 1000.0)
    log.info("pg_profile report report_id=%s server_id=%s operation=report status=%s duration_ms=%s",
             report_id, payload.pgprofile_server_id, status, int((time.monotonic() - started) * 1000))
    if status == "SUCCEEDED" and settings.feature_extraction_enabled:
        try:
            from .feature_extractor import extract_and_store
            extract_and_store(report_id)
        except Exception as exc:
            log.warning("pg_profile feature extraction report_id=%s status=failed error=%s", report_id, sanitize_error(exc))
    return {"available": True, "status": status, "idempotent": False, "report": api}


def list_reports(limit: int = 100, offset: int = 0, server_id: int | None = None,
                 incident_id: int | None = None) -> dict[str, Any]:
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    with SessionLocal() as db:
        stmt = select(PgProfileReport).join(
            PgProfileServer, PgProfileServer.id == PgProfileReport.pgprofile_server_id
        ).order_by(PgProfileReport.id.desc())
        if settings.allowed_environments:
            stmt = stmt.where(func.lower(PgProfileServer.environment).in_(settings.allowed_environments))
        if server_id:
            stmt = stmt.where(PgProfileReport.pgprofile_server_id == server_id)
        if incident_id:
            stmt = stmt.where(PgProfileReport.incident_id == incident_id)
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
        return {"items": [_api(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def get_report(report_id: int, include_content: bool = False) -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.get(PgProfileReport, report_id)
        server = db.get(PgProfileServer, row.pgprofile_server_id) if row else None
        return _api(row, include_content=include_content) if row and server and _allowed(server) else None
