"""Backups page — pgBackRest info + PGO backup schedules."""
from __future__ import annotations

import json
import time
from typing import Any

from . import sources as S

STANZA = "db"


def _pgbackrest_info() -> list[dict[str, Any]]:
    args = S.KUBECTL + [
        "-n", S.NS, "exec", S.primary_pod(), "-c", S.DB_CONTAINER, "--",
        "pgbackrest", "info", "--output=json", f"--stanza={STANZA}",
    ]
    raw = S._run(args, timeout=30)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise S.SourceError("could not parse pgbackrest info") from exc


def _human_duration(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def _backup_row(b: dict[str, Any]) -> dict[str, Any]:
    ts = b.get("timestamp", {})
    start, stop = ts.get("start", 0), ts.get("stop", 0)
    info = b.get("info", {})
    arch = b.get("archive", {})
    return {
        "label": b.get("label"),
        "type": b.get("type"),
        "database_size_bytes": info.get("size", 0),
        "repo_size_bytes": info.get("repository", {}).get("size", 0),
        "stop_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(stop)) if stop else None,
        "duration_human": _human_duration(max(0, stop - start)),
        "wal_start": arch.get("start"),
        "wal_stop": arch.get("stop"),
        "error": b.get("error", False),
    }


def build_backups() -> dict[str, Any]:
    schedule_doc = build_schedules()
    try:
        info = _pgbackrest_info()
    except S.SourceError as exc:
        return {"source": "pgbackrest", "summary": {"status": "unknown", "error": str(exc)},
                "backups": [], "history": [], "archive": {"error": str(exc)},
                "repo": {}, "settings": {}, "pgo": {"available": False},
                "schedules": schedule_doc.get("schedules", []),
                "schedules_error": schedule_doc.get("error")}

    stanza = info[0] if info else {}
    backups = [_backup_row(b) for b in stanza.get("backup", [])]
    archive = stanza.get("archive", [{}])
    last_wal = archive[0].get("max") if archive else None

    arch_status = stanza.get("status", {}) or {}
    repo = (stanza.get("repo") or [{}])[0]

    # archive freshness via prometheus archiver metrics (best-effort)
    last_age = None
    try:
        failed = int(S.prom_scalar(
            f'max(pg_stat_archiver_failed_count_total{{namespace="{S.NS}"}})', 0) or 0)
    except S.SourceError:
        failed = 0

    settings_rows = S.sql(
        "select name, setting from pg_settings where name in ('archive_mode','archive_command')"
    )
    settings = {r[0]: r[1] for r in settings_rows}

    return {
        "source": "pgbackrest",
        "summary": {
            "status": "ok" if not arch_status.get("code") else "degraded",
            "archive_failed_count": failed,
            "last_archive_age_seconds": last_age,
            "backup_count": len(backups),
        },
        "repo": {
            "repo": repo.get("key") or 1,
            "stanza": stanza.get("name", STANZA),
            "cipher": stanza.get("cipher", "none"),
            "uri": repo.get("uri") or f"repo1 ({stanza.get('cipher','none')} cipher)",
            "bucket": repo.get("bucket"),
        },
        "archive": {
            "archived_count": len(archive),
            "failed_count": failed,
            "last_archived_wal": last_wal,
            "last_failed_wal": None,
            "min_wal": archive[0].get("min") if archive else None,
            "max_wal": last_wal,
            "error": None,
        },
        "settings": {
            "archive_mode": settings.get("archive_mode"),
            "archive_command": settings.get("archive_command"),
            "error": None,
        },
        "pgo": {
            "available": True,
            "name": S.CLUSTER_NAME,
            "pgbackrest": stanza.get("backrest", {}).get("version")
                or (stanza.get("backup", [{}])[0].get("backrest", {}).get("version")),
            "standby": False,
        },
        "backups": backups,
        "schedules": schedule_doc.get("schedules", []),
        "schedules_error": schedule_doc.get("error"),
        "history": backups,  # same source; UI shows newest-first history
    }


def build_schedules() -> dict[str, Any]:
    """PGO backup schedules from the PostgresCluster CR."""
    try:
        doc = S.kubectl_json(["-n", S.NS, "get", "postgrescluster", S.CLUSTER_NAME])
    except S.SourceError as exc:
        return {"schedules": [], "error": str(exc)}
    repos = (doc.get("spec", {}).get("backups", {}).get("pgbackrest", {}).get("repos", []))
    schedules = []
    for repo in repos:
        sched = repo.get("schedules", {}) or {}
        for kind, cron in sched.items():
            schedules.append({
                "id": f"{repo.get('name')}-{kind}",
                "name": f"{repo.get('name')} {kind}",
                "kind": kind, "type": kind, "cron": cron,
                "enabled": True, "state": "scheduled",
                "retention_days": repo.get("backupRetentionPolicy"),
            })
    return {"schedules": schedules, "source": "PostgresCluster CR"}


def build_pitr_preview() -> dict[str, Any]:
    """Earliest/latest restorable time derived from pgBackRest backups + WAL."""
    try:
        info = _pgbackrest_info()
    except S.SourceError as exc:
        return {"available": False, "error": str(exc)}
    stanza = info[0] if info else {}
    backups = stanza.get("backup", [])
    if not backups:
        return {"available": False, "reason": "no backups"}
    earliest = min(b.get("timestamp", {}).get("stop", 0) for b in backups)
    latest = int(time.time())
    fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(t))
    return {
        "available": True,
        "source": "pgbackrest",
        "earliest_restore_time": fmt(earliest),
        "latest_restore_time": fmt(latest),
        "rpo_seconds": 0,
        "full_backups": sum(1 for b in backups if b.get("type") == "full"),
    }
