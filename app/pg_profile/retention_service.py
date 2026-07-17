"""Retention for application metadata and supported pg_profile settings."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select

from .. import metrics
from ..db.models import IncidentPgProfileReport, PgProfileFeature, PgProfileReport, PgProfileServer
from ..db.session import SessionLocal
from . import client
from .config import settings
from .service import advisory_lock
from .security import sanitize_error


def run(dry_run: bool = True, server_id: int | None = None) -> dict[str, Any]:
    if not settings.enabled:
        return {"available": False, "status": "UNAVAILABLE", "reason": "PGPROFILE_ENABLED is false",
                "dry_run": dry_run, "results": []}
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    with SessionLocal() as db:
        stmt = select(PgProfileServer)
        if server_id:
            stmt = stmt.where(PgProfileServer.id == server_id)
        if settings.allowed_environments:
            stmt = stmt.where(func.lower(PgProfileServer.environment).in_(settings.allowed_environments))
        servers = db.execute(stmt).scalars().all()
    results: list[dict[str, Any]] = []
    for server in servers:
        with advisory_lock(server.id) as acquired:
            if not acquired:
                metrics.PGPROFILE_LOCK_CONTENTION.inc()
                results.append({"server_id": server.id, "status": "SKIPPED", "reason": "lock busy"})
                continue
            with SessionLocal() as db:
                protected = select(IncidentPgProfileReport.report_id).join(PgProfileReport).where(
                    PgProfileReport.generation_status == "SUCCEEDED")
                old_reports = db.execute(select(PgProfileReport.id).where(
                    PgProfileReport.pgprofile_server_id == server.id,
                    PgProfileReport.created_at < cutoff,
                    PgProfileReport.id.not_in(protected))).scalars().all()
                old_features = db.execute(select(PgProfileFeature.id).where(
                    PgProfileFeature.pgprofile_server_id == server.id,
                    PgProfileFeature.created_at < cutoff,
                    PgProfileFeature.incident_id.is_(None))).scalars().all()
                if not dry_run:
                    if old_features:
                        db.execute(delete(PgProfileFeature).where(PgProfileFeature.id.in_(old_features)))
                    if old_reports:
                        db.execute(delete(PgProfileReport).where(PgProfileReport.id.in_(old_reports)))
                    db.commit()
                try:
                    ext = client.apply_retention(server.server_name, settings.retention_days, dry_run=dry_run)
                    status = "PREVIEW" if dry_run else "SUCCEEDED"
                except Exception as exc:
                    ext = {"ok": False, "error": sanitize_error(exc)}
                    status = "PARTIAL_DATA"
                results.append({"server_id": server.id, "status": status, "reports": len(old_reports),
                                "features": len(old_features), "extension_retention": ext})
    overall = "PARTIAL_DATA" if any(r["status"] in {"PARTIAL_DATA", "SKIPPED"} for r in results) else ("PREVIEW" if dry_run else "SUCCEEDED")
    return {"available": True, "status": overall, "dry_run": dry_run, "retention_days": settings.retention_days,
            "cutoff": cutoff.isoformat(), "results": results}
