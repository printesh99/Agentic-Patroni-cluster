"""SMTP notification service for AI DBA recommendations."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def enabled() -> bool:
    return _env_bool("AI_RECOMMENDATION_EMAIL_ENABLED", False)


def _rec_value(rec: Any, name: str, default: Any = "") -> Any:
    if isinstance(rec, dict):
        return rec.get(name, default)
    return getattr(rec, name, default)


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, indent=2, default=str)[:6000]
    return str(value)


def subject_for(rec: Any) -> str:
    severity = _rec_value(rec, "severity", "INFO")
    category = _rec_value(rec, "category", "OTHER")
    cluster = _rec_value(rec, "cluster_name", "-")
    database = _rec_value(rec, "database_name", "-")
    target = f"{cluster}/{database}" if database and database != "-" else str(cluster)
    return f"AI DBA Recommendation - [{severity}] - [{category}] - [{target}]"


def body_for(rec: Any, approval_url: str | None = None) -> str:
    lines = [
        "AI DBA Recommendation",
        "",
        f"Severity: {_fmt(_rec_value(rec, 'severity'))}",
        f"Cluster: {_fmt(_rec_value(rec, 'cluster_name'))}",
        f"Region/DC: {_fmt(_rec_value(rec, 'region_name'))} / {_fmt(_rec_value(rec, 'dc_name'))}",
        f"Database: {_fmt(_rec_value(rec, 'database_name'))}",
        f"Category: {_fmt(_rec_value(rec, 'category'))}",
        "",
        "Finding:",
        _fmt(_rec_value(rec, "finding")),
        "",
        "Evidence:",
        _fmt(_rec_value(rec, "evidence")),
        "",
        "Root cause:",
        _fmt(_rec_value(rec, "root_cause")),
        "",
        "Recommendation:",
        _fmt(_rec_value(rec, "recommendation")),
        "",
        "Recommended SQL:",
        _fmt(_rec_value(rec, "recommended_sql")),
        "",
        "Rollback SQL:",
        _fmt(_rec_value(rec, "rollback_sql")),
        "",
        f"Risk: {_fmt(_rec_value(rec, 'risk_level'))}",
        f"Confidence score: {_fmt(_rec_value(rec, 'confidence_score'))}",
        "",
        "Review:",
        approval_url or "Open the Web UI console and review the AI Recommendations page before approving any action.",
    ]
    return "\n".join(lines)


def build_message(rec: Any, approval_url: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME") or "ai-dba-agent@localhost"
    recipients = [x.strip() for x in (os.environ.get("SMTP_TO") or "").split(",") if x.strip()]
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject_for(rec)
    msg.set_content(body_for(rec, approval_url=approval_url))
    return msg


def send_recommendation(rec: Any, approval_url: str | None = None) -> dict[str, Any]:
    if not enabled():
        return {"sent": False, "reason": "AI_RECOMMENDATION_EMAIL_ENABLED is false"}
    host = os.environ.get("SMTP_HOST")
    to_raw = os.environ.get("SMTP_TO")
    if not host or not to_raw:
        return {"sent": False, "reason": "SMTP_HOST or SMTP_TO is not configured"}
    port = int(os.environ.get("SMTP_PORT") or "587")
    username = os.environ.get("SMTP_USERNAME") or ""
    password = os.environ.get("SMTP_PASSWORD") or ""
    use_tls = _env_bool("SMTP_USE_TLS", True)
    msg = build_message(rec, approval_url=approval_url)
    recipients = [x.strip() for x in to_raw.split(",") if x.strip()]
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(msg, to_addrs=recipients)
        return {"sent": True, "recipients": len(recipients)}
    except Exception as exc:
        return {"sent": False, "reason": str(exc)}


def notify_recommendations(recommendations: list[Any], approval_url: str | None = None) -> dict[str, Any]:
    if not recommendations:
        return {"enabled": enabled(), "sent": 0, "errors": []}
    sent = 0
    errors: list[str] = []
    for rec in recommendations:
        result = send_recommendation(rec, approval_url=approval_url)
        if result.get("sent"):
            sent += 1
        elif result.get("reason"):
            errors.append(str(result["reason"]))
    return {"enabled": enabled(), "sent": sent, "errors": errors[:5]}
