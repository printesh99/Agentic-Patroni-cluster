"""Read-only PostgreSQL diagnostic collector for Phase 1 snapshots."""
from __future__ import annotations

from typing import Any, Callable

from .. import sources as S


def _one(query: str, cast: Callable[[str], Any] = float, default: Any = None) -> Any:
    row = S.sql_one(query)
    if not row or row[0] in ("", None):
        return default
    return cast(row[0])


def collect() -> dict[str, Any]:
    values: dict[str, Any] = {}
    warnings: list[str] = []

    queries: dict[str, tuple[str, Callable[[str], Any], Any]] = {
        "active_connections": ("select count(*) from pg_stat_activity", int, None),
        "max_connections": ("select setting::int from pg_settings where name='max_connections'", int, None),
        "locks_waiting_count": ("select count(*) from pg_locks where not granted", int, 0),
        "long_txn_count": (
            "select count(*) from pg_stat_activity where xact_start is not null "
            "and now() - xact_start > interval '5 minutes'",
            int,
            0,
        ),
        "idle_in_transaction_count": (
            "select count(*) from pg_stat_activity where state = 'idle in transaction'",
            int,
            0,
        ),
        # pg_stat_archiver.failed_count is cumulative since stats_reset. Treat
        # it as an active failure only when the most recent archiver event is a
        # failure; old recovered failures should not keep AI incidents open.
        "archive_failed_count": (
            "select case "
            "when failed_count = 0 then 0 "
            "when last_failed_time is null then 0 "
            "when last_archived_time is null then failed_count "
            "when last_failed_time > last_archived_time then failed_count "
            "else 0 end "
            "from pg_stat_archiver",
            int,
            0,
        ),
        "logical_slot_inactive_count": (
            "select count(*) from pg_replication_slots where slot_type='logical' and not active",
            int,
            0,
        ),
        "replication_slot_retained_wal_mb": (
            "select coalesce(max(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)),0) / 1024 / 1024 "
            "from pg_replication_slots where restart_lsn is not null",
            float,
            0.0,
        ),
        "temp_files_mb": (
            "select coalesce(sum(temp_bytes),0) / 1024 / 1024 from pg_stat_database",
            float,
            0.0,
        ),
    }

    for name, (query, cast, default) in queries.items():
        try:
            values[name] = _one(query, cast, default)
        except Exception as exc:
            values[name] = default
            warnings.append(f"postgres diagnostic {name} unavailable: {exc}")

    try:
        used = values.get("active_connections")
        mx = values.get("max_connections")
        values["active_connections_percent"] = (used / mx * 100.0) if used is not None and mx else None
    except Exception as exc:
        values["active_connections_percent"] = None
        warnings.append(f"active connection percent unavailable: {exc}")

    try:
        # pg_stat_statements may not be installed. Treat missing extension as a warning.
        values["pg_stat_statements_slow_query_count"] = _one(
            "select count(*) from pg_stat_statements where mean_exec_time > 1000",
            int,
            0,
        )
    except Exception as exc:
        values["pg_stat_statements_slow_query_count"] = None
        warnings.append(f"pg_stat_statements unavailable: {exc}")

    try:
        lag = _one(
            "select coalesce(max(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)),0) "
            "from pg_stat_replication",
            float,
            0.0,
        )
        values["replication_lag_bytes"] = lag
    except Exception as exc:
        values["replication_lag_bytes"] = None
        warnings.append(f"replication lag bytes unavailable: {exc}")

    return {
        "source": "postgres",
        "available": True,
        "values": values,
        "warnings": warnings,
    }
