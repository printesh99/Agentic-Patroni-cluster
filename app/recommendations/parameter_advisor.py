"""Read-only PostgreSQL parameter advisor.

The rules are deliberately conservative. They recommend DBA review and dry-run
validation only; this module never applies a setting change.
"""
from __future__ import annotations

from typing import Any

from .. import sources as S

B = 1
KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB
UNIT_BYTES = {"": 1, "B": 1, "kB": KIB, "8kB": 8 * KIB, "MB": MIB, "GB": GIB}
UNIT_SECONDS = {"": 1, "ms": 0.001, "s": 1, "min": 60, "h": 3600}

PARAMETERS = [
    "checkpoint_timeout",
    "effective_cache_size",
    "log_min_duration_statement",
    "maintenance_work_mem",
    "max_connections",
    "max_parallel_workers",
    "max_parallel_workers_per_gather",
    "max_wal_size",
    "max_worker_processes",
    "random_page_cost",
    "shared_buffers",
    "track_io_timing",
    "wal_compression",
    "work_mem",
]


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _setting_bytes(row: dict[str, Any] | None) -> int | None:
    if not row:
        return None
    unit = row.get("unit") or ""
    if unit not in UNIT_BYTES:
        return None
    val = _float(row.get("setting"))
    if val is None:
        return None
    return int(val * UNIT_BYTES[unit])


def _setting_seconds(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    unit = row.get("unit") or ""
    if unit not in UNIT_SECONDS:
        return None
    val = _float(row.get("setting"))
    if val is None:
        return None
    return val * UNIT_SECONDS[unit]


def _format_bytes(num: int | float | None) -> str:
    if num is None:
        return ""
    n = int(num)
    if n >= GIB and n % GIB == 0:
        return f"{n // GIB}GB"
    if n >= GIB:
        return f"{round(n / MIB)}MB"
    if n >= MIB:
        return f"{max(1, round(n / MIB))}MB"
    if n >= KIB:
        return f"{max(1, round(n / KIB))}kB"
    return str(n)


def _display(row: dict[str, Any] | None) -> str:
    if not row:
        return "unknown"
    as_bytes = _setting_bytes(row)
    if as_bytes is not None and row.get("name") not in {"max_connections", "max_worker_processes", "max_parallel_workers", "max_parallel_workers_per_gather"}:
        return _format_bytes(as_bytes)
    unit = row.get("unit") or ""
    return f"{row.get('setting', '')}{unit}" if unit else str(row.get("setting", ""))


def _apply_mode(row: dict[str, Any] | None) -> str:
    if not row:
        return "validate"
    context = str(row.get("context") or "").lower()
    if context == "postmaster":
        return "restart"
    if context in {"sighup", "superuser", "backend", "superuser-backend"}:
        return "reload"
    return "validate"


def _fetch_settings() -> dict[str, dict[str, Any]]:
    names = ",".join("'" + p.replace("'", "''") + "'" for p in PARAMETERS)
    rows = S.sql(
        "select name, setting, coalesce(unit,''), context, vartype, source, "
        "pending_restart::text from pg_settings where name in (" + names + ") order by name"
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[r[0]] = {
            "name": r[0],
            "setting": r[1],
            "unit": r[2],
            "context": r[3],
            "vartype": r[4],
            "source": r[5],
            "pending_restart": str(r[6]).lower() in {"t", "true", "1", "yes"},
        }
    return out


def build_response(ram_gib: float | None = None, cpu_cores: float | None = None) -> dict[str, Any]:
    try:
        settings = _fetch_settings()
    except S.SourceError as exc:
        return {
            "available": False,
            "source": "pg_settings",
            "reason": str(exc),
            "recommendations": [],
            "summary": {"total": 0, "advice": 0, "ok": 0, "unknown": 0},
            "capacity": {"capacity_known": False, "ram_gib": ram_gib, "cpu_cores": cpu_cores},
        }

    recommendations: list[dict[str, Any]] = []

    def add(name: str, category: str, current: str, recommended: str, status: str,
            rationale: str, row: dict[str, Any] | None, confidence: float = 0.72,
            current_bytes: int | None = None, recommended_bytes: int | None = None) -> None:
        recommendations.append({
            "name": name,
            "parameter": name,
            "category": category,
            "current": current,
            "recommended": recommended,
            "status": status,
            "apply": _apply_mode(row),
            "rationale": rationale,
            "confidence": confidence,
            "current_bytes": current_bytes,
            "recommended_bytes": recommended_bytes,
            "source": "pg_settings",
        })

    def add_bool(name: str, expected: str, category: str, rationale: str, confidence: float = 0.78) -> None:
        row = settings.get(name)
        if not row:
            add(name, category, "unknown", expected, "unknown", rationale, row, confidence)
            return
        current = str(row.get("setting") or "").lower()
        status = "ok" if current == expected.lower() else "advice"
        add(name, category, _display(row), expected, status, rationale, row, confidence)

    def add_min_seconds(name: str, min_seconds: int, recommended: str, category: str, rationale: str) -> None:
        row = settings.get(name)
        if not row:
            add(name, category, "unknown", recommended, "unknown", rationale, row)
            return
        current_seconds = _setting_seconds(row)
        status = "ok" if current_seconds is not None and current_seconds >= min_seconds else "advice"
        add(name, category, _display(row), recommended, status, rationale, row)

    def add_max_numeric(name: str, max_value: float, recommended: str, category: str, rationale: str, confidence: float = 0.65) -> None:
        row = settings.get(name)
        if not row:
            add(name, category, "unknown", recommended, "unknown", rationale, row, confidence)
            return
        current = _float(row.get("setting"))
        status = "ok" if current is not None and current <= max_value else "advice"
        add(name, category, _display(row), recommended, status, rationale, row, confidence)

    def add_memory(name: str, target_bytes: int, category: str, rationale: str, confidence: float = 0.74) -> None:
        row = settings.get(name)
        current_bytes = _setting_bytes(row)
        recommended = _format_bytes(target_bytes)
        if current_bytes is None:
            add(name, category, _display(row), recommended, "unknown", rationale, row, confidence, current_bytes, target_bytes)
            return
        lower = target_bytes * 0.85
        upper = target_bytes * 1.15
        status = "ok" if lower <= current_bytes <= upper else "advice"
        add(name, category, _display(row), recommended, status, rationale, row, confidence, current_bytes, target_bytes)

    def add_integer(name: str, target: int, category: str, rationale: str, confidence: float = 0.70) -> None:
        row = settings.get(name)
        if not row:
            add(name, category, "unknown", str(target), "unknown", rationale, row, confidence)
            return
        current = _int(row.get("setting"))
        status = "ok" if current >= target else "advice"
        add(name, category, _display(row), str(target), status, rationale, row, confidence)

    add_bool("track_io_timing", "on", "observability", "Enables IO timing evidence for slow query and storage diagnosis.")
    add_bool("wal_compression", "on", "wal", "Reduces WAL volume for many update-heavy workloads; validate CPU headroom before changing.")
    add_min_seconds("checkpoint_timeout", 900, "15min", "wal", "Longer checkpoint windows usually reduce checkpoint pressure on busy OLTP systems.")
    add_memory("max_wal_size", 8 * GIB, "wal", "A larger WAL budget can reduce checkpoint frequency during write spikes.", 0.68)
    add_max_numeric("random_page_cost", 1.5, "1.1", "planner", "Lower random_page_cost is commonly appropriate for SSD-backed storage after plan validation.", 0.58)

    log_row = settings.get("log_min_duration_statement")
    if log_row:
        cur = _int(log_row.get("setting"), -1)
        status = "ok" if 0 <= cur <= 1000 else "advice"
        add("log_min_duration_statement", "observability", _display(log_row), "1000ms", status,
            "Captures slow SQL evidence for tuning without logging every statement.", log_row, 0.76)

    ram = _float(ram_gib)
    cpu = _float(cpu_cores)
    ram_bytes = int(ram * GIB) if ram and ram > 0 else None
    if ram_bytes:
        add_memory("shared_buffers", max(128 * MIB, min(int(ram_bytes * 0.25), 32 * GIB)),
                   "memory", "Size shared buffers from container memory, then validate cache hit rate and checkpoint behavior.")
        add_memory("effective_cache_size", max(512 * MIB, int(ram_bytes * 0.70)),
                   "planner", "Planner cache estimate should reflect OS cache plus shared buffers available to PostgreSQL.")
        add_memory("maintenance_work_mem", max(64 * MIB, min(int(ram_bytes * 0.05), 2 * GIB)),
                   "maintenance", "Improves VACUUM, CREATE INDEX, and maintenance operations within a bounded memory budget.")
        max_connections = _int(settings.get("max_connections", {}).get("setting"), 100)
        work_mem_target = max(4 * MIB, min(128 * MIB, int((ram_bytes * 0.10) / max(1, max_connections))))
        add_memory("work_mem", work_mem_target, "memory", "Per-operation memory should be sized against max connection concurrency.", 0.66)

    if cpu and cpu > 0:
        cores = int(cpu)
        add_integer("max_worker_processes", max(8, cores), "parallelism", "Keep worker process budget aligned with CPU allocation.")
        add_integer("max_parallel_workers", max(2, cores), "parallelism", "Allow PostgreSQL to use available CPU for parallel-safe plans.")
        add_integer("max_parallel_workers_per_gather", max(2, min(4, cores // 2 or 1)),
                    "parallelism", "Bound per-query parallelism so one query cannot consume the full CPU allocation.", 0.63)

    summary = {"total": len(recommendations), "advice": 0, "ok": 0, "unknown": 0}
    for rec in recommendations:
        summary[rec["status"]] = summary.get(rec["status"], 0) + 1

    return {
        "available": True,
        "source": "pg_settings + conservative tuning rules",
        "recommendations": recommendations,
        "summary": summary,
        "capacity": {"capacity_known": bool(ram_bytes or cpu), "ram_gib": ram_gib, "cpu_cores": cpu_cores},
    }
