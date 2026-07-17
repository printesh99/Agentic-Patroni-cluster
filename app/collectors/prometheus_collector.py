"""Prometheus collector for Phase 1 health snapshots."""
from __future__ import annotations

from typing import Any

from .. import ai_config
from .. import sources as S


def collect() -> dict[str, Any]:
    metrics_cfg = ai_config.load_metrics_map()["metrics"]
    values: dict[str, float | None] = {}
    warnings: list[str] = []
    for name, spec in metrics_cfg.items():
        query = (
            str(spec["prometheus_query"])
            .replace("{namespace}", S.NS)
            .replace("{cluster_name}", S.CLUSTER_NAME)
            .replace("{cluster_id}", S.CLUSTER_ID)
        )
        try:
            values[name] = S.prom_scalar(query)
            if values[name] is None:
                warnings.append(f"prometheus metric {name} returned no data")
        except Exception as exc:
            values[name] = None
            warnings.append(f"prometheus metric {name} unavailable: {exc}")
    return {
        "source": "prometheus",
        "available": any(v is not None for v in values.values()),
        "values": values,
        "warnings": warnings,
    }
