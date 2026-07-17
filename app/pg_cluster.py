"""Build the Cluster/Patroni detail payload (/api/v1/ui/cluster/{id})."""
from __future__ import annotations

from typing import Any

from . import sources as S
from .pg_overview import _members  # reuse member normalisation


def _settings() -> dict[str, Any]:
    keys = (
        "archive_mode", "max_replication_slots", "max_slot_wal_keep_size",
        "max_wal_senders", "synchronous_commit", "synchronous_standby_names",
        "wal_level",
    )
    rows = S.sql(
        "select name, setting from pg_settings where name in ("
        + ",".join("'%s'" % k for k in keys) + ")"
    )
    return {r[0]: r[1] for r in rows}


def _wal() -> dict[str, Any]:
    row = S.sql_one(
        "select pg_current_wal_lsn()::text, "
        "pg_walfile_name(pg_current_wal_lsn()), "
        "to_char(pg_postmaster_start_time(),'YYYY-MM-DD\"T\"HH24:MI:SSOF')"
    )
    if not row:
        return {}
    return {"current_lsn": row[0], "wal_file": row[1], "started_at": row[2]}


def _slots() -> list[dict[str, Any]]:
    rows = S.sql(
        "select slot_name, active::text, "
        "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn),0)::bigint, "
        "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn),0)::bigint "
        "from pg_replication_slots"
    )
    out = []
    for r in rows:
        out.append({
            "slot_name": r[0],
            "active": r[1] == "t",
            "retained_wal_bytes": int(r[2]),
            "lag_bytes": int(r[3]),
        })
    return out


def _replication() -> list[dict[str, Any]]:
    rows = S.sql(
        "select application_name, state, sync_state, "
        "coalesce(replay_lsn::text,'-'), "
        "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn),0)::bigint, "
        "coalesce(extract(epoch from replay_lag),0)::int "
        "from pg_stat_replication order by application_name"
    )
    out = []
    for r in rows:
        out.append({
            "application_name": r[0],
            "state": r[1],
            "sync_state": r[2],
            "replay_lsn": r[3],
            "replay_lag_bytes": int(r[4]),
            "replay_lag_sec": int(r[5]),
        })
    return out


def _history() -> list[Any]:
    """Patroni timeline history (list of [timeline, lsn, reason, timestamp, …])."""
    try:
        doc = S.patroni_cluster()
    except S.SourceError:
        return []
    # Patroni's /cluster includes a "history" key on some versions.
    hist = doc.get("history")
    return hist if isinstance(hist, list) else []


def build_cluster() -> dict[str, Any]:
    try:
        patroni = S.patroni_cluster()
        patroni_ok = True
    except S.SourceError:
        patroni, patroni_ok = {"members": []}, False
    members, _leader, _tl = _members(patroni)

    return {
        "source": "live PostgreSQL + Patroni",
        "cluster_name": S.CLUSTER_NAME,
        "patroni": {"patroni_ok": patroni_ok, "members": members,
                    "scope": patroni.get("scope")},
        "wal": _wal(),
        "settings": _settings(),
        "slots": _slots(),
        "replication": _replication(),
        "history": _history(),
        "pgbouncer": _pgbouncer(),
    }


def _pgbouncer() -> dict[str, int]:
    pgb = [p for p in S.pods() if p["role"] == "pgbouncer"]
    return {"pods_ready": sum(1 for p in pgb if p["ready_bool"]), "pods_total": len(pgb)}
