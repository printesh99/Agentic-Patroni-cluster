"""Phase 5 risk scoring across rules, ML, forecasts, logs, and readiness."""
from __future__ import annotations

from typing import Any

SEVERITY_SCORE = {
    "normal": 0,
    "info": 35,
    "warning": 60,
    "low": 45,
    "medium": 60,
    "high": 75,
    "critical": 82,
    "emergency": 95,
    "fatal": 90,
}


def score(inputs: dict[str, Any]) -> dict[str, Any]:
    rule_findings = inputs.get("rule_findings") or []
    ml = inputs.get("ml_findings") or {}
    forecasts = inputs.get("forecast_findings") or []
    log_findings = inputs.get("log_findings") or []
    readiness = inputs.get("readiness") or {}

    points: list[int] = []
    reasons: list[str] = []
    runbooks: list[str] = []
    categories: list[str] = []

    for finding in rule_findings:
        sev = finding.get("severity", "info")
        points.append(SEVERITY_SCORE.get(sev, 35))
        reasons.append(finding.get("message") or finding.get("rule_id"))
        if finding.get("recommended_runbook_id"):
            runbooks.append(finding["recommended_runbook_id"])
        if finding.get("category"):
            categories.append(finding["category"])

    if ml.get("available") and ml.get("status") == "scored":
        anomaly_score = float(ml.get("anomaly_score") or 0)
        if ml.get("is_anomaly"):
            points.append(max(65, int(anomaly_score * 100)))
            reasons.append("IsolationForest detected a multivariate anomaly")
        elif anomaly_score >= 0.5:
            points.append(35)
            reasons.append("IsolationForest score is elevated but not anomalous")

    for forecast in forecasts:
        sev = forecast.get("severity")
        if sev in ("warning", "critical", "emergency"):
            points.append(SEVERITY_SCORE.get(sev, 35))
            metric = forecast.get("metric_name")
            reasons.append(f"{metric} forecast severity is {sev}")

    for finding in log_findings:
        sev = finding.get("severity", "info")
        points.append(SEVERITY_SCORE.get(sev, 35))
        reasons.append(f"Loki finding: {finding.get('title') or finding.get('category')}")
        if finding.get("category"):
            categories.append(finding["category"])

    summary = readiness.get("summary") or {}
    if summary.get("critical"):
        points.append(80)
        reasons.append("Readiness has critical checks")
    elif summary.get("warnings"):
        points.append(55)
        reasons.append("Readiness has warning checks")

    risk_score = min(100, max(points) if points else 0)
    if len([p for p in points if p >= 75]) >= 2:
        risk_score = min(100, risk_score + 10)
    severity = severity_for_score(risk_score)
    primary_category = _primary_category(categories, rule_findings, log_findings)
    runbook = runbooks[0] if runbooks else _runbook_for_category(primary_category)
    return {
        "risk_score": risk_score,
        "final_severity": severity,
        "primary_category": primary_category,
        "reasons": [r for r in reasons if r][:10],
        "recommended_runbook_id": runbook,
    }


def severity_for_score(value: int) -> str:
    if value <= 30:
        return "normal"
    if value <= 55:
        return "info"
    if value <= 70:
        return "warning"
    if value <= 85:
        return "critical"
    return "emergency"


def _primary_category(categories: list[str], rules: list[dict[str, Any]], logs: list[dict[str, Any]]) -> str:
    if categories:
        return categories[0]
    if rules:
        return rules[0].get("category") or "general"
    if logs:
        return logs[0].get("category") or "logs"
    return "normal"


def _runbook_for_category(category: str) -> str:
    return {
        "replication": "runbook_replication_lag",
        "wal": "runbook_wal_disk_full",
        "storage": "runbook_pgdata_disk_pressure",
        "backup": "runbook_pgbackrest_troubleshooting",
        "patroni": "runbook_patroni_failover",
        "connections": "runbook_connection_exhaustion",
        "lock_contention": "runbook_lock_contention",
    }.get(category, "runbook_general_dba_triage")
