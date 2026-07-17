"""IsolationForest wrapper for Phase 3 metric anomaly detection."""
from __future__ import annotations

from typing import Any

from sklearn.ensemble import IsolationForest


def train_model(rows: list[list[float]], contamination: float, random_state: int = 42) -> IsolationForest:
    model = IsolationForest(contamination=contamination, random_state=random_state)
    model.fit(rows)
    return model


def score_model(model: IsolationForest, vector: list[float]) -> dict[str, Any]:
    # sklearn returns higher score for more normal samples. Convert to an
    # intuitive 0..1 anomaly score where larger means more anomalous.
    raw_score = float(model.score_samples([vector])[0])
    decision = float(model.decision_function([vector])[0])
    pred = int(model.predict([vector])[0])
    anomaly_score = max(0.0, min(1.0, 0.5 - decision))
    return {
        "is_anomaly": pred == -1,
        "anomaly_score": anomaly_score,
        "raw_score": raw_score,
        "decision": decision,
    }
