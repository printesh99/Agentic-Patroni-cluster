"""IsolationForest training job for Phase 3."""
from __future__ import annotations

from typing import Any

import joblib
from sqlalchemy import select

from .. import ai_config, sources as S
from ..db.models import ClusterHealthSnapshot
from ..db.session import SessionLocal
from . import baseline, isolation_forest, model_registry
from .snapshot_matrix import matrix, row_to_features, usable_feature_names
from ..services import inventory_service


def train(cluster_name: str | None = None, force: bool = False) -> dict[str, Any]:
    cluster = cluster_name or S.CLUSTER_NAME
    with SessionLocal() as db:
        inv = inventory_service.resolve(db, cluster_name=cluster)
        rows = db.execute(
            select(ClusterHealthSnapshot)
            .where(ClusterHealthSnapshot.inventory_id == inv.id)
            .order_by(ClusterHealthSnapshot.collected_at.asc(), ClusterHealthSnapshot.id.asc())
        ).scalars().all()
        min_rows = ai_config.AI_ML_MIN_TRAINING_ROWS
        if len(rows) < min_rows and not force:
            return {
                "available": False,
                "status": "insufficient_history",
                "cluster_name": cluster,
                "rows": len(rows),
                "min_rows": min_rows,
                "hint": "Use force=true only for local/dev validation.",
            }
        if len(rows) < 2:
            return {"available": False, "status": "insufficient_history", "cluster_name": cluster, "rows": len(rows), "min_rows": 2}
        features = usable_feature_names(rows)
        if not features:
            return {"available": False, "status": "no_usable_features", "cluster_name": cluster}
        mat = matrix(rows, features)
        contamination = min(max(ai_config.AI_ML_DEFAULT_CONTAMINATION, 0.001), 0.49)
        model = isolation_forest.train_model(mat, contamination)
        fill_values = {name: sum(row[i] for row in mat) / len(mat) for i, name in enumerate(features)}
        baseline_summary = baseline.summarize([row_to_features(r) for r in rows])
        payload = {
            "model": model,
            "feature_list": features,
            "fill_values": fill_values,
            "baseline": baseline_summary,
            "cluster_name": cluster,
        }
        path = model_registry.model_path(cluster)
        joblib.dump(payload, path)
        reg = model_registry.register(db, cluster, inv.region, inv.env, features, len(rows), contamination, path)
        db.commit()
        return {
            "available": True,
            "status": "trained",
            "model": model_registry.serialize(reg),
            "rows": len(rows),
            "features": features,
            "forced": force,
        }
