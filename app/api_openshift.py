"""OpenShift Grafana integration API.

Read-only endpoints used by the OpenShift Overview console module. The API
does not store Grafana credentials and does not call Grafana directly. It
returns safe configuration plus metadata from a local grafana_live_extractor.py
artifact when one is present.
"""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body

from . import openshift_rag
from .threads import to_thread

router = APIRouter(prefix="/api/v1/openshift", tags=["openshift"])

APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GRAFANA_BASE = "https://grafana-route-grafana.apps.ocp-dr.habibbank.local"
DEFAULT_DASHBOARD_UID = "ocpdr-overview"
DEFAULT_DASHBOARD_SLUG = "ocp-dr-1-cluster-overview"
DEFAULT_FOLDER_UID = "fess10tt4fbi8a"
DEFAULT_LOKI_UID = "f556b0b0-a756-4fc5-902b-ddc2f1523b45"
SENSITIVE_VALUE_RE = re.compile(
    r"(bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"\b(password|passwd|pwd|token|secret|api[_-]?key)\s*[:=]\s*[^,\s;]+",
    re.IGNORECASE,
)


def _body_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _config() -> dict[str, Any]:
    base = os.environ.get("OPENSHIFT_GRAFANA_BASE", DEFAULT_GRAFANA_BASE).rstrip("/")
    uid = os.environ.get("OPENSHIFT_GRAFANA_DASHBOARD_UID", DEFAULT_DASHBOARD_UID)
    slug = os.environ.get("OPENSHIFT_GRAFANA_DASHBOARD_SLUG", DEFAULT_DASHBOARD_SLUG)
    folder_uid = os.environ.get("OPENSHIFT_GRAFANA_FOLDER_UID", DEFAULT_FOLDER_UID)
    loki_uid = os.environ.get("OPENSHIFT_GRAFANA_LOKI_UID", DEFAULT_LOKI_UID)
    time_from = os.environ.get("OPENSHIFT_GRAFANA_FROM", "now-6h")
    time_to = os.environ.get("OPENSHIFT_GRAFANA_TO", "now")
    refresh = os.environ.get("OPENSHIFT_GRAFANA_REFRESH", "30s")
    dashboard_url = f"{base}/d/{uid}/{slug}?from={time_from}&to={time_to}&refresh={refresh}"
    return {
        "grafana_base": base,
        "folder_uid": folder_uid,
        "dashboard_uid": uid,
        "dashboard_slug": slug,
        "dashboard_url": dashboard_url,
        "loki_datasource_uid": loki_uid,
        "default_from": time_from,
        "default_to": time_to,
        "refresh": refresh,
    }


def _candidate_extract_roots() -> list[Path]:
    raw = os.environ.get("OPENSHIFT_GRAFANA_EXTRACT_DIR")
    if raw:
        return [Path(raw)]
    roots = [
        Path(os.environ.get("OPENSHIFT_GRAFANA_EXTRACTS_ROOT", "")) if os.environ.get("OPENSHIFT_GRAFANA_EXTRACTS_ROOT") else None,
        APP_ROOT / "extracts",
        APP_ROOT.parent / "extracts",
        Path.cwd() / "extracts",
        Path("/data/extracts"),
    ]
    out: list[Path] = []
    for root in roots:
        if not root:
            continue
        if root.name.startswith("grafana_extract_"):
            out.append(root)
            continue
        out.extend(sorted(root.glob("grafana_extract_*")) if root.exists() else [])
    return out


def _latest_extract() -> Path | None:
    candidates = [p for p in _candidate_extract_roots() if p.exists() and p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _read_jsonl(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rows.append(json.loads(line))
                if len(rows) >= limit:
                    break
    except Exception:
        return rows
    return rows


def _count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except Exception:
        return 0


def _read_csv(path: Path, limit: int = 50) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                rows.append({k: v for k, v in row.items() if k is not None})
                if len(rows) >= limit:
                    break
    except Exception:
        return rows
    return rows


def _safe_text(value: Any, limit: int = 420) -> str:
    text = "" if value is None else str(value)
    text = SENSITIVE_VALUE_RE.sub(lambda m: (m.group(1) or m.group(2) or "value") + "=<REDACTED>", text)
    text = text.replace("\n", " ").replace("\r", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _log_sample(extract_dir: Path, limit: int) -> dict[str, Any]:
    logs_file = extract_dir / "ml" / "loki_logs_for_ml.jsonl"
    count = _count_lines(logs_file)
    sample = []
    for row in _read_jsonl(logs_file, limit=max(1, min(limit, 50))):
        labels = row.get("labels") or {}
        sample.append({
            "timestamp_utc": row.get("timestamp_utc"),
            "namespace": labels.get("k8s_namespace_name") or labels.get("kubernetes_namespace_name") or "",
            "pod": labels.get("k8s_pod_name") or labels.get("kubernetes_pod_name") or "",
            "node": labels.get("k8s_node_name") or labels.get("kubernetes_host") or "",
            "container": labels.get("k8s_container_name") or labels.get("kubernetes_container_name") or "",
            "line": _safe_text(row.get("line"), 520),
        })
    return {
        "available": logs_file.exists(),
        "file": "ml/loki_logs_for_ml.jsonl",
        "count": count,
        "sample": sample,
    }


def _extract_payload(log_limit: int = 8) -> dict[str, Any]:
    cfg = _config()
    extract_dir = _latest_extract()
    if not extract_dir:
        return {
            "available": False,
            "source": "grafana-extractor",
            "config": cfg,
            "extract": {"available": False},
            "summary": {
                "dashboard_count": 1,
                "datasource_count": 0,
                "panel_count": 0,
                "query_count": 0,
                "embed_url_count": 1,
                "loki_log_rows": 0,
            },
            "dashboards": [{
                "uid": cfg["dashboard_uid"],
                "title": "OCP DR - Cluster Overview",
                "url": cfg["dashboard_url"],
                "folder_uid": cfg["folder_uid"],
            }],
            "datasources": [],
            "panels": [],
            "queries": [],
            "logs": {"available": False, "count": 0, "sample": []},
        }

    summary = _read_json(extract_dir / "summary.json", {})
    display = _read_json(extract_dir / "inventory" / "display_requirements.json", {})
    dashboards = display.get("dashboards") or _read_csv(extract_dir / "inventory" / "dashboards.csv", limit=25)
    datasources = display.get("datasources") or _read_json(extract_dir / "datasources" / "datasources.json", [])
    panels = _read_csv(extract_dir / "inventory" / "panels.csv", limit=40)
    queries = _read_jsonl(extract_dir / "inventory" / "panel_query_inventory.jsonl", limit=30)
    logs = _log_sample(extract_dir, log_limit)
    summary = {
        **summary,
        "dashboard_count": summary.get("dashboard_count", len(dashboards)),
        "datasource_count": summary.get("datasource_count", len(datasources)),
        "panel_count": summary.get("panel_count", len(panels)),
        "query_count": summary.get("query_count", len(queries)),
        "embed_url_count": summary.get("embed_url_count", len(display.get("dashboard_embed_urls") or [])),
        "loki_log_rows": logs["count"],
    }
    return {
        "available": True,
        "source": "grafana-extractor",
        "config": cfg,
        "extract": {
            "available": True,
            "name": extract_dir.name,
            "summary_file": "summary.json",
            "display_requirements_file": "inventory/display_requirements.json",
        },
        "summary": summary,
        "dashboards": dashboards[:25],
        "datasources": [
            {
                "uid": ds.get("uid") or ds.get("id") or ds.get("name"),
                "name": ds.get("name"),
                "type": ds.get("type"),
                "access": ds.get("access"),
                "url": _safe_text(ds.get("url"), 180),
            }
            for ds in datasources[:25]
        ],
        "panels": panels[:40],
        "queries": queries[:30],
        "logs": logs,
    }


@router.get("/grafana/overview")
async def grafana_overview(log_limit: int = 8):
    return _extract_payload(log_limit=log_limit)


@router.get("/grafana/config")
async def grafana_config():
    return {"source": "static-config", "config": _config()}


@router.get("/grafana/logs/sample")
async def grafana_logs_sample(limit: int = 10):
    extract_dir = _latest_extract()
    if not extract_dir:
        return {"available": False, "count": 0, "sample": []}
    return _log_sample(extract_dir, limit)


@router.get("/rag/status")
async def rag_status():
    return await to_thread(openshift_rag.status, _latest_extract())


@router.post("/rag/ingest")
async def rag_ingest(payload: dict = Body(default={})):
    limit = int(payload.get("limit") or 5000)
    backfill = _body_bool(payload.get("backfill_embeddings"), True)
    persist_snapshot = _body_bool(payload.get("persist_ml_snapshot"), True)
    return await to_thread(
        openshift_rag.ingest,
        _latest_extract(),
        limit,
        backfill,
        persist_snapshot,
    )


@router.get("/rag/search")
async def rag_search(query: str, limit: int = 10):
    return await to_thread(openshift_rag.search, query, limit)


@router.post("/rag/ask")
async def rag_ask(payload: dict = Body(default={})):
    question = payload.get("question") or payload.get("prompt") or ""
    limit = int(payload.get("limit") or 8)
    return await to_thread(openshift_rag.ask, question, limit)


@router.post("/ml/snapshot")
async def ml_snapshot(payload: dict = Body(default={})):
    limit = int(payload.get("limit") or 5000)
    return await to_thread(openshift_rag.persist_log_feature_snapshot, _latest_extract(), limit)


@router.post("/ml/train")
async def ml_train(payload: dict = Body(default={})):
    return await to_thread(openshift_rag.train, _body_bool(payload.get("force"), False))
