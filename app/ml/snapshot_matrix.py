"""Snapshot-to-matrix helpers for metric ML models."""
from __future__ import annotations

from typing import Any

from ..db.models import ClusterHealthSnapshot
from .features import FEATURE_FIELDS


def row_to_features(row: ClusterHealthSnapshot) -> dict[str, float | None]:
    return {name: _num(getattr(row, name, None)) for name in FEATURE_FIELDS}


def usable_feature_names(rows: list[ClusterHealthSnapshot]) -> list[str]:
    names: list[str] = []
    for name in FEATURE_FIELDS:
        vals = [getattr(row, name, None) for row in rows]
        if any(v is not None for v in vals):
            names.append(name)
    return names


def matrix(rows: list[ClusterHealthSnapshot], feature_names: list[str]) -> list[list[float]]:
    means: dict[str, float] = {}
    for name in feature_names:
        vals = [_num(getattr(row, name, None)) for row in rows]
        present = [v for v in vals if v is not None]
        means[name] = sum(present) / len(present) if present else 0.0
    return [[_num(getattr(row, name, None)) if _num(getattr(row, name, None)) is not None else means[name]
             for name in feature_names] for row in rows]


def vector(row: ClusterHealthSnapshot, feature_names: list[str], fill_values: dict[str, float] | None = None) -> list[float]:
    fill = fill_values or {}
    return [
        _num(getattr(row, name, None)) if _num(getattr(row, name, None)) is not None else fill.get(name, 0.0)
        for name in feature_names
    ]


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
