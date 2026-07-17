"""Replication page — topology, sync, logical, FDW, history."""
from __future__ import annotations

from typing import Any

from . import sources as S
from .pg_overview import _members


def _patroni():
    try:
        return S.patroni_cluster(), True
    except S.SourceError:
        return {"members": []}, False


def _stat_replication() -> list[dict[str, Any]]:
    rows = S.sql(
        "select application_name, state, sync_state, coalesce(client_addr::text,'-'), "
        "coalesce(replay_lsn::text,'-'), "
        "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn),0)::bigint "
        "from pg_stat_replication order by application_name"
    )
    return [{"application_name": r[0], "state": r[1], "sync_state": r[2],
             "client_addr": r[3], "replay_lsn": r[4], "replay_lag_bytes": int(r[5])}
            for r in rows]


def build_topology() -> dict[str, Any]:
    patroni, ok = _patroni()
    members, leader, _tl = _members(patroni)
    repl = _stat_replication()
    return {
        "source": "Patroni + pg_stat_replication",
        "members": members,
        "replication": repl,
        "pgo": {"available": True, "name": S.CLUSTER_NAME},
        "data_centers": [{"name": "dc1", "role": "PRIMARY",
                          "members": len(members), "leader": leader}],
        "summary": {"members": len(members), "leader": leader,
                    "streaming": sum(1 for r in repl if r["state"] == "streaming"),
                    "patroni_ok": ok},
    }


def build_sync() -> dict[str, Any]:
    patroni, ok = _patroni()
    members, leader, _ = _members(patroni)
    ssn = S.sql_one("select setting from pg_settings where name='synchronous_standby_names'")
    repl = _stat_replication()
    candidates = [{"name": m["name"], "role": m["role"], "state": m["state"]}
                  for m in members if m["role"] in ("sync_standby", "replica")]
    return {
        "source": "pg_stat_replication + pg_settings",
        "members": members,
        "replication": repl,
        "sync_candidates": candidates,
        "summary": {
            "synchronous_standby_names": ssn[0] if ssn else "",
            "sync_mode": "synchronous" if (ssn and ssn[0]) else "asynchronous",
            "sync_standbys": sum(1 for r in repl if r["sync_state"] == "sync"),
            "patroni_ok": ok,
        },
    }


def build_logical(database: str | None = None) -> dict[str, Any]:
    pubs = S.sql(
        "select p.pubname, r.rolname, p.puballtables::text, p.pubinsert::text, "
        "p.pubupdate::text, p.pubdelete::text, "
        "(select count(*) from pg_publication_rel pr where pr.prpubid=p.oid) "
        "from pg_publication p join pg_roles r on r.oid=p.pubowner order by p.pubname"
    )
    publications = [{"pubname": r[0], "owner": r[1], "puballtables": r[2] == "t",
                     "pubinsert": r[3] == "t", "pubupdate": r[4] == "t",
                     "pubdelete": r[5] == "t", "table_count": int(r[6])} for r in pubs]
    subs = S.sql(
        "select s.subname, r.rolname, s.subenabled::text, s.subslotname, "
        "array_to_string(s.subpublications,',') from pg_subscription s "
        "join pg_roles r on r.oid=s.subowner order by s.subname"
    )
    subscriptions = [{"subname": r[0], "owner": r[1], "subenabled": r[2] == "t",
                      "subslotname": r[3], "subpublications": r[4]} for r in subs]
    slots = S.sql(
        "select slot_name, active::text, coalesce(plugin,'') from pg_replication_slots "
        "where slot_type='logical' order by slot_name"
    )
    logical_slots = [{"slot_name": r[0], "active": r[1] == "t", "plugin": r[2]} for r in slots]
    return {
        "source": "pg_publication / pg_subscription / pg_replication_slots",
        "publications": publications,
        "subscriptions": subscriptions,
        "logical_slots": logical_slots,
        "subscription_health": [],
        "subscription_tables_not_ready": [],
        "summary": {"publications": len(publications), "subscriptions": len(subscriptions),
                    "logical_slots": len(logical_slots)},
    }


def build_fdw(database: str | None = None) -> dict[str, Any]:
    servers = S.sql(
        "select s.srvname, w.fdwname, r.rolname, coalesce(s.srvtype,''), "
        "coalesce(array_to_string(s.srvoptions,', '),'') "
        "from pg_foreign_server s join pg_foreign_data_wrapper w on w.oid=s.srvfdw "
        "join pg_roles r on r.oid=s.srvowner order by s.srvname"
    )
    server_rows = [{"srvname": r[0], "fdwname": r[1], "owner": r[2],
                    "srvtype": r[3], "options": r[4]} for r in servers]
    ftbls = S.sql(
        "select n.nspname, c.relname, s.srvname from pg_foreign_table ft "
        "join pg_class c on c.oid=ft.ftrelid join pg_namespace n on n.oid=c.relnamespace "
        "join pg_foreign_server s on s.oid=ft.ftserver order by 1,2"
    )
    foreign_tables = [{"schema": r[0], "table_name": r[1], "server": r[2]} for r in ftbls]
    return {
        "source": "pg_foreign_server / pg_foreign_table",
        "servers": server_rows,
        "foreign_tables": foreign_tables,
        "user_mappings": [],
        "summary": {"servers": len(server_rows), "foreign_tables": len(foreign_tables)},
    }


def build_history(limit: int = 75) -> dict[str, Any]:
    patroni, _ok = _patroni()
    hist = patroni.get("history")
    rows = hist if isinstance(hist, list) else []
    return {
        "source": "Patroni history",
        "patroni_history": rows[-int(limit):],
        "jobs": [],
        "summary": {"transitions": len(rows)},
    }
