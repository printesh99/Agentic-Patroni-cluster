"""Build the Overview page payload (/api/v1/ui/overview/{id}) from live data."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import sources as S
from . import cluster_model
from . import pg_backups


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _capacity() -> dict[str, Any]:
    try:
        doc = S.kubectl_json([
            "-n", S.NS, "get", "pvc", "-l",
            f"postgres-operator.crunchydata.com/cluster={S.CLUSTER_NAME}",
        ])
        primary = S.kubectl_json(["-n", S.NS, "get", "pod", S.primary_pod()])
    except S.SourceError as exc:
        return {"available": False, "error": str(exc), "volumes": []}

    primary_claims = {
        volume.get("persistentVolumeClaim", {}).get("claimName")
        for volume in primary.get("spec", {}).get("volumes", [])
        if volume.get("persistentVolumeClaim", {}).get("claimName")
    }
    totals = {"primary_data_gib": 0.0, "replicated_data_gib": 0.0,
              "wal_gib": 0.0, "repository_gib": 0.0}
    volumes = []
    storage_classes = set()
    for item in doc.get("items", []):
        metadata = item.get("metadata", {})
        spec = item.get("spec", {})
        name = metadata.get("name", "")
        labels = metadata.get("labels", {}) or {}
        searchable = " ".join([name] + [str(value) for value in labels.values()]).lower()
        category = "wal" if "wal" in searchable else (
            "repository" if "repo" in searchable or "backrest" in searchable else "data"
        )
        capacity_gib = cluster_model._mem_gib(
            item.get("status", {}).get("capacity", {}).get("storage")
        )
        attached_to_primary = name in primary_claims
        storage_class = spec.get("storageClassName")
        if storage_class:
            storage_classes.add(storage_class)
        volumes.append({"name": name, "category": category,
                        "capacity_gib": capacity_gib,
                        "attached_to_primary": attached_to_primary,
                        "storage_class": storage_class})
        if category == "data":
            totals["replicated_data_gib"] += capacity_gib
            if attached_to_primary:
                totals["primary_data_gib"] += capacity_gib
        elif category == "wal":
            totals["wal_gib"] += capacity_gib
        else:
            totals["repository_gib"] += capacity_gib
    primary_data_available = totals["primary_data_gib"] > 0
    return {
        "available": bool(volumes),
        "primary_data_available": primary_data_available,
        "error": None if volumes else "no matching PVCs were returned",
        "source": "OpenShift PVC inventory + primary pod mounts",
        "storage_classes": sorted(storage_classes),
        "volumes": volumes,
        **{key: round(value, 2) for key, value in totals.items()},
    }


def _backup_metadata() -> dict[str, Any]:
    try:
        schedules = pg_backups.build_schedules()
        backups = pg_backups.build_backups()
    except S.SourceError as exc:
        return {
            "schedules": [], "schedules_available": False,
            "schedules_error": str(exc),
            "repository": {"available": False, "source": "pgbackrest",
                           "descriptor": None, "stanza": None},
        }
    repository = backups.get("repo", {})
    backup_error = backups.get("summary", {}).get("error")
    return {
        "schedules": schedules.get("schedules", []),
        "schedules_available": not bool(schedules.get("error")),
        "schedules_error": schedules.get("error"),
        "repository": {
            "available": bool(repository) and not bool(backup_error),
            "source": backups.get("source", "pgbackrest"),
            "descriptor": repository.get("uri"),
            "stanza": repository.get("stanza"),
            "error": backup_error,
        },
    }


def _settings() -> dict[str, Any]:
    rows = S.sql(
        "select name, setting, unit from pg_settings where name in "
        "('synchronous_commit','shared_buffers','max_wal_size','archive_mode',"
        "'wal_level','synchronous_standby_names')"
    )
    raw = {r[0]: (r[1], r[2] if len(r) > 2 else "") for r in rows}

    def to_gib(name: str) -> float | None:
        if name not in raw:
            return None
        val, unit = raw[name]
        try:
            n = float(val)
        except ValueError:
            return None
        mult = {"8kB": 8 / (1024**2), "kB": 1 / (1024**2), "MB": 1 / 1024,
                "GB": 1.0, "": 1 / (1024**3)}.get(unit, 1 / (1024**3))
        return round(n * mult, 2)

    return {
        "synchronous_commit": raw.get("synchronous_commit", ("", ""))[0],
        "wal_level": raw.get("wal_level", ("", ""))[0],
        "archive_mode": raw.get("archive_mode", ("", ""))[0],
        "synchronous_standby_names": raw.get("synchronous_standby_names", ("", ""))[0],
        "shared_buffers_gib": to_gib("shared_buffers"),
        "max_wal_size_gib": to_gib("max_wal_size"),
    }


def _connections() -> dict[str, int]:
    rows = S.sql(
        "select coalesce(state,'unknown'), count(*) from pg_stat_activity group by 1"
    )
    by_state = {r[0]: int(r[1]) for r in rows}
    return {
        "total": sum(by_state.values()),
        "active": by_state.get("active", 0),
        "idle": by_state.get("idle", 0),
        "idle_in_transaction": by_state.get("idle in transaction", 0),
    }


def _databases() -> list[dict[str, Any]]:
    rows = S.sql(
        "select datname, pg_database_size(datname) from pg_database "
        "where not datistemplate order by 2 desc"
    )
    out = []
    for name, size in rows:
        out.append({"datname": name, "size_gib": round(int(size) / (1024**3), 4)})
    return out


def _members(patroni: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None, int]:
    members = []
    leader = None
    timeline = 0
    for m in patroni.get("members", []):
        members.append({
            "name": m.get("name"),
            "role": m.get("role"),
            "state": m.get("state"),
            "lag": m.get("lag", 0) or 0,
            "replay_lag": m.get("replay_lag", 0) or 0,
            "lsn": m.get("lsn") or m.get("replay_lsn"),
            "timeline": m.get("timeline"),
            "host": m.get("host"),
            "port": m.get("port"),
        })
        if m.get("role") == "leader":
            leader = m.get("name")
            timeline = m.get("timeline", 0)
    return members, leader, timeline


def build_overview() -> dict[str, Any]:
    try:
        patroni = S.patroni_cluster()
        patroni_ok = True
    except S.SourceError:
        patroni, patroni_ok = {"members": []}, False

    members, leader, _tl = _members(patroni)
    lsn_row = S.sql_one(
        "select pg_current_wal_lsn()::text, pg_walfile_name(pg_current_wal_lsn())"
    )
    ver = S.sql_one("select current_setting('server_version')")
    mc = S.sql_one("select current_setting('max_connections')")
    max_conns = int(mc[0]) if mc and str(mc[0]).isdigit() else None
    dbsz = S.sql_one("select coalesce(sum(pg_database_size(datname)),0)::bigint "
                     "from pg_database where not datistemplate")
    total_db_bytes = int(dbsz[0]) if dbsz and dbsz[0] else 0
    summary = cluster_model.build_summary()
    capacity = _capacity()
    backup = _backup_metadata()
    generated_at = _generated_at()
    try:
        pgb_pods = [p for p in S.pods() if p["role"] == "pgbouncer"]
    except S.SourceError:
        pgb_pods = []

    version_str = f"PostgreSQL {ver[0]}" if ver else None
    return {
        "generated_at": generated_at,
        "freshness": {"status": "current", "observed_at": generated_at},
        "source": "live PostgreSQL + Patroni + OpenShift",
        "cluster": {
            "leader": leader,
            "patroni_ok": patroni_ok,
            "members": members,
            "scope": patroni.get("scope"),
        },
        "pg": {
            "version": version_str,
            "max_connections": max_conns,
            "connections": _connections(),
            "total_db_size_bytes": total_db_bytes,
            "current_lsn": lsn_row[0] if lsn_row else None,
            "current_wal_file": lsn_row[1] if lsn_row else None,
            "databases": _databases(),
            "settings": _settings(),
        },
        "config": {
            "cores": summary.get("cores"),
            "ram_gib": summary.get("ram_gib"),
            "compute_available": summary.get("compute_available", bool(summary.get("cores") or summary.get("ram_gib"))),
            "location": {
                "role": summary.get("role"),
                "region": summary.get("region"),
                "namespace": summary.get("namespace"),
            },
            "capacity": capacity,
        },
        "backup": backup,
        "pgbouncer": {
            "pods_ready": sum(1 for p in pgb_pods if p["ready_bool"]),
            "pods_total": len(pgb_pods),
        },
    }
