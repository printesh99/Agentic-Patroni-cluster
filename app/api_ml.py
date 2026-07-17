"""Phase 3 ML model API."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .db.models import MlAnomalyScore, MlModelRegistry
from .db.session import SessionLocal
from .ml import model_registry, scoring_job, training_job
from .threads import to_thread

router = APIRouter(prefix="/api/v1")


def _safe(fn, *args, status: str = "error"):
    """Run a job and always return a JSON-serializable dict — never surface a
    raw exception as an HTTP 500 to the ML panel."""
    try:
        return fn(*args)
    except Exception as exc:  # pragma: no cover - defensive UI contract
        return {"available": False, "status": status, "error": str(exc)}


@router.post("/ml/train/{cluster_name}")
async def train_cluster(cluster_name: str, force: bool = False):
    return await to_thread(_safe, training_job.train, cluster_name, force)


@router.post("/ml/score/{cluster_name}")
async def score_cluster(cluster_name: str):
    return await to_thread(_safe, scoring_job.score_latest, cluster_name)


@router.get("/ml/models")
async def models():
    def _list():
        with SessionLocal() as db:
            rows = db.execute(select(MlModelRegistry).order_by(MlModelRegistry.id.desc())).scalars().all()
            return {"available": True, "models": [model_registry.serialize(r) for r in rows]}
    return await to_thread(_list)


@router.get("/ml/models/{model_id}")
async def model_detail(model_id: int):
    def _get():
        with SessionLocal() as db:
            row = db.get(MlModelRegistry, model_id)
            if row is None:
                return {"available": False, "error": "model not found", "model_id": model_id}
            return {"available": True, "model": model_registry.serialize(row)}
    return await to_thread(_get)


@router.get("/ml/anomalies")
async def anomalies(limit: int = 50):
    def _list():
        with SessionLocal() as db:
            rows = db.execute(select(MlAnomalyScore).order_by(MlAnomalyScore.id.desc()).limit(limit)).scalars().all()
            return {"available": True, "anomalies": [_serialize_score(r) for r in rows]}
    return await to_thread(_list)


@router.get("/ml/anomalies/{snapshot_id}")
async def anomalies_for_snapshot(snapshot_id: int):
    def _list():
        with SessionLocal() as db:
            rows = db.execute(
                select(MlAnomalyScore)
                .where(MlAnomalyScore.snapshot_id == snapshot_id)
                .order_by(MlAnomalyScore.id.desc())
            ).scalars().all()
            return {"available": True, "snapshot_id": snapshot_id, "anomalies": [_serialize_score(r) for r in rows]}
    return await to_thread(_list)


def _serialize_score(row: MlAnomalyScore) -> dict:
    return {
        "id": row.id,
        "snapshot_id": row.snapshot_id,
        "model_id": row.model_id,
        "scored_at": row.scored_at.isoformat() if row.scored_at else None,
        "is_anomaly": row.is_anomaly,
        "anomaly_score": row.anomaly_score,
        "severity": row.severity,
        "top_features": row.top_features,
        "evidence": row.evidence,
    }
