"""Loki log summary collector for Phase 1 snapshots."""
from __future__ import annotations

from typing import Any

from .. import loki
from .. import sources as S


def _sum_matrix(matrix: list[dict[str, Any]]) -> int:
    total = 0
    for series in matrix:
        for _, value in series.get("values", []):
            try:
                total += int(float(value))
            except (TypeError, ValueError):
                continue
    return total


def collect(range_minutes: int = 15) -> dict[str, Any]:
    end = loki.now_ns()
    start = end - range_minutes * 60 * loki.NS_PER_S
    warnings: list[str] = []
    values: dict[str, Any] = {}
    selector = f'{{namespace="{S.NS}"}}'
    try:
        values["log_error_count"] = _sum_matrix(
            loki.metric_range(f'count_over_time({selector} |~ "(?i)error|fatal|panic" [{range_minutes}m])', start, end)
        )
        values["log_warning_count"] = _sum_matrix(
            loki.metric_range(f'count_over_time({selector} |~ "(?i)warn|warning" [{range_minutes}m])', start, end)
        )
    except Exception as exc:
        warnings.append(f"loki summary unavailable: {exc}")
    return {
        "source": "loki",
        "available": bool(values),
        "values": values,
        "warnings": warnings,
    }
