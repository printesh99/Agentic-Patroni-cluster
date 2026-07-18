from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
from typing import Any

from .. import pg_replication
from .. import sources as S
from .schema import PhysicalReplicationEvidence, WalArchiverEvidence

_PHYSICAL_CACHE: tuple[float, PhysicalReplicationEvidence] | None = None
_PHYSICAL_LOCK = threading.Lock()
_PHYSICAL_TTL_S = 5.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if value in (None, "", "-"):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def collect_physical_replication() -> PhysicalReplicationEvidence:
    global _PHYSICAL_CACHE
    now = time.monotonic()
    if _PHYSICAL_CACHE and now - _PHYSICAL_CACHE[0] <= _PHYSICAL_TTL_S:
        return _PHYSICAL_CACHE[1]
    with _PHYSICAL_LOCK:
        now = time.monotonic()
        if _PHYSICAL_CACHE and now - _PHYSICAL_CACHE[0] <= _PHYSICAL_TTL_S:
            return _PHYSICAL_CACHE[1]
        result = _collect_physical_replication_uncached()
        _PHYSICAL_CACHE = (time.monotonic(), result)
        return result


def _collect_physical_replication_uncached() -> PhysicalReplicationEvidence:
    topology = pg_replication.build_topology()
    members = topology.get("members") or []
    names = {m.get("name") for m in members if m.get("name")}
    rows = topology.get("replication") or []
    physical = [r for r in rows if r.get("application_name") in names]
    logical = [r for r in rows if r.get("application_name") not in names]
    return PhysicalReplicationEvidence(
        primary_member=(topology.get("summary") or {}).get("leader"),
        patroni_ok=bool((topology.get("summary") or {}).get("patroni_ok", False)),
        standbys=physical, logical_walsenders=len(logical), collected_at=utcnow(),
    )


def collect_wal_archiver() -> WalArchiverEvidence:
    row = S.sql_one(
        "select pg_walfile_name(pg_current_wal_lsn()), pg_current_wal_lsn()::text, "
        "last_archived_wal, last_archived_time::text, archived_count::bigint, "
        "failed_count::bigint, last_failed_wal, last_failed_time::text "
        "from pg_stat_archiver"
    )
    if not row:
        raise S.SourceError("pg_stat_archiver returned no row")
    return WalArchiverEvidence(
        current_wal_segment=str(row[0]), current_wal_lsn=str(row[1]),
        last_archived_wal=str(row[2]) if row[2] else None,
        last_archived_time=_parse_time(row[3]), archived_count=int(row[4] or 0),
        failed_count=int(row[5] or 0), last_failed_wal=str(row[6]) if row[6] else None,
        last_failed_time=_parse_time(row[7]), collected_at=utcnow(),
    )
