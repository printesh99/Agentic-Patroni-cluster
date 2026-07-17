"""Admin/DBA page — databases, roles, privileges, HBA, object inventory, pods."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import sources as S
from .pg_overview import _members


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _literal(value: str) -> str:
    return value.replace("'", "''")


def build_databases(include_objects: bool = False) -> dict[str, Any]:
    dbs = S.sql(
        "select d.datname, r.rolname, pg_encoding_to_char(d.encoding), d.datcollate, "
        "pg_database_size(d.datname), d.datallowconn::text, d.datistemplate::text, "
        "(select count(*) from pg_stat_activity a where a.datname=d.datname) "
        "from pg_database d join pg_roles r on r.oid=d.datdba "
        "order by pg_database_size(d.datname) desc"
    )
    databases = [{
        "datname": r[0], "owner": r[1], "encoding": r[2], "lc_collate": r[3],
        "size_bytes": int(r[4]), "allow_connections": r[5] == "t",
        "is_template": r[6] == "t", "active_connections": int(r[7]),
    } for r in dbs]

    result = {
        "source": "pg_database + pg_stat_activity",
        "available": True,
        "generated_at": _generated_at(),
        "databases": databases,
    }
    if not include_objects:
        return result

    tables = S.sql(
        "select t.schemaname, t.tablename, t.tableowner, "
        "coalesce(c.reltuples::bigint,0), "
        "pg_total_relation_size(quote_ident(t.schemaname)||'.'||quote_ident(t.tablename)) "
        "from pg_tables t join pg_class c on c.relname=t.tablename "
        "where t.schemaname not in ('pg_catalog','information_schema') "
        "order by 5 desc limit 200"
    )
    table_rows = [{
        "table_schema": r[0], "table_name": r[1], "owner": r[2],
        "table_type": "table", "estimated_rows": int(r[3]), "total_size_bytes": int(r[4]),
    } for r in tables]

    exts = S.sql(
        "select e.extname, e.extversion, n.nspname from pg_extension e "
        "join pg_namespace n on n.oid=e.extnamespace order by e.extname"
    )
    extensions = [{"name": r[0], "installed_version": r[1], "schema_name": r[2]} for r in exts]

    schemas = S.sql(
        "select nspname, (select rolname from pg_roles where oid=nspowner) "
        "from pg_namespace where nspname not in ('pg_catalog','information_schema','pg_toast') "
        "order by nspname"
    )
    schema_rows = [{"schema_name": r[0], "owner": r[1]} for r in schemas]

    return {
        **result,
        "tables": table_rows,
        "extensions": extensions,
        "schemas": schema_rows,
        "indexes": [],
    }


def build_roles() -> dict[str, Any]:
    rows = S.sql(
        "select rolname, rolsuper::text, rolcanlogin::text, rolcreatedb::text, "
        "rolcreaterole::text, rolinherit::text, rolreplication::text, rolconnlimit, "
        "coalesce(to_char(rolvaliduntil,'YYYY-MM-DD'),''), "
        "coalesce((select string_agg(g.rolname,',') from pg_auth_members m "
        "join pg_roles g on g.oid=m.roleid where m.member=pg_roles.oid),'') "
        "from pg_roles order by rolsuper desc, rolname"
    )
    roles = [{
        "rolname": r[0], "rolsuper": r[1] == "t", "rolcanlogin": r[2] == "t",
        "rolcreatedb": r[3] == "t", "rolcreaterole": r[4] == "t",
        "rolinherit": r[5] == "t", "rolreplication": r[6] == "t",
        "rolconnlimit": int(r[7]), "rolvaliduntil": r[8],
        "member_of": [x for x in r[9].split(",") if x],
        # camelCase aliases the UI also reads
        "roleName": r[0], "isSuperuser": r[1] == "t",
        "members": [x for x in r[9].split(",") if x],
        "memberCount": len([x for x in r[9].split(",") if x]),
    } for r in rows]
    return {"source": "pg_roles", "available": True, "generated_at": _generated_at(), "roles": roles}


def build_privileges(database: str = "postgres", role: str | None = None,
                     schema: str | None = None) -> dict[str, Any]:
    predicates = ["table_schema not in ('pg_catalog','information_schema')"]
    if role:
        predicates.append(f"grantee = '{_literal(role)}'")
    if schema:
        predicates.append(f"table_schema = '{_literal(schema)}'")
    rows = S.sql(
        "select table_schema, table_name, grantee, grantor, privilege_type, is_grantable "
        "from information_schema.role_table_grants "
        f"where {' and '.join(predicates)} "
        "order by table_schema, table_name, grantee limit 500",
        dbname=database,
    )
    privileges = [{
        "table_schema": r[0], "table_name": r[1], "grantee": r[2], "grantor": r[3],
        "privilege_type": r[4], "is_grantable": r[5] == "t",
    } for r in rows]
    return {"source": "information_schema.role_table_grants", "available": True,
            "generated_at": _generated_at(), "database": database,
            "filters": {"role": role or None, "schema": schema or None},
            "privileges": privileges}


def build_hba() -> dict[str, Any]:
    rows = S.sql(
        "select line_number, coalesce(type,''), array_to_string(database,','), "
        "array_to_string(user_name,','), coalesce(address,''), coalesce(auth_method,''), "
        "coalesce(error::text,'') from pg_hba_file_rules order by line_number"
    )
    hba = [{
        "line_number": int(r[0]), "type": r[1], "database": r[2], "user_name": r[3],
        "address": r[4], "auth_method": r[5], "error": r[6] or None,
    } for r in rows]
    return {"source": "pg_hba_file_rules", "available": True, "generated_at": _generated_at(), "hba": hba}


def build_pods() -> dict[str, Any]:
    k8s_pods = S.pods()
    try:
        members = {m["name"]: m for m in _members(S.patroni_cluster())[0]}
    except S.SourceError:
        members = {}
    pod_rows = []
    for p in k8s_pods:
        mem = members.get(p["name"], {})
        pod_rows.append({
            "name": p["name"],
            "role": mem.get("role") or p["role"] or "—",
            "state": mem.get("state") or p["phase"],
            "lag": mem.get("lag", 0),
            "default_container": S.DB_CONTAINER if p["role"] in ("master", "replica") else "",
            "ready": p["ready"], "restarts": p["restarts"], "node": p["node"],
        })
    return {"source": "kubernetes + Patroni", "namespace": S.NS, "pods": pod_rows,
            "log_permission_needed": False}
