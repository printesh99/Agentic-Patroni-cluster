"""Collect, normalize, and persist Phase 1 health snapshots."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from .. import sources as S
from ..collectors import loki_collector, patroni_collector, postgres_collector, prometheus_collector
from ..db.models import ClusterHealthSnapshot
from ..db.session import SessionLocal
from ..ml.features import build_snapshot
from . import inventory_service


def collect_raw() -> dict[str, Any]:
    return {
        "prometheus": prometheus_collector.collect(),
        "postgres": postgres_collector.collect(),
        "patroni": patroni_collector.collect(),
        "loki": loki_collector.collect(),
    }


def collect_and_persist() -> dict[str, Any]:
    raw = collect_raw()
    normalized = build_snapshot(raw)
    f = normalized["features"]
    with SessionLocal() as db:
        inv = inventory_service.resolve(db, create=True)
        row = ClusterHealthSnapshot(
            inventory_id=inv.id,
            role=normalized["role"],
            timeline=normalized["timeline"],
            replication_lag_seconds=f["replication_lag_seconds"],
            wal_rate_mb_min=f["wal_rate_mb_min"],
            wal_pvc_used_percent=f["wal_pvc_used_percent"],
            pgdata_pvc_used_percent=f["pgdata_pvc_used_percent"],
            active_connections_percent=f["active_connections_percent"],
            pgbouncer_pool_used_percent=f["pgbouncer_pool_used_percent"],
            cpu_percent=f["cpu_percent"],
            memory_percent=f["memory_percent"],
            deadlocks_per_min=f["deadlocks_per_min"],
            locks_waiting_count=_int_or_none(f["locks_waiting_count"]),
            long_txn_count=_int_or_none(f["long_txn_count"]),
            idle_in_transaction_count=_int_or_none(f["idle_in_transaction_count"]),
            archive_failed_count=_int_or_none(f["archive_failed_count"]),
            backup_duration_minutes=f["backup_duration_minutes"],
            pod_restart_count=_int_or_none(f["pod_restart_count"]),
            logical_slot_inactive_count=_int_or_none(f["logical_slot_inactive_count"]),
            replication_slot_retained_wal_mb=f["replication_slot_retained_wal_mb"],
            pg_stat_statements_slow_query_count=_int_or_none(f["pg_stat_statements_slow_query_count"]),
            temp_files_mb=f["temp_files_mb"],
            patroni_status=normalized["patroni_status"],
            raw_metrics=normalized["raw_metrics"],
        )
        pg_values = (raw.get("postgres") or {}).get("values") or {}
        row.active_connections = _int_or_none(pg_values.get("active_connections"))
        row.max_connections = _int_or_none(pg_values.get("max_connections"))
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "available": True,
            "snapshot_id": row.id,
            "inventory_id": inv.id,
            "cluster_name": inv.cluster_name,
            "namespace": inv.namespace,
            "features": f,
            "warnings": normalized["warnings"],
            "sources": {k: {"available": v.get("available"), "warnings": v.get("warnings", [])} for k, v in raw.items()},
        }


def latest() -> dict[str, Any]:
    with SessionLocal() as db:
        inv = inventory_service.resolve(db)
        row = db.execute(
            select(ClusterHealthSnapshot)
            .where(ClusterHealthSnapshot.inventory_id == inv.id)
            .order_by(ClusterHealthSnapshot.collected_at.desc(), ClusterHealthSnapshot.id.desc()).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return {"available": False, "reason": "no snapshots collected"}
        return {
            "available": True,
            "snapshot_id": row.id,
            "collected_at": row.collected_at.isoformat() if row.collected_at else None,
            "inventory_id": inv.id,
            "cluster_name": inv.cluster_name,
            "features": {
                "replication_lag_seconds": row.replication_lag_seconds,
                "wal_rate_mb_min": row.wal_rate_mb_min,
                "wal_pvc_used_percent": row.wal_pvc_used_percent,
                "pgdata_pvc_used_percent": row.pgdata_pvc_used_percent,
                "active_connections_percent": row.active_connections_percent,
                "pgbouncer_pool_used_percent": row.pgbouncer_pool_used_percent,
                "cpu_percent": row.cpu_percent,
                "memory_percent": row.memory_percent,
                "deadlocks_per_min": row.deadlocks_per_min,
                "locks_waiting_count": row.locks_waiting_count,
                "long_txn_count": row.long_txn_count,
                "idle_in_transaction_count": row.idle_in_transaction_count,
                "archive_failed_count": row.archive_failed_count,
                "backup_duration_minutes": row.backup_duration_minutes,
                "pod_restart_count": row.pod_restart_count,
                "logical_slot_inactive_count": row.logical_slot_inactive_count,
                "replication_slot_retained_wal_mb": row.replication_slot_retained_wal_mb,
                "pg_stat_statements_slow_query_count": row.pg_stat_statements_slow_query_count,
                "temp_files_mb": row.temp_files_mb,
            },
        }


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
