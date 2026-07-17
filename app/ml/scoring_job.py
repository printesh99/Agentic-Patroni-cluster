"""IsolationForest scoring job for Phase 3."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
from sqlalchemy import select

from .. import sources as S
from ..db.models import ClusterHealthSnapshot, MlAnomalyScore
from ..db.session import SessionLocal
from . import baseline, isolation_forest, model_registry
from .snapshot_matrix import row_to_features, vector
from ..services import inventory_service


def score_latest(cluster_name: str | None = None) -> dict[str, Any]:
    cluster = cluster_name or S.CLUSTER_NAME
    with SessionLocal() as db:
        inv = inventory_service.resolve(db, cluster_name=cluster)
        reg = model_registry.active(db, cluster)
        if reg is None:
            return {"available": False, "status": "untrained", "cluster_name": cluster}
        path = Path(reg.model_path)
        if not path.exists():
            return {"available": False, "status": "model_file_missing", "cluster_name": cluster, "model_id": reg.id}
        row = db.execute(
            select(ClusterHealthSnapshot)
            .where(ClusterHealthSnapshot.inventory_id == inv.id)
            .order_by(ClusterHealthSnapshot.collected_at.desc(), ClusterHealthSnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return {"available": False, "status": "no_snapshots", "cluster_name": cluster}
        payload = joblib.load(path)
        feature_list = payload["feature_list"]
        if list(reg.feature_list) != list(feature_list):
            return {"available": False, "status": "feature_list_mismatch", "model_id": reg.id}
        vec = vector(row, feature_list, payload.get("fill_values") or {})
        score = isolation_forest.score_model(payload["model"], vec)
        current = row_to_features(row)
        abnormal = baseline.abnormal_features(current, payload.get("baseline") or {})
        top_features = _top_features(current, payload.get("baseline") or {}, feature_list)
        severity = _severity(score["anomaly_score"], score["is_anomaly"], abnormal)
        db_row = MlAnomalyScore(
            snapshot_id=row.id,
            model_id=reg.id,
            is_anomaly=score["is_anomaly"],
            anomaly_score=score["anomaly_score"],
            severity=severity,
            top_features=top_features,
            evidence={name: current.get(name) for name in top_features},
            raw_output={"score": score, "baseline_abnormal": abnormal},
        )
        db.add(db_row)
        db.commit()
        db.refresh(db_row)
        return {
            "available": True,
            "status": "scored",
            "score_id": db_row.id,
            "snapshot_id": row.id,
            "model_id": reg.id,
            "is_anomaly": score["is_anomaly"],
            "anomaly_score": score["anomaly_score"],
            "severity": severity,
            "top_features": top_features,
            "baseline_abnormal": abnormal,
        }


def _severity(anomaly_score: float, is_anomaly: bool, abnormal: list[dict[str, Any]]) -> str:
    if is_anomaly and anomaly_score >= 0.75:
        return "critical"
    if is_anomaly or abnormal:
        return "warning"
    return "info"


def _top_features(current: dict[str, float | None], base: dict[str, dict[str, Any]], feature_list: list[str]) -> list[str]:
    ranked: list[tuple[float, str]] = []
    for name in feature_list:
        value = current.get(name)
        if value is None:
            continue
        spec = base.get(name) or {}
        med = spec.get("median")
        mad = spec.get("mad") or spec.get("stddev")
        if med is None or not mad:
            ranked.append((abs(value), name))
        else:
            ranked.append((abs(value - med) / mad, name))
    ranked.sort(reverse=True)
    return [name for _, name in ranked[:3]]
