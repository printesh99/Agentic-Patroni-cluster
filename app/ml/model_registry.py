"""Model registry helpers backed by ml_model_registry."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .. import ai_config
from ..db.models import MlModelRegistry


MODEL_NAME = "isolation_forest"
MODEL_TYPE = "metric_anomaly"


def model_path(cluster_name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in cluster_name)
    ai_config.AI_ML_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    return ai_config.AI_ML_MODEL_DIR / f"{safe}_isolation_forest.joblib"


def register(db, cluster_name: str, region: str | None, env: str | None, feature_list: list[str],
             training_rows: int, contamination: float, path: Path) -> MlModelRegistry:
    existing = db.execute(
        select(MlModelRegistry)
        .where(MlModelRegistry.model_name == MODEL_NAME)
        .where(MlModelRegistry.cluster_name == cluster_name)
        .where(MlModelRegistry.status == "active")
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing is None:
        existing = MlModelRegistry(
            model_name=MODEL_NAME,
            model_type=MODEL_TYPE,
            cluster_name=cluster_name,
            region=region,
            env=env,
            model_path=str(path),
            feature_list=feature_list,
            training_start=now,
            training_end=now,
            training_rows=training_rows,
            contamination=contamination,
            status="active",
        )
        db.add(existing)
    else:
        existing.model_path = str(path)
        existing.feature_list = feature_list
        existing.training_start = now
        existing.training_end = now
        existing.training_rows = training_rows
        existing.contamination = contamination
        existing.status = "active"
    db.flush()
    return existing


def active(db, cluster_name: str) -> MlModelRegistry | None:
    return db.execute(
        select(MlModelRegistry)
        .where(MlModelRegistry.model_name == MODEL_NAME)
        .where(MlModelRegistry.cluster_name == cluster_name)
        .where(MlModelRegistry.status == "active")
        .order_by(MlModelRegistry.created_at.desc(), MlModelRegistry.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def serialize(model: MlModelRegistry) -> dict[str, Any]:
    return {
        "id": model.id,
        "model_name": model.model_name,
        "model_type": model.model_type,
        "cluster_name": model.cluster_name,
        "region": model.region,
        "env": model.env,
        "model_path": model.model_path,
        "feature_list": model.feature_list,
        "training_rows": model.training_rows,
        "contamination": model.contamination,
        "status": model.status,
        "created_at": model.created_at.isoformat() if model.created_at else None,
    }
