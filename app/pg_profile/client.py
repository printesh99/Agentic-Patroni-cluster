"""Version-aware client for the pg_profile central repository."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any

from sqlalchemy import text

from ..db.session import engine
from .config import PgProfileConfigError, settings
from .security import sanitize_error


class PgProfileUnavailable(RuntimeError):
    pass


class PgProfileUnsupported(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtensionStatus:
    available: bool
    enabled: bool
    supported: bool
    version: str | None
    schema: str
    functions: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True)
class SampleResult:
    ok: bool
    server_name: str
    sample_id: int | None
    sample_time: datetime | None
    duration_ms: int
    status: str
    error_code: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ReportResult:
    ok: bool
    html: str | None
    duration_ms: int
    report_type: str
    error_code: str | None = None
    error: str | None = None


def _schema() -> str:
    # Validated once in config; never accept a request-controlled identifier.
    return settings.schema


def _set_timeouts(conn, seconds: int) -> None:
    conn.execute(text("SELECT set_config('statement_timeout', :timeout, true)"), {"timeout": f"{seconds}s"})
    conn.execute(text("SELECT set_config('lock_timeout', :timeout, true)"),
                 {"timeout": f"{settings.collection_lock_timeout_seconds}s"})
    conn.execute(text("SELECT set_config('application_name', 'pg-enterprise-console/pg_profile', true)"))


def _functions(conn) -> dict[str, list[str]]:
    rows = conn.execute(text(
        "SELECT p.proname, pg_get_function_identity_arguments(p.oid) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=:schema ORDER BY p.proname"
    ), {"schema": _schema()}).all()
    out: dict[str, list[str]] = {}
    for name, args in rows:
        out.setdefault(str(name), []).append(str(args or ""))
    return out


def _require_function(conn, name: str) -> None:
    if name not in _functions(conn):
        raise PgProfileUnsupported(f"installed pg_profile does not expose {name}()")


def _argument_count(signature: str) -> int:
    signature = signature.strip()
    return 0 if not signature else signature.count(",") + 1


def _require_arity(conn, name: str, arity: int, expected_types: tuple[str, ...] = ()) -> None:
    signatures = _functions(conn).get(name, [])
    compatible = []
    for signature in signatures:
        if _argument_count(signature) != arity:
            continue
        parts = [part.strip().lower() for part in signature.split(",")] if signature.strip() else []
        if expected_types and not all(re.search(rf"\b{re.escape(expected)}\b", part)
                                      for part, expected in zip(parts, expected_types)):
            continue
        compatible.append(signature)
    if not compatible:
        raise PgProfileUnsupported(
            f"installed pg_profile {name}() signature is not compatible with the validated adapter"
        )


def _read_only(conn) -> None:
    conn.execute(text("SET TRANSACTION READ ONLY"))


def extension_status() -> ExtensionStatus:
    if not settings.enabled:
        return ExtensionStatus(False, False, False, None, _schema(), (), "PGPROFILE_ENABLED is false")
    if engine.dialect.name != "postgresql":
        return ExtensionStatus(False, True, False, None, _schema(), (), "pg_profile requires PostgreSQL metadata storage")
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT e.extversion, n.nspname FROM pg_extension e "
                "JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_profile'"
            )).first()
            if not row:
                return ExtensionStatus(False, True, False, None, _schema(), (), "pg_profile extension is not installed")
            if row[1] != _schema():
                return ExtensionStatus(False, True, False, str(row[0]), _schema(), (), "extension schema does not match PGPROFILE_SCHEMA")
            version = str(row[0])
            match = re.match(r"^(\d+)\.(\d+)", version)
            if not match or int(match.group(1)) != 4 or int(match.group(2)) < 10:
                return ExtensionStatus(False, True, False, version, _schema(), (),
                                       "installed pg_profile version is outside the validated 4.10 adapter range")
            funcs = _functions(conn)
            required = {"show_servers", "show_samples", "take_sample", "get_report"}
            missing = sorted(required - funcs.keys())
            return ExtensionStatus(not missing, True, not missing, version, _schema(),
                                   tuple(sorted(funcs)), None if not missing else "missing functions: " + ", ".join(missing))
    except Exception as exc:
        return ExtensionStatus(False, True, False, None, _schema(), (), sanitize_error(exc))


def _require_available() -> None:
    status = extension_status()
    if not status.available:
        raise PgProfileUnavailable(status.reason or "pg_profile unavailable")


def _credential(reference: str) -> dict[str, str]:
    raw: str
    if reference.startswith("env:"):
        name = reference[4:]
        if not name or not name.replace("_", "").isalnum():
            raise PgProfileConfigError("invalid credential environment reference")
        raw = os.getenv(name, "")
    elif reference.startswith("file:"):
        relative = reference[5:]
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise PgProfileConfigError("invalid credential file reference")
        path = (settings.secret_mount_dir / relative).resolve()
        root = settings.secret_mount_dir.resolve()
        if root not in path.parents:
            raise PgProfileConfigError("credential file escapes secret mount")
        raw = path.read_text(encoding="utf-8")
    else:
        raise PgProfileConfigError("unsupported credential reference")
    if not raw:
        raise PgProfileUnavailable("credential reference is unavailable")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PgProfileConfigError("credential reference must contain JSON") from exc
    if not isinstance(value, dict) or not value.get("username") or not value.get("password"):
        raise PgProfileConfigError("credential JSON requires username and password")
    return {str(k): str(v) for k, v in value.items() if k in {"username", "password", "sslrootcert"}}


def build_remote_conninfo(server: Any) -> str:
    if settings.require_ssl and server.sslmode not in settings.allowed_sslmodes:
        raise PgProfileConfigError("server sslmode is not allowed")
    cred = _credential(server.credential_reference)
    try:
        from psycopg.conninfo import make_conninfo
    except ImportError as exc:
        raise PgProfileUnavailable("psycopg conninfo support unavailable") from exc
    values = {
        "host": server.endpoint_host, "port": server.endpoint_port,
        "dbname": server.database_name, "user": cred["username"], "password": cred["password"],
        "sslmode": server.sslmode, "application_name": "pg_profile_collector",
        "connect_timeout": min(30, settings.sample_timeout_seconds),
        "options": f"-c statement_timeout={settings.sample_timeout_seconds * 1000} -c lock_timeout={settings.collection_lock_timeout_seconds * 1000}",
    }
    if cred.get("sslrootcert"):
        values["sslrootcert"] = cred["sslrootcert"]
    return make_conninfo(**values)


def list_registered_servers() -> list[dict[str, Any]]:
    _require_available()
    with engine.begin() as conn:
        _read_only(conn)
        _set_timeouts(conn, settings.sample_timeout_seconds)
        _require_arity(conn, "show_servers", 0)
        rows = conn.execute(text(f'SELECT * FROM "{_schema()}".show_servers()')).mappings().all()
    return [{k: v for k, v in dict(row).items() if "connstr" not in k.lower() and "password" not in k.lower()} for row in rows]


def register_server(server: Any) -> dict[str, Any]:
    _require_available()
    conninfo = build_remote_conninfo(server)
    with engine.begin() as conn:
        _set_timeouts(conn, settings.sample_timeout_seconds)
        _require_arity(conn, "create_server", 5, ("name", "text", "boolean", "integer", "text"))
        conn.execute(text(
            f'SELECT "{_schema()}".create_server(CAST(:name AS name), :conninfo, :enabled, :retention, :description)'
        ), {"name": server.server_name, "conninfo": conninfo, "enabled": bool(server.enabled),
            "retention": settings.retention_days,
            "description": f"{server.environment}/{server.region or '-'}/{server.dc or '-'} {server.cluster_name}"})
    return {"ok": True, "server_name": server.server_name, "registered": True}


def update_server(server: Any, update_connection: bool = True) -> dict[str, Any]:
    _require_available()
    with engine.begin() as conn:
        _set_timeouts(conn, settings.sample_timeout_seconds)
        if update_connection:
            _require_arity(conn, "set_server_connstr", 2, ("name", "text"))
            conn.execute(text(f'SELECT "{_schema()}".set_server_connstr(CAST(:name AS name), :conninfo)'),
                         {"name": server.server_name, "conninfo": build_remote_conninfo(server)})
        fn = "enable_server" if server.enabled else "disable_server"
        _require_arity(conn, fn, 1, ("name",))
        conn.execute(text(f'SELECT "{_schema()}".{fn}(CAST(:name AS name))'), {"name": server.server_name})
    return {"ok": True, "server_name": server.server_name, "enabled": bool(server.enabled)}


def disable_server(server_name: str) -> dict[str, Any]:
    _require_available()
    with engine.begin() as conn:
        _set_timeouts(conn, settings.sample_timeout_seconds)
        _require_arity(conn, "disable_server", 1, ("name",))
        conn.execute(text(f'SELECT "{_schema()}".disable_server(CAST(:name AS name))'), {"name": server_name})
    return {"ok": True, "server_name": server_name, "enabled": False}


def list_samples(server_name: str, days: int | None = None) -> list[dict[str, Any]]:
    _require_available()
    with engine.begin() as conn:
        _read_only(conn)
        _set_timeouts(conn, settings.sample_timeout_seconds)
        _require_arity(conn, "show_samples", 2, ("name", "integer"))
        stmt = text(f'SELECT * FROM "{_schema()}".show_samples(CAST(:name AS name), :days) ORDER BY sample_time DESC')
        params = {"name": server_name, "days": max(1, min(days or settings.retention_days, settings.retention_days))}
        return [dict(row) for row in conn.execute(stmt, params).mappings().all()]


def take_sample(server_name: str, skip_sizes: bool = True) -> SampleResult:
    import time
    _require_available()
    started = time.monotonic()
    try:
        with engine.begin() as conn:
            _set_timeouts(conn, settings.sample_timeout_seconds)
            _require_arity(conn, "take_sample", 2, ("name", "boolean"))
            row = conn.execute(text(
                f'SELECT * FROM "{_schema()}".take_sample(CAST(:name AS name), CAST(:skip AS boolean))'
            ), {"name": server_name, "skip": skip_sizes}).mappings().first()
        elapsed = int((time.monotonic() - started) * 1000)
        result_text = str((row or {}).get("result") or "OK")
        if result_text.upper() != "OK":
            return SampleResult(False, server_name, None, None, elapsed, "FAILED", "COLLECTION_FAILED", sanitize_error(result_text))
        samples = list_samples(server_name, days=2)
        latest = samples[0] if samples else {}
        return SampleResult(True, server_name, latest.get("sample"), latest.get("sample_time"), elapsed, "SUCCEEDED")
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return SampleResult(False, server_name, None, None, elapsed, "FAILED", type(exc).__name__.upper(), sanitize_error(exc))


def verify_server(server_name: str) -> SampleResult:
    """Use the documented lightweight sample as the remote connectivity check."""
    return take_sample(server_name, skip_sizes=True)


def find_samples_for_time_range(server_name: str, start: datetime, end: datetime) -> tuple[dict, dict] | None:
    rows = sorted(list_samples(server_name, days=min(settings.retention_days, max(2, (end - start).days + 2))),
                  key=lambda r: r["sample_time"])
    before = [r for r in rows if r["sample_time"] <= start]
    after = [r for r in rows if r["sample_time"] >= end]
    if not before or not after:
        return None
    return before[-1], after[0]


def generate_regular_report(server_name: str, start_id: int, end_id: int) -> ReportResult:
    import time
    _require_available()
    started = time.monotonic()
    try:
        with engine.begin() as conn:
            _read_only(conn)
            _set_timeouts(conn, settings.report_timeout_seconds)
            _require_arity(conn, "get_report", 5, ("name", "integer", "integer", "text", "boolean"))
            html = conn.execute(text(
                f'SELECT "{_schema()}".get_report(CAST(:name AS name), :start_id, :end_id, :description, false)'
            ), {"name": server_name, "start_id": start_id, "end_id": end_id,
                "description": "Generated by PostgreSQL Enterprise Monitoring Console"}).scalar_one()
        return ReportResult(True, str(html), int((time.monotonic() - started) * 1000), "REGULAR")
    except Exception as exc:
        return ReportResult(False, None, int((time.monotonic() - started) * 1000), "REGULAR",
                            type(exc).__name__.upper(), sanitize_error(exc))


def generate_diff_report(server_name: str, start1: int, end1: int, start2: int, end2: int) -> ReportResult:
    import time
    _require_available()
    started = time.monotonic()
    try:
        with engine.begin() as conn:
            _read_only(conn)
            _set_timeouts(conn, settings.report_timeout_seconds)
            _require_arity(conn, "get_diffreport", 7,
                           ("name", "integer", "integer", "integer", "integer", "text", "boolean"))
            html = conn.execute(text(
                f'SELECT "{_schema()}".get_diffreport(CAST(:name AS name), :s1, :e1, :s2, :e2, :description, false)'
            ), {"name": server_name, "s1": start1, "e1": end1, "s2": start2, "e2": end2,
                "description": "Generated by PostgreSQL Enterprise Monitoring Console"}).scalar_one()
        return ReportResult(True, str(html), int((time.monotonic() - started) * 1000), "DIFF")
    except Exception as exc:
        return ReportResult(False, None, int((time.monotonic() - started) * 1000), "DIFF",
                            type(exc).__name__.upper(), sanitize_error(exc))


def repository_size() -> int | None:
    if not settings.enabled or engine.dialect.name != "postgresql":
        return None
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            return int(conn.execute(text(
                "SELECT COALESCE(sum(pg_total_relation_size(c.oid)),0) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema"
            ), {"schema": _schema()}).scalar() or 0)
    except Exception:
        return None


def collection_health() -> dict[str, Any]:
    status = extension_status()
    return {"available": status.available, "enabled": status.enabled, "supported": status.supported,
            "version": status.version, "schema": status.schema, "reason": status.reason,
            "repository_size_bytes": repository_size()}


def apply_retention(server_name: str, days: int, dry_run: bool = True) -> dict[str, Any]:
    _require_available()
    days = max(1, min(days, 3650))
    if dry_run:
        return {"ok": True, "dry_run": True, "server_name": server_name, "max_sample_age_days": days,
                "note": "pg_profile removes expired internal samples during a supported take_sample call"}
    with engine.begin() as conn:
        _set_timeouts(conn, settings.sample_timeout_seconds)
        _require_arity(conn, "set_server_max_sample_age", 2, ("name", "integer"))
        conn.execute(text(
            f'SELECT "{_schema()}".set_server_max_sample_age(CAST(:name AS name), :days)'
        ), {"name": server_name, "days": days})
    return {"ok": True, "dry_run": False, "server_name": server_name, "max_sample_age_days": days}
