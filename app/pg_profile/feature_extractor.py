"""Structured pg_profile repository extraction and robust query baselines."""
from __future__ import annotations

from datetime import datetime
import hashlib
import math
import re
import statistics
from typing import Any

from sqlalchemy import delete, func, select, text

from .. import metrics
from ..db.models import PgProfileFeature, PgProfileReport, PgProfileServer, QueryPerformanceBaseline
from ..db.session import SessionLocal, engine
from ..ml import isolation_forest
from .config import settings

EXTRACTION_VERSION = "pgprofile-v4-adapter-1"
BASELINE_VERSION = "robust-mad-v1"

_DB_KEYS = {
    "xact_commit": "commits", "xact_rollback": "rollbacks", "blks_read": "shared_blocks_read",
    "blks_hit": "shared_blocks_hit", "blk_read_time": "block_read_ms", "blk_write_time": "block_write_ms",
    "tup_returned": "rows_returned", "tup_fetched": "rows_fetched", "tup_inserted": "rows_inserted",
    "tup_updated": "rows_updated", "tup_deleted": "rows_deleted", "temp_bytes": "temp_bytes",
    "temp_files": "temp_files", "deadlocks": "deadlocks", "wal_bytes": "wal_bytes",
    "checkpoints_timed": "checkpoints_timed", "checkpoints_req": "checkpoints_requested",
    "checkpoint_write_time": "checkpoint_write_ms", "checkpoint_sync_time": "checkpoint_sync_ms",
    "buffers_checkpoint": "checkpoint_buffers", "database_size": "database_size_bytes",
    "size_delta": "database_growth_bytes", "seq_scan": "sequential_scans", "idx_scan": "index_scans",
    "vacuum_count": "manual_vacuums", "autovacuum_count": "autovacuums",
}
_QUERY_KEYS = {
    "calls": "calls", "total_exec_time": "total_execution_ms", "mean_exec_time": "mean_execution_ms",
    "rows": "rows", "shared_blks_hit": "shared_blocks_hit", "shared_blks_read": "shared_blocks_read",
    "shared_blks_dirtied": "shared_blocks_dirtied", "shared_blks_written": "shared_blocks_written",
    "temp_blks_read": "temp_blocks_read", "temp_blks_written": "temp_blocks_written",
    "wal_records": "wal_records", "wal_bytes": "wal_bytes", "user_time": "cpu_user_seconds",
    "system_time": "cpu_system_seconds",
}


def normalize_query(query: str | None) -> tuple[str | None, str | None]:
    if not query:
        return None, None
    value = re.sub(r"'(?:''|[^'])*'", "?", query)
    value = re.sub(r"\b\d+(?:\.\d+)?\b", "?", value)
    value = re.sub(r"\s+", " ", value).strip()[:settings.query_text_max_length]
    return (value if settings.query_text_enabled else None,
            hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest())


class PgProfileV4Adapter:
    """Best-effort adapter isolated from application models.

    pg_profile internal layouts vary by version. We introspect fixed known tables
    and columns and return no rows when the installed layout is not recognized.
    """

    def _columns(self, conn, table_name: str) -> set[str]:
        rows = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=:schema AND table_name=:table"
        ), {"schema": settings.schema, "table": table_name}).all()
        return {str(r[0]) for r in rows}

    def _rows(self, table: str, server_name: str, start_id: int, end_id: int,
              required: set[str]) -> list[dict[str, Any]]:
        if engine.dialect.name != "postgresql":
            return []
        with engine.connect() as conn:
            columns = self._columns(conn, table)
            if not required.issubset(columns) or "sample_id" not in columns:
                return []
            where = "sample_id > :start_id AND sample_id <= :end_id"
            params: dict[str, Any] = {"start_id": start_id, "end_id": end_id}
            if "server_name" in columns:
                where += " AND server_name=:server_name"
                params["server_name"] = server_name
            elif "server_id" in columns and self._columns(conn, "servers") >= {"server_id", "server_name"}:
                where += f' AND server_id=(SELECT server_id FROM "{settings.schema}".servers WHERE server_name=:server_name)'
                params["server_name"] = server_name
            else:
                return []
            # Table and schema are fixed adapter constants/config-validated identifiers.
            rows = conn.execute(text(
                f'SELECT * FROM "{settings.schema}"."{table}" WHERE {where} LIMIT 5000'
            ), params).mappings().all()
            return [dict(r) for r in rows]

    def database_rows(self, server_name: str, start_id: int, end_id: int) -> list[dict[str, Any]]:
        for table in ("sample_stat_database", "sample_stat_database_total"):
            rows = self._rows(table, server_name, start_id, end_id, {"sample_id"})
            if rows:
                return rows
        return []

    def query_rows(self, server_name: str, start_id: int, end_id: int) -> list[dict[str, Any]]:
        for table in ("sample_statements", "sample_statements_total"):
            rows = self._rows(table, server_name, start_id, end_id, {"sample_id"})
            if rows:
                return rows
        return []

    def wait_rows(self, server_name: str, start_id: int, end_id: int) -> list[dict[str, Any]]:
        for table in ("sample_stat_activity", "sample_wait_sampling_total", "sample_wait_sampling"):
            rows = self._rows(table, server_name, start_id, end_id, {"sample_id"})
            if rows:
                return rows
        return []

    def relation_rows(self, server_name: str, start_id: int, end_id: int) -> list[dict[str, Any]]:
        for table in ("sample_stat_tables", "sample_stat_tables_total"):
            rows = self._rows(table, server_name, start_id, end_id, {"sample_id"})
            if rows:
                return rows
        return []


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _sum_rows(rows: list[dict[str, Any]], mapping: dict[str, str]) -> dict[str, float]:
    return {target: sum(_number(r.get(source)) for r in rows) for source, target in mapping.items()
            if any(source in r for r in rows)}


def _reset_in_period(value: Any, start: datetime | None, end: datetime | None) -> bool:
    if not value or not start or not end:
        return False
    try:
        candidate = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return start <= candidate <= end
    except (TypeError, ValueError):
        return False


def extract(report: PgProfileReport, server: PgProfileServer,
            adapter: PgProfileV4Adapter | None = None) -> list[dict[str, Any]]:
    adapter = adapter or PgProfileV4Adapter()
    db_rows = adapter.database_rows(server.server_name, report.start_sample_id, report.end_sample_id)
    query_rows = adapter.query_rows(server.server_name, report.start_sample_id, report.end_sample_id)
    wait_rows = getattr(adapter, "wait_rows", lambda *_: [])(
        server.server_name, report.start_sample_id, report.end_sample_id)
    relation_rows = getattr(adapter, "relation_rows", lambda *_: [])(
        server.server_name, report.start_sample_id, report.end_sample_id)
    out: list[dict[str, Any]] = []
    by_db: dict[str, list[dict[str, Any]]] = {}
    for row in db_rows:
        by_db.setdefault(str(row.get("datname") or row.get("database_name") or server.database_name), []).append(row)
    for database, rows in by_db.items():
        values = _sum_rows(rows, _DB_KEYS)
        commits, rollbacks = values.get("commits", 0), values.get("rollbacks", 0)
        duration = max(1.0, ((report.period_end - report.period_start).total_seconds()
                             if report.period_start and report.period_end else 1.0))
        values["tps"] = (commits + rollbacks) / duration
        hits, reads = values.get("shared_blocks_hit", 0), values.get("shared_blocks_read", 0)
        values["cache_hit_ratio"] = hits / (hits + reads) if hits + reads else None
        values["wal_bytes_per_second"] = values.get("wal_bytes", 0) / duration
        values["statistics_reset_detected"] = any(
            _reset_in_period(row.get("stats_reset"), report.period_start, report.period_end) for row in rows)
        out.append({"database_name": database, "query_id": None, "query_fingerprint": None,
                    "feature_type": "DATABASE_INTERVAL", "feature_values": values})
    totals_by_db: dict[str, float] = {}
    for row in query_rows:
        db = str(row.get("datname") or row.get("database_name") or server.database_name)
        totals_by_db[db] = totals_by_db.get(db, 0) + _number(row.get("total_exec_time"))
    for row in query_rows:
        query_id = row.get("queryid") or row.get("query_id")
        if query_id is None:
            continue
        database = str(row.get("datname") or row.get("database_name") or server.database_name)
        normalized, fingerprint = normalize_query(row.get("query") or row.get("query_text"))
        values = _sum_rows([row], _QUERY_KEYS)
        total = values.get("total_execution_ms", 0)
        values["workload_contribution_pct"] = (100.0 * total / totals_by_db.get(database, 1)) if total else 0.0
        values["rank_by_total_time"] = None
        values["cpu_metrics_available"] = any(name in row for name in ("user_time", "system_time"))
        if normalized is not None:
            values["normalized_query"] = normalized
        out.append({"database_name": database, "query_id": str(query_id), "query_fingerprint": fingerprint,
                    "feature_type": "QUERY_INTERVAL", "feature_values": values})
    query_features = [r for r in out if r["feature_type"] == "QUERY_INTERVAL"]
    for rank, item in enumerate(sorted(query_features,
                                       key=lambda x: x["feature_values"].get("total_execution_ms", 0), reverse=True), 1):
        item["feature_values"]["rank_by_total_time"] = rank
    wait_counts: dict[str, float] = {}
    for row in wait_rows:
        key = str(row.get("wait_event") or row.get("event") or row.get("state") or "unknown")[:128]
        wait_counts[key] = wait_counts.get(key, 0.0) + _number(
            row.get("count") or row.get("samples") or row.get("duration") or 1)
    if wait_counts:
        total_wait = sum(wait_counts.values()) or 1.0
        out.append({"database_name": server.database_name, "query_id": None, "query_fingerprint": None,
                    "feature_type": "WAIT_EVENT_INTERVAL",
                    "feature_values": {"distribution": {k: v / total_wait for k, v in wait_counts.items()},
                                       "sample_count": total_wait}})
    if relation_rows:
        values = _sum_rows(relation_rows, _DB_KEYS)
        out.append({"database_name": server.database_name, "query_id": None, "query_fingerprint": None,
                    "feature_type": "RELATION_INTERVAL", "feature_values": values})
    return out


def extract_and_store(report_id: int, adapter: PgProfileV4Adapter | None = None) -> dict[str, Any]:
    with SessionLocal() as db:
        report = db.get(PgProfileReport, report_id)
        if not report:
            raise ValueError("report not found")
        server = db.get(PgProfileServer, report.pgprofile_server_id)
        rows = extract(report, server, adapter=adapter)
        db.execute(delete(PgProfileFeature).where(
            PgProfileFeature.pgprofile_server_id == server.id,
            PgProfileFeature.start_sample_id == report.start_sample_id,
            PgProfileFeature.end_sample_id == report.end_sample_id,
            PgProfileFeature.extraction_version == EXTRACTION_VERSION))
        for item in rows:
            db.add(PgProfileFeature(
                pgprofile_server_id=server.id, start_sample_id=report.start_sample_id,
                end_sample_id=report.end_sample_id, period_start=report.period_start,
                period_end=report.period_end, database_name=item["database_name"],
                query_id=item["query_id"], query_fingerprint=item["query_fingerprint"],
                feature_type=item["feature_type"], feature_values=item["feature_values"],
                incident_id=report.incident_id, extraction_version=EXTRACTION_VERSION,
            ))
        db.commit()
    metrics.PGPROFILE_FEATURE_EXTRACTION.labels(
        status="SUCCEEDED" if rows else "PARTIAL_DATA", type="INTERVAL").inc()
    rebuild_baselines(server.id)
    return {"available": bool(rows), "status": "SUCCEEDED" if rows else "PARTIAL_DATA",
            "report_id": report_id, "feature_rows": len(rows), "extraction_version": EXTRACTION_VERSION}


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = max(0, min(len(values) - 1, math.ceil(pct * len(values)) - 1))
    return values[index]


def rebuild_baselines(server_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        features = db.execute(select(PgProfileFeature).where(
            PgProfileFeature.pgprofile_server_id == server_id,
            PgProfileFeature.feature_type == "QUERY_INTERVAL").order_by(PgProfileFeature.period_start)).scalars().all()
        groups: dict[tuple[str, str, int | None, int | None], list[PgProfileFeature]] = {}
        for feature in features:
            weekday = feature.period_start.weekday() if feature.period_start else -1
            hour = feature.period_start.hour if feature.period_start else -1
            groups.setdefault((feature.database_name or "postgres", feature.query_id or "", weekday, hour), []).append(feature)
        count = 0
        for (database, query_id, weekday, hour), rows in groups.items():
            means = [_number(r.feature_values.get("mean_execution_ms")) for r in rows]
            median = statistics.median(means) if means else None
            mad = statistics.median([abs(x - median) for x in means]) if means and median is not None else None
            existing = db.execute(select(QueryPerformanceBaseline).where(
                QueryPerformanceBaseline.pgprofile_server_id == server_id,
                QueryPerformanceBaseline.database_name == database,
                QueryPerformanceBaseline.query_id == query_id,
                QueryPerformanceBaseline.weekday == weekday,
                QueryPerformanceBaseline.hour == hour,
                QueryPerformanceBaseline.model_version == BASELINE_VERSION)).scalar_one_or_none()
            row = existing or QueryPerformanceBaseline(
                pgprofile_server_id=server_id, database_name=database, query_id=query_id,
                weekday=weekday, hour=hour, model_version=BASELINE_VERSION)
            row.query_fingerprint = rows[-1].query_fingerprint
            row.sample_count, row.median_execution_ms, row.mad_execution_ms = len(rows), median, mad
            row.p95_execution_ms = _percentile(means, .95)
            row.median_calls = statistics.median([_number(r.feature_values.get("calls")) for r in rows])
            row.median_rows = statistics.median([_number(r.feature_values.get("rows")) for r in rows])
            row.median_buffer_reads = statistics.median([_number(r.feature_values.get("shared_blocks_read")) for r in rows])
            row.median_temp_io_bytes = statistics.median([
                8192 * (_number(r.feature_values.get("temp_blocks_read")) + _number(r.feature_values.get("temp_blocks_written")))
                for r in rows])
            row.median_wal_bytes = statistics.median([_number(r.feature_values.get("wal_bytes")) for r in rows])
            row.first_seen, row.last_seen = rows[0].period_start, rows[-1].period_end
            row.history_status = "READY" if len(rows) >= settings.min_baseline_samples else "COLD_START"
            row.model_metadata = {"method": "median_mad", "minimum_samples": settings.min_baseline_samples,
                                  "isolation_forest_eligible": len(rows) >= settings.min_baseline_samples * 3}
            if len(rows) >= settings.min_baseline_samples * 3:
                vectors = [_ml_vector(item.feature_values) for item in rows]
                try:
                    model = isolation_forest.train_model(vectors, contamination=0.05)
                    for item, vector in zip(rows, vectors):
                        values = dict(item.feature_values or {})
                        values["ml_anomaly"] = isolation_forest.score_model(model, vector)
                        item.feature_values = values
                    row.model_metadata["isolation_forest"] = {
                        "status": "TRAINED", "rows": len(rows), "feature_version": EXTRACTION_VERSION,
                    }
                except Exception:
                    row.model_metadata["isolation_forest"] = {"status": "UNAVAILABLE"}
            if not existing:
                db.add(row)
            count += 1
        db.commit()
        return {"available": bool(count), "baselines": count, "model_version": BASELINE_VERSION}


def _ml_vector(values: dict[str, Any]) -> list[float]:
    return [_number(values.get(name)) for name in (
        "mean_execution_ms", "calls", "shared_blocks_read", "temp_blocks_read",
        "temp_blocks_written", "wal_bytes", "workload_contribution_pct",
    )]


def _authorized(stmt):
    stmt = stmt.join(PgProfileServer, PgProfileServer.id == PgProfileFeature.pgprofile_server_id)
    if settings.allowed_environments:
        stmt = stmt.where(func.lower(PgProfileServer.environment).in_(settings.allowed_environments))
    return stmt


def query_history(server_id: int | None = None, database: str | None = None,
                  query_id: str | None = None, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    with SessionLocal() as db:
        stmt = _authorized(select(PgProfileFeature)).where(PgProfileFeature.feature_type == "QUERY_INTERVAL")
        if server_id: stmt = stmt.where(PgProfileFeature.pgprofile_server_id == server_id)
        if database: stmt = stmt.where(PgProfileFeature.database_name == database)
        if query_id: stmt = stmt.where(PgProfileFeature.query_id == query_id)
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = db.execute(stmt.order_by(PgProfileFeature.period_start.desc()).offset(offset).limit(limit)).scalars().all()
        items = []
        for row in rows:
            baseline = db.execute(select(QueryPerformanceBaseline).where(
                QueryPerformanceBaseline.pgprofile_server_id == row.pgprofile_server_id,
                QueryPerformanceBaseline.database_name == row.database_name,
                QueryPerformanceBaseline.query_id == row.query_id,
                QueryPerformanceBaseline.weekday == (row.period_start.weekday() if row.period_start else -1),
                QueryPerformanceBaseline.hour == (row.period_start.hour if row.period_start else -1),
                QueryPerformanceBaseline.model_version == BASELINE_VERSION)).scalar_one_or_none()
            mean = _number(row.feature_values.get("mean_execution_ms"))
            median = baseline.median_execution_ms if baseline else None
            mad = baseline.mad_execution_ms if baseline else None
            robust_z = (0.6745 * (mean - median) / mad) if median is not None and mad not in (None, 0) else None
            items.append({"id": row.id, "server_id": row.pgprofile_server_id,
                          "database_name": row.database_name, "query_id": row.query_id,
                          "query_fingerprint": row.query_fingerprint,
                          "period_start": row.period_start.isoformat() if row.period_start else None,
                          "period_end": row.period_end.isoformat() if row.period_end else None,
                          "features": row.feature_values, "baseline_median_ms": median,
                          "baseline_p95_ms": baseline.p95_execution_ms if baseline else None,
                          "percentage_change": ((mean - median) / median * 100) if median else None,
                          "robust_z_score": robust_z,
                          "anomaly_score": (row.feature_values.get("ml_anomaly") or {}).get("anomaly_score"),
                          "is_anomaly": (row.feature_values.get("ml_anomaly") or {}).get("is_anomaly"),
                          "history_status": baseline.history_status if baseline else "COLD_START"})
        return {"items": items, "total": total, "limit": limit, "offset": offset}


def list_baselines(server_id: int | None = None, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    with SessionLocal() as db:
        stmt = select(QueryPerformanceBaseline).join(
            PgProfileServer, PgProfileServer.id == QueryPerformanceBaseline.pgprofile_server_id)
        if settings.allowed_environments:
            stmt = stmt.where(func.lower(PgProfileServer.environment).in_(settings.allowed_environments))
        if server_id: stmt = stmt.where(QueryPerformanceBaseline.pgprofile_server_id == server_id)
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = db.execute(stmt.order_by(QueryPerformanceBaseline.last_seen.desc()).offset(offset).limit(limit)).scalars().all()
        return {"items": [{"id": r.id, "server_id": r.pgprofile_server_id, "database_name": r.database_name,
                           "query_id": r.query_id, "query_fingerprint": r.query_fingerprint,
                           "weekday": None if r.weekday == -1 else r.weekday,
                           "hour": None if r.hour == -1 else r.hour, "sample_count": r.sample_count,
                           "median_execution_ms": r.median_execution_ms, "mad_execution_ms": r.mad_execution_ms,
                           "p95_execution_ms": r.p95_execution_ms, "history_status": r.history_status,
                           "feedback_state": r.feedback_state, "model_version": r.model_version,
                           "last_seen": r.last_seen.isoformat() if r.last_seen else None,
                           "model_metadata": r.model_metadata} for r in rows],
                "total": total, "limit": limit, "offset": offset}


def list_features(server_id: int | None = None, feature_type: str | None = None,
                  limit: int = 100, offset: int = 0) -> dict[str, Any]:
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    with SessionLocal() as db:
        stmt = _authorized(select(PgProfileFeature))
        if server_id:
            stmt = stmt.where(PgProfileFeature.pgprofile_server_id == server_id)
        if feature_type:
            stmt = stmt.where(PgProfileFeature.feature_type == feature_type)
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = db.execute(stmt.order_by(PgProfileFeature.id.desc()).offset(offset).limit(limit)).scalars().all()
        return {"items": [{"id": r.id, "server_id": r.pgprofile_server_id,
                            "start_sample_id": r.start_sample_id, "end_sample_id": r.end_sample_id,
                            "period_start": r.period_start.isoformat() if r.period_start else None,
                            "period_end": r.period_end.isoformat() if r.period_end else None,
                            "database_name": r.database_name, "query_id": r.query_id,
                            "query_fingerprint": r.query_fingerprint, "feature_type": r.feature_type,
                            "feature_values": r.feature_values, "incident_id": r.incident_id,
                            "extraction_version": r.extraction_version} for r in rows],
                "total": total, "limit": limit, "offset": offset}


def set_baseline_feedback(baseline_id: int, state: str, note: str | None, actor: str) -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(QueryPerformanceBaseline, baseline_id)
        server = db.get(PgProfileServer, row.pgprofile_server_id) if row else None
        if not row or not server or (settings.allowed_environments and
                                     (server.environment or "").lower() not in settings.allowed_environments):
            raise ValueError("baseline not found")
        row.feedback_state = state
        metadata = dict(row.model_metadata or {})
        metadata["feedback"] = {"actor": actor, "note": note, "reviewed_at": datetime.utcnow().isoformat() + "Z"}
        row.model_metadata = metadata
        db.commit(); db.refresh(row)
        return {"id": row.id, "feedback_state": row.feedback_state, "status": "UPDATED"}
