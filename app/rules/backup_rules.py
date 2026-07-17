from __future__ import annotations

from typing import Any

from .common import finding, present, threshold


def evaluate(features: dict[str, Any], _snapshot: dict[str, Any], defaults: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    failed = features.get("archive_failed_count")
    if present(failed) and failed > 0:
        out.append(finding("ARCHIVE_FAILED", "critical", "backup",
                           "Current WAL archive failures are present",
                           "archive_failed_count", failed, 0, "runbook_pgbackrest_archive_failure"))
    duration = features.get("backup_duration_minutes")
    if present(duration):
        crit = threshold(defaults, "backup_duration_minutes", "critical", 120)
        warn = threshold(defaults, "backup_duration_minutes", "warning", 60)
        if duration >= crit:
            out.append(finding("BACKUP_DURATION_CRITICAL", "critical", "backup",
                               "Backup duration is above critical threshold",
                               "backup_duration_minutes", duration, crit, "runbook_pgbackrest_troubleshooting"))
        elif duration >= warn:
            out.append(finding("BACKUP_DURATION_WARNING", "warning", "backup",
                               "Backup duration is above warning threshold",
                               "backup_duration_minutes", duration, warn, "runbook_pgbackrest_troubleshooting"))
    return out
