"""Simple statistical baselines for Phase 3."""
from __future__ import annotations

from statistics import median, pstdev
from typing import Any


def summarize(rows: list[dict[str, float | None]]) -> dict[str, dict[str, float | None]]:
    if not rows:
        return {}
    out: dict[str, dict[str, float | None]] = {}
    names = sorted({k for row in rows for k in row})
    for name in names:
        vals = [row.get(name) for row in rows if row.get(name) is not None]
        if not vals:
            out[name] = {"median": None, "stddev": None, "mad": None}
            continue
        med = float(median(vals))
        deviations = [abs(v - med) for v in vals]
        out[name] = {
            "median": med,
            "stddev": float(pstdev(vals)) if len(vals) > 1 else 0.0,
            "mad": float(median(deviations)) if deviations else 0.0,
        }
    return out


def abnormal_features(current: dict[str, float | None], baseline: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, value in current.items():
        if value is None:
            continue
        spec = baseline.get(name) or {}
        med = spec.get("median")
        mad = spec.get("mad")
        if med is None or mad in (None, 0):
            continue
        robust_z = abs(value - med) / (1.4826 * mad)
        if robust_z >= 3:
            out.append({"feature": name, "value": value, "median": med, "mad": mad, "robust_z": round(robust_z, 3)})
    return out
