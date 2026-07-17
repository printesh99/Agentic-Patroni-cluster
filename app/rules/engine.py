"""Config-driven deterministic rule engine."""
from __future__ import annotations

from typing import Any

from .. import ai_config
from . import backup_rules, connection_rules, patroni_rules, replication_rules, sql_rules, wal_rules
from .common import SEVERITY_RANK


RULE_MODULES = [
    patroni_rules,
    replication_rules,
    wal_rules,
    connection_rules,
    backup_rules,
    sql_rules,
]


def evaluate(snapshot: dict[str, Any]) -> dict[str, Any]:
    thresholds = ai_config.load_thresholds()
    defaults = thresholds.get("defaults") or {}
    features = snapshot.get("features") or {}
    findings: list[dict[str, Any]] = []
    for module in RULE_MODULES:
        findings.extend(module.evaluate(features, snapshot, defaults))
    findings.sort(key=lambda f: (-SEVERITY_RANK.get(f["severity"], 0), f["rule_id"]))
    summary = {
        "total": len(findings),
        "emergency": sum(1 for f in findings if f["severity"] == "emergency"),
        "critical": sum(1 for f in findings if f["severity"] == "critical"),
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "info": sum(1 for f in findings if f["severity"] == "info"),
    }
    summary["status"] = (
        "emergency" if summary["emergency"] else
        "critical" if summary["critical"] else
        "warning" if summary["warning"] else
        "ok"
    )
    return {
        "available": True,
        "source": "rule-engine",
        "snapshot_id": snapshot.get("snapshot_id"),
        "findings": findings,
        "summary": summary,
    }
