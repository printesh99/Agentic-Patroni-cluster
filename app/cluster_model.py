"""Build the UI's cluster-descriptor object from live cluster facts."""
from __future__ import annotations

import os
from typing import Any

from . import sources as S


def _label(*names: str, default: str = "") -> str:
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return default


def _cluster_role(default: str = "LIVE") -> str:
    explicit = _label("PGC_CLUSTER_ROLE", "CLUSTER_ROLE", default="")
    if explicit:
        return explicit
    name = S.CLUSTER_NAME.lower()
    if name.startswith("uat"):
        return "UAT"
    if name.startswith("dr"):
        return "DR"
    if name.startswith("prod"):
        return "PROD"
    return default


def _cpu_cores_from_limit(limit: str | None) -> float:
    if not limit:
        return 0.0
    if limit.endswith("m"):
        return round(int(limit[:-1]) / 1000, 2)
    return float(limit)


def _mem_gib(mem: str | None) -> float:
    if not mem:
        return 0.0
    units = {"Ki": 1 / (1024**2), "Mi": 1 / 1024, "Gi": 1.0, "Ti": 1024.0}
    for suffix, factor in units.items():
        if mem.endswith(suffix):
            return round(float(mem[: -len(suffix)]) * factor, 2)
    return round(int(mem) / (1024**3), 2)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _db_resources() -> tuple[float, float]:
    """(cores, ram_gib) from the primary's database container limits."""
    try:
        pod = S.primary_pod()
        doc = S.kubectl_json(["-n", S.NS, "get", "pod", pod])
        for c in doc["spec"]["containers"]:
            if c["name"] == S.DB_CONTAINER:
                lim = c.get("resources", {}).get("limits", {})
                return _cpu_cores_from_limit(lim.get("cpu")), _mem_gib(lim.get("memory"))
    except (S.SourceError, KeyError):
        pass
    return 0.0, 0.0


def _storage_gib() -> float:
    """Sum of bound PVC capacities for the cluster, in GiB."""
    try:
        doc = S.kubectl_json([
            "-n", S.NS, "get", "pvc", "-l",
            f"postgres-operator.crunchydata.com/cluster={S.CLUSTER_NAME}",
        ])
    except S.SourceError:
        return 0.0
    total = 0.0
    for item in doc.get("items", []):
        cap = item.get("status", {}).get("capacity", {}).get("storage")
        total += _mem_gib(cap)
    return round(total, 1)


def _pg_version() -> str:
    try:
        ver = S.sql_one("select current_setting('server_version')")
        return f"PostgreSQL {ver[0]}" if ver else "PostgreSQL"
    except S.SourceError:
        return "PostgreSQL"


def _direct_sql_summary() -> dict[str, Any]:
    pg_version = _pg_version()
    db_bytes = _int((S.sql_one(
        "select coalesce(sum(pg_database_size(datname)),0)::bigint "
        "from pg_database where not datistemplate"
    ) or ["0"])[0])
    db_count = _int((S.sql_one(
        "select count(*) from pg_database where not datistemplate"
    ) or ["0"])[0])
    total_conns = _int((S.sql_one("select count(*) from pg_stat_activity") or ["0"])[0])
    max_conns = _int((S.sql_one("select current_setting('max_connections')") or ["300"])[0], 300)
    used_gib = round(db_bytes / (1024**3), 2)
    return {
        "id": S.CLUSTER_ID,
        "name": S.CLUSTER_NAME,
        "label": _label("PGC_CLUSTER_LABEL", "CLUSTER_LABEL", default=_cluster_role("UNKNOWN")),
        "role": _cluster_role("UNKNOWN"),
        "namespace": S.NS,
        "region": _label("PGC_REGION_LABEL", "REGION_LABEL", default=f"OpenShift namespace {S.NS}"),
        "pgVersion": pg_version,
        "pg_version": pg_version,
        "cores": None,
        "ram_gib": None,
        "ramGiB": None,
        "compute_available": False,
        "total_storage_gib": None,
        "totalStorageGiB": None,
        "storage_available": False,
        "serverState": "Healthy",
        "ha": "Unavailable (OpenShift inventory unavailable)",
        "instances": 1,
        "leader": None,
        "pods_ready": 1,
        "pods_total": 1,
        "activeConns": total_conns,
        "maxConns": max_conns,
        "database_count": db_count,
        "total_db_size_bytes": db_bytes,
    }


def build_summary() -> dict[str, Any]:
    """Compact descriptor for the cluster list / picker."""
    try:
        pods = S.pods()
    except S.SourceError:
        return _direct_sql_summary()

    pg = [p for p in pods if p["role"] in ("master", "replica")]
    primary = next((p for p in pods if p["role"] == "master"), None)
    cores, ram = _db_resources()
    pg_version = _pg_version()
    storage = _storage_gib()

    healthy = primary is not None and all(p["ready_bool"] for p in pg)
    return {
        "id": S.CLUSTER_ID,
        "name": S.CLUSTER_NAME,
        "label": _label("PGC_CLUSTER_LABEL", "CLUSTER_LABEL", default=_cluster_role()),
        "role": _cluster_role(),
        "namespace": S.NS,
        "region": _label("PGC_REGION_LABEL", "REGION_LABEL", default=f"OpenShift namespace {S.NS}"),
        "pgVersion": pg_version,
        "pg_version": pg_version,
        "cores": cores or None,
        "ram_gib": ram or None,
        "ramGiB": ram or None,
        "compute_available": bool(cores or ram),
        "total_storage_gib": storage,
        "totalStorageGiB": storage,
        "storage_available": storage > 0,
        "serverState": "Healthy" if healthy else "Degraded",
        "ha": "Enabled (synchronous)",
        "instances": len(pg),
        "leader": primary["name"] if primary else None,
        "pods_ready": sum(1 for p in pg if p["ready_bool"]),
        "pods_total": len(pg),
    }
