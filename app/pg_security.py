"""Security page — auth/roles/HBA, pgaudit, sensitive-data heuristic, TLS sessions."""
from __future__ import annotations

from typing import Any

from . import sources as S


def _pg_bool(value: Any) -> bool:
    """Normalize native and textual PostgreSQL boolean representations."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"t", "true", "1", "on", "yes"}


def build_auth() -> dict[str, Any]:
    roles = S.sql(
        "select rolname, rolcanlogin::text, rolcreatedb::text, rolcreaterole::text, "
        "rolsuper::text, rolreplication::text, "
        "coalesce(to_char(rolvaliduntil,'YYYY-MM-DD'),'') "
        "from pg_roles order by rolsuper desc, rolname"
    )
    role_rows = [{
        "rolname": r[0], "rolcanlogin": _pg_bool(r[1]), "rolcreatedb": _pg_bool(r[2]),
        "rolcreaterole": _pg_bool(r[3]), "rolsuper": _pg_bool(r[4]),
        "rolreplication": _pg_bool(r[5]), "valid_until": r[6],
    } for r in roles]

    hba = S.sql(
        "select line_number, coalesce(type,''), array_to_string(database,','), "
        "coalesce(address,''), coalesce(netmask,''), coalesce(auth_method,''), "
        "coalesce(error::text,'') from pg_hba_file_rules order by line_number"
    )
    hba_rows = [{
        "line_number": int(r[0]), "type": r[1], "database": r[2], "address": r[3],
        "netmask": r[4], "auth_method": r[5], "error": r[6] or None,
    } for r in hba]

    srows = S.sql("select name, setting from pg_settings where name in "
                  "('password_encryption','ssl','authentication_timeout')")
    settings = {r[0]: r[1] for r in srows}

    login_roles = [r for r in role_rows if r["rolcanlogin"]]
    return {
        "source": "pg_roles + pg_hba_file_rules",
        "roles": role_rows,
        "hba": hba_rows,
        "settings": settings,
        "summary": {
            "available": True, "ok": all(not h["error"] for h in hba_rows),
            "login_roles": len(login_roles),
            "privileged_roles": sum(1 for r in role_rows if r["rolsuper"]),
            "no_expiry_login_roles": sum(1 for r in login_roles if not r["valid_until"]),
            "hba_rules": len(hba_rows),
            "hba_errors": sum(1 for h in hba_rows if h["error"]),
            "password_encryption": settings.get("password_encryption"),
            "auth_methods": sorted({h["auth_method"] for h in hba_rows if h["auth_method"]}),
        },
    }


def build_pgaudit() -> dict[str, Any]:
    inst = S.sql_one("select count(*) from pg_extension where extname='pgaudit'")
    installed = bool(inst and int(inst[0]) > 0)
    preload = S.sql_one("select setting from pg_settings where name='shared_preload_libraries'")
    preloaded = bool(preload and "pgaudit" in preload[0])
    controls = S.sql(
        "select name, setting, coalesce(context,'') from pg_settings "
        "where name like 'pgaudit.%' order by name"
    )
    control_rows = [{"name": r[0], "setting": r[1], "context": r[2]} for r in controls]
    return {
        "source": "pg_extension + pg_settings",
        "extension": {"installed": installed, "name": "pgaudit", "preloaded": preloaded},
        "settings": {r[0]: r[1] for r in controls},
        "controls": control_rows,
        "summary": {"installed": installed, "preloaded": preloaded,
                    "controls": len(control_rows), "available": True,
                    "status": "ok" if installed and preloaded else "partial"},
    }


_SENSITIVE = ("email", "phone", "mobile", "ssn", "passport", "iban", "card", "pan",
              "cvv", "dob", "birth", "salary", "password", "secret", "token", "nric",
              "emirates", "national_id")


def build_sensitive() -> dict[str, Any]:
    like = " or ".join("lower(column_name) like '%%%s%%'" % w for w in _SENSITIVE)
    rows = S.sql(
        "select table_schema, table_name, column_name, data_type from information_schema.columns "
        f"where table_schema not in ('pg_catalog','information_schema') and ({like}) "
        "order by table_schema, table_name limit 200"
    )
    matches = [{
        "schema": r[0], "table": r[1], "column": r[2], "data_type": r[3],
        "category": "PII/secret (name heuristic)",
        "evidence": f"column name matches sensitive pattern",
    } for r in rows]
    sc = S.sql_one("select count(*), count(distinct table_schema) from information_schema.columns "
                   "where table_schema not in ('pg_catalog','information_schema')")
    return {
        "source": "information_schema.columns (name heuristic)",
        "matches": matches,
        "summary": {"matches": len(matches), "available": True,
                    "scanned_columns": int(sc[0]) if sc else 0,
                    "scanned_schemas": int(sc[1]) if sc else 0,
                    "status": "ok" if not matches else "review"},
    }


def build_tls() -> dict[str, Any]:
    sessions = S.sql(
        "select a.pid, coalesce(a.usename,''), coalesce(a.application_name,''), "
        "s.ssl::text, coalesce(s.version,''), coalesce(s.cipher,''), coalesce(s.bits,0)::text, "
        "coalesce(s.client_dn,'') "
        "from pg_stat_ssl s join pg_stat_activity a on a.pid=s.pid "
        "where a.backend_type='client backend' order by a.pid"
    )
    sess_rows = [{
        "pid": int(r[0]), "username": r[1], "application_name": r[2],
        "ssl": _pg_bool(r[3]), "version": r[4], "cipher": r[5],
        "bits": int(r[6]) if r[6].isdigit() else 0, "client_dn": r[7] or None,
    } for r in sessions]
    srows = S.sql("select name, setting from pg_settings where name in "
                  "('ssl','ssl_min_protocol_version','ssl_cert_file')")
    settings = {r[0]: r[1] for r in srows}
    ssl_count = sum(1 for s in sess_rows if s["ssl"])
    return {
        "source": "pg_stat_ssl",
        "sessions": sess_rows,
        "settings": settings,
        "summary": {"available": True, "ssl_sessions": ssl_count,
                    "non_ssl_sessions": len(sess_rows) - ssl_count,
                    "protocols": sorted({s["version"] for s in sess_rows if s["version"]}),
                    "status": "ok" if settings.get("ssl") == "on" else "review"},
    }
