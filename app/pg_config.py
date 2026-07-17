"""Configuration page — pg_settings, Patroni dynamic config, pause state, per-db/role GUCs."""
from __future__ import annotations

import json
from typing import Any

from . import sources as S
from .pg_overview import _members


def _patroni_config() -> dict[str, Any]:
    """Patroni dynamic (DCS) config + pause/leader/members via REST."""
    out = {"config": {}, "paused": False, "leader": None, "members": [],
           "patroni_ok": False, "mode": "unknown"}
    try:
        cluster = S.patroni_cluster()
        members, leader, _tl = _members(cluster)
        out.update(members=members, leader=leader, patroni_ok=True,
                   paused=bool(cluster.get("pause")))
        out["mode"] = "paused" if out["paused"] else "running"
    except S.SourceError:
        return out
    # dynamic config document
    args = S.KUBECTL + ["-n", S.NS, "exec", S.primary_pod(), "-c", S.DB_CONTAINER, "--",
                        "bash", "-lc",
                        "curl -sk https://localhost:8008/config || curl -s http://localhost:8008/config"]
    try:
        out["config"] = json.loads(S._run(args, timeout=15))
    except (S.SourceError, json.JSONDecodeError):
        pass
    return out


def build_parameters() -> dict[str, Any]:
    rows = S.sql(
        "select name, setting, coalesce(unit,''), context, coalesce(short_desc,''), "
        "source, pending_restart::text from pg_settings order by name"
    )
    parameters = [{
        "name": r[0], "parameter": r[0], "setting": r[1], "value": r[1],
        "unit": r[2], "context": r[3], "short_desc": r[4], "source": r[5],
        "pending_restart": r[6] == "t",
    } for r in rows]
    pat = _patroni_config()
    return {
        "source": "pg_settings",
        "available": True, "config_available": pat["patroni_ok"],
        "parameters": parameters,
        "settings": parameters,
        "contexts": sorted({p["context"] for p in parameters}),
        "guardrails": {"restart_required": [p["name"] for p in parameters if p["pending_restart"]]},
        "leader": pat["leader"], "members": pat["members"],
        "paused": pat["paused"], "mode": pat["mode"], "patroni_ok": pat["patroni_ok"],
    }


def build_patroni() -> dict[str, Any]:
    pat = _patroni_config()
    return {"source": "Patroni DCS", "available": pat["patroni_ok"],
            "config_available": bool(pat["config"]), **pat}


def build_maintenance() -> dict[str, Any]:
    pat = _patroni_config()
    return {
        "source": "Patroni",
        "available": pat["patroni_ok"],
        "paused": pat["paused"], "mode": pat["mode"],
        "members": pat["members"], "leader": pat["leader"],
        "patroni_ok": pat["patroni_ok"],
    }


def build_database_settings() -> dict[str, Any]:
    rows = S.sql(
        "select coalesce(d.datname,'(all databases)'), unnest(s.setconfig) "
        "from pg_db_role_setting s left join pg_database d on d.oid=s.setdatabase "
        "where s.setrole = 0 order by 1"
    )
    settings = []
    for r in rows:
        name, _, value = r[1].partition("=")
        settings.append({"database": r[0], "name": name, "parameter": name,
                         "setting": value, "value": value})
    return {"source": "pg_db_role_setting", "settings": settings}


def build_role_settings() -> dict[str, Any]:
    rows = S.sql(
        "select coalesce(r.rolname,'(all roles)'), unnest(s.setconfig) "
        "from pg_db_role_setting s left join pg_roles r on r.oid=s.setrole "
        "where s.setrole <> 0 order by 1"
    )
    settings = []
    for r in rows:
        name, _, value = r[1].partition("=")
        settings.append({"role_name": r[0], "name": name, "parameter": name,
                         "setting": value, "value": value})
    return {"source": "pg_db_role_setting", "settings": settings}
