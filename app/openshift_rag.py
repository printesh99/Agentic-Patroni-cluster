"""OpenShift Grafana/Loki bridge into the console AI/RAG/ML stores.

The source of truth is the local ``grafana_live_extractor.py`` output mounted
into the web container. Nothing here calls OpenShift, Grafana, Loki, or psql on
the monitored production cluster. Writes are limited to the console metadata DB.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from . import sources as S
from .ai import rag_retriever
from .db.models import AiKnowledgeBase, ClusterHealthSnapshot
from .db.session import SessionLocal
from .ml import training_job
from .services import ai_provider, inventory_service

SENSITIVE_VALUE_RE = re.compile(
    r"(bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"\b(password|passwd|pwd|token|secret|api[_-]?key)\s*[:=]\s*[^,\s;]+",
    re.IGNORECASE,
)
ERROR_RE = re.compile(r"\b(error|failed|failure|fatal|panic|denied|forbidden|timeout|exception)\b", re.IGNORECASE)
WARN_RE = re.compile(r"\b(warn|warning|degraded|retry|throttle|backoff)\b", re.IGNORECASE)
RESTART_RE = re.compile(r"\b(crashloopbackoff|oomkilled|back-?off|restart(?:ed|ing)?|evicted)\b", re.IGNORECASE)
ARCHIVE_RE = re.compile(r"\b(pgbackrest|archive(?:-push)?|wal archive|stanza)\b", re.IGNORECASE)
LOCK_RE = re.compile(r"\b(lock|blocked|blocking|deadlock|wait(?:ing)?)\b", re.IGNORECASE)
SLOW_RE = re.compile(r"\b(slow query|duration:|statement timeout|query timeout)\b", re.IGNORECASE)


def _cluster_name() -> str:
    return os.environ.get("OPENSHIFT_CLUSTER_NAME") or os.environ.get("OPENSHIFT_RAG_CLUSTER_NAME") or "ocp-dr-openshift"


def _safe_text(value: Any, limit: int = 2000) -> str:
    text = "" if value is None else str(value)
    text = SENSITIVE_VALUE_RE.sub(lambda m: (m.group(1) or m.group(2) or "value") + "=<REDACTED>", text)
    text = text.replace("\r", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if limit is not None and len(rows) >= limit:
                    break
    except Exception:
        return rows
    return rows


def _line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except Exception:
        return 0


def logs_file_for_extract(extract_dir: Path | None) -> Path | None:
    if not extract_dir:
        return None
    path = extract_dir / "ml" / "loki_logs_for_ml.jsonl"
    return path if path.exists() else None


def _labels(row: dict[str, Any]) -> dict[str, Any]:
    labels = row.get("labels")
    return labels if isinstance(labels, dict) else {}


def _message(row: dict[str, Any]) -> str:
    line = row.get("line")
    if line is None:
        line = row.get("message") or row.get("log") or row.get("raw")
    return _safe_text(line, 2200)


def _row_fields(row: dict[str, Any]) -> dict[str, str]:
    labels = _labels(row)
    namespace = labels.get("k8s_namespace_name") or labels.get("kubernetes_namespace_name") or ""
    pod = labels.get("k8s_pod_name") or labels.get("kubernetes_pod_name") or ""
    node = labels.get("k8s_node_name") or labels.get("kubernetes_host") or ""
    container = labels.get("k8s_container_name") or labels.get("kubernetes_container_name") or ""
    level = row.get("level") or labels.get("level") or ""
    log_type = labels.get("log_type") or labels.get("openshift_log_type") or row.get("log_type") or "infrastructure"
    ts = row.get("timestamp_utc") or row.get("@timestamp") or row.get("time") or ""
    return {
        "timestamp_utc": str(ts),
        "namespace": str(namespace),
        "pod": str(pod),
        "node": str(node),
        "container": str(container),
        "level": str(level),
        "log_type": str(log_type),
    }


def _fingerprint(row: dict[str, Any]) -> str:
    fields = _row_fields(row)
    raw = "|".join([
        fields["timestamp_utc"],
        fields["namespace"],
        fields["pod"],
        fields["node"],
        fields["container"],
        _message(row)[:700],
    ])
    return sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


def _doc_for_row(row: dict[str, Any], extract_name: str) -> dict[str, Any]:
    fields = _row_fields(row)
    msg = _message(row)
    fp = _fingerprint(row)
    where = fields["pod"] or fields["node"] or "cluster"
    title = f"OpenShift infra log {fields['timestamp_utc'] or 'unknown-time'} {where}"
    tags = [
        "openshift",
        "grafana",
        "loki",
        fields["log_type"] or "infrastructure",
        fields["namespace"] or "cluster",
        fields["container"] or "node",
    ]
    content = "\n".join([
        f"timestamp_utc: {fields['timestamp_utc'] or '-'}",
        f"log_type: {fields['log_type'] or '-'}",
        f"namespace: {fields['namespace'] or '-'}",
        f"pod: {fields['pod'] or '-'}",
        f"node: {fields['node'] or '-'}",
        f"container: {fields['container'] or '-'}",
        f"level: {fields['level'] or '-'}",
        f"message: {msg}",
    ])
    return {
        "runbook_id": f"openshift_loki_{fp}",
        "title": title[:512],
        "content": content,
        "tags": sorted(set(t for t in tags if t)),
        "source_file": f"{extract_name}/ml/loki_logs_for_ml.jsonl",
        "cluster_name": _cluster_name(),
    }


def _openshift_doc_count(db) -> int:
    return int(db.execute(
        select(func.count(AiKnowledgeBase.id)).where(AiKnowledgeBase.doc_type == "openshift_loki_log")
    ).scalar() or 0)


def status(extract_dir: Path | None) -> dict[str, Any]:
    logs_file = logs_file_for_extract(extract_dir)
    with SessionLocal() as db:
        return {
            "available": True,
            "cluster_name": _cluster_name(),
            "extract_available": bool(extract_dir and extract_dir.exists()),
            "extract_name": extract_dir.name if extract_dir else None,
            "logs_file": "ml/loki_logs_for_ml.jsonl" if logs_file else None,
            "loki_rows_on_disk": _line_count(logs_file) if logs_file else 0,
            "kb_openshift_docs": _openshift_doc_count(db),
            "kb_total_docs": int(db.execute(select(func.count(AiKnowledgeBase.id))).scalar() or 0),
            "semantic_enabled": rag_retriever.semantic_enabled(),
            "provider": ai_provider.provider_status(),
        }


def ingest(extract_dir: Path | None, limit: int = 5000, backfill_embeddings: bool = True,
           persist_ml_snapshot: bool = True) -> dict[str, Any]:
    logs_file = logs_file_for_extract(extract_dir)
    if not logs_file:
        return {"available": False, "status": "no_loki_ml_file", "ingested": 0}
    limit = max(1, min(int(limit or 5000), 50000))
    rows = _read_jsonl(logs_file, limit=limit)
    docs = [_doc_for_row(row, extract_dir.name if extract_dir else "grafana_extract") for row in rows]
    inserted = 0
    skipped = 0
    with SessionLocal() as db:
        existing = {
            r[0] for r in db.execute(
                select(AiKnowledgeBase.runbook_id).where(AiKnowledgeBase.doc_type == "openshift_loki_log")
            ).all()
        }
        for doc in docs:
            if doc["runbook_id"] in existing:
                skipped += 1
                continue
            db.add(AiKnowledgeBase(
                doc_type="openshift_loki_log",
                region=os.environ.get("REGION_NAME") or "uae",
                cluster_name=doc["cluster_name"],
                title=doc["title"],
                content=doc["content"],
                tags=doc["tags"],
                source_file=doc["source_file"],
                runbook_id=doc["runbook_id"],
            ))
            inserted += 1
        db.commit()
    embedded = None
    if backfill_embeddings:
        embedded = rag_retriever.backfill_missing_embeddings(limit=inserted or None)
    snapshot = persist_log_feature_snapshot(extract_dir, limit=limit) if persist_ml_snapshot else None
    return {
        "available": True,
        "status": "ingested",
        "extract_name": extract_dir.name if extract_dir else None,
        "rows_read": len(rows),
        "ingested": inserted,
        "skipped_existing": skipped,
        "kb_openshift_docs": status(extract_dir).get("kb_openshift_docs"),
        "embedding_backfill": embedded,
        "ml_snapshot": snapshot,
    }


def search(query: str, limit: int = 10) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"available": False, "error": "query is required", "documents": []}
    limit = max(1, min(int(limit or 10), 50))
    hits = rag_retriever.retrieve(query=f"OpenShift Loki infrastructure {q}", limit=limit)
    return {
        "available": True,
        "query": q,
        "semantic_enabled": rag_retriever.semantic_enabled(),
        "documents": hits,
        "count": len(hits),
    }


def ask(question: str, limit: int = 8) -> dict[str, Any]:
    q = (question or "").strip()
    if not q:
        return {"available": False, "error": "question is required"}
    result = search(q, limit=limit)
    docs = result.get("documents") or []
    evidence = "\n\n".join(
        f"[{idx + 1}] {doc.get('title')}\n{_safe_text(doc.get('content'), 1000)}"
        for idx, doc in enumerate(docs[:limit])
    )
    prompt = (
        "You are a read-only OpenShift/Grafana/Loki DBA/SRE assistant. "
        "Answer only from the evidence. Do not invent live state, do not expose "
        "secrets, and do not propose destructive actions. Include safe next "
        "checks and call out if evidence is insufficient.\n\n"
        f"Question: {q}\n\nEvidence from PostgreSQL RAG store:\n{evidence or '(no evidence found)'}"
    )
    provider = ai_provider.generate_rca(prompt, timeout_s=_ask_timeout_s())
    if provider.available:
        answer = provider.content
        model = f"{provider.provider}:{provider.model}"
    else:
        titles = "; ".join(str(d.get("title") or d.get("runbook_id")) for d in docs[:4])
        answer = (
            "LLM provider is not available for synthesis. "
            + (f"Relevant RAG hits found: {titles}." if titles else "No matching RAG evidence was found.")
        )
        model = f"rag-only ({provider.error or 'provider disabled'})"
    return {
        "available": True,
        "question": q,
        "answer": answer,
        "model": model,
        "semantic_enabled": result.get("semantic_enabled"),
        "evidence_count": len(docs),
        "documents": docs[:limit],
        "provider": ai_provider.provider_status(),
    }


def _ask_timeout_s() -> float:
    try:
        return float(os.environ.get("OPENSHIFT_AI_TIMEOUT_S", os.environ.get("AI_ASSISTANT_TIMEOUT_S", "60")))
    except (TypeError, ValueError):
        return 60.0


def _inventory_id(db) -> int:
    return inventory_service.resolve(db, cluster_name=_cluster_name()).id


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except Exception:
        return None


def _features_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_level: Counter[str] = Counter()
    by_namespace: Counter[str] = Counter()
    restart_count = archive_failures = lock_count = deadlock_count = slow_query_count = 0
    error_count = warning_count = 0
    timestamps: list[datetime] = []
    for row in rows:
        fields = _row_fields(row)
        msg = _message(row)
        level = (fields["level"] or "").lower()
        if level:
            by_level[level] += 1
        if fields["namespace"]:
            by_namespace[fields["namespace"]] += 1
        ts = _parse_ts(fields["timestamp_utc"])
        if ts:
            timestamps.append(ts)
        if ERROR_RE.search(level) or ERROR_RE.search(msg):
            error_count += 1
        if WARN_RE.search(level) or WARN_RE.search(msg):
            warning_count += 1
        if RESTART_RE.search(msg):
            restart_count += 1
        if ARCHIVE_RE.search(msg) and ERROR_RE.search(msg):
            archive_failures += 1
        if LOCK_RE.search(msg):
            lock_count += 1
        if "deadlock" in msg.lower():
            deadlock_count += 1
        if SLOW_RE.search(msg):
            slow_query_count += 1
    minutes = 1.0
    if len(timestamps) >= 2:
        span = (max(timestamps) - min(timestamps)).total_seconds() / 60.0
        minutes = max(span, 1.0)
    return {
        "total_lines": len(rows),
        "error_count": error_count,
        "warning_count": warning_count,
        "restart_count": restart_count,
        "archive_failures": archive_failures,
        "lock_count": lock_count,
        "deadlock_count": deadlock_count,
        "slow_query_count": slow_query_count,
        "window_minutes": round(minutes, 2),
        "deadlocks_per_min": deadlock_count / minutes,
        "top_levels": by_level.most_common(10),
        "top_namespaces": by_namespace.most_common(10),
    }


def persist_log_feature_snapshot(extract_dir: Path | None, limit: int = 5000) -> dict[str, Any]:
    logs_file = logs_file_for_extract(extract_dir)
    if not logs_file:
        return {"available": False, "status": "no_loki_ml_file"}
    rows = _read_jsonl(logs_file, limit=max(1, min(int(limit or 5000), 50000)))
    features = _features_from_rows(rows)
    with SessionLocal() as db:
        inv_id = _inventory_id(db)
        row = ClusterHealthSnapshot(
            inventory_id=inv_id,
            role="openshift",
            deadlocks_per_min=features["deadlocks_per_min"],
            locks_waiting_count=features["lock_count"],
            archive_failed_count=features["archive_failures"],
            pod_restart_count=features["restart_count"],
            pg_stat_statements_slow_query_count=features["slow_query_count"],
            raw_metrics={
                "source": "grafana_live_extractor",
                "extract_name": extract_dir.name if extract_dir else None,
                "openshift_loki_features": features,
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "available": True,
            "status": "snapshot_stored",
            "snapshot_id": row.id,
            "cluster_name": _cluster_name(),
            "features": features,
        }


def train(force: bool = False) -> dict[str, Any]:
    return training_job.train(_cluster_name(), force=force)
