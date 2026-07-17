"""Normalize Loki stream entries + build LogQL from explorer filters.

Keeps the log vocabulary (component/level) consistent with the Promtail ingest
taxonomy (see ``infra/logging/promtail-values.yaml``). Raw source levels are
preserved as ``level`` and folded into a 4-value ``severity`` the UI colors by.
"""
from __future__ import annotations

import os
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from . import sources as S

# This cluster's LokiStack is fed by the OpenShift Logging collector, whose
# stream labels follow the k8s/OTEL schema (k8s_namespace_name, k8s_pod_name,
# ...) — NOT the bare Promtail labels (namespace/pod/container) this console was
# first written against. The opa-openshift gateway also derives per-namespace
# authorization from ``k8s_namespace_name``; querying the old
# ``kubernetes_namespace_name`` is rejected 403 "access this tenant", and bare
# ``namespace`` authorizes but matches no streams. Keep every selector/readback
# keyed off these constants so the console matches the live label schema.
# Env-overridable so the same image adapts to a logging-stack upgrade
# (kubernetes_* vs k8s_*) or a different collector without a rebuild — just
# `oc set env`. Defaults match the current OpenShift LokiStack (OTEL schema).
LBL_NAMESPACE = os.environ.get("LOKI_LABEL_NAMESPACE") or "k8s_namespace_name"
LBL_POD = os.environ.get("LOKI_LABEL_POD") or "k8s_pod_name"
LBL_CONTAINER = os.environ.get("LOKI_LABEL_CONTAINER") or "k8s_container_name"
LBL_NODE = os.environ.get("LOKI_LABEL_NODE") or "k8s_node_name"

# Raw source level (PostgreSQL / Patroni / PgBouncer / PGO) -> normalized
# severity. Anything unknown falls back to "info".
_SEVERITY = {
    "panic": "fatal", "fatal": "fatal", "crit": "fatal", "critical": "fatal",
    "error": "error", "err": "error",
    "warning": "warn", "warn": "warn",
    "notice": "info", "log": "info", "info": "info", "debug": "info",
    "detail": "info", "hint": "info", "statement": "info", "context": "info",
    "stats": "info", "noise": "info",
}
SEVERITIES = ["fatal", "error", "warn", "info"]


def severity(raw_level: str | None) -> str:
    return _SEVERITY.get((raw_level or "").strip().lower(), "info")


def _iso(ts_ns: str | int) -> str:
    secs = int(ts_ns) / 1_000_000_000
    return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Egress redaction (defense in depth) — second layer after Promtail ingest
# (see infra/logging/promtail-values.yaml). If an unredacted secret ever
# reaches Loki (mis-parsed prefix, a new log shape), the backend still never
# emits it to the browser or the AI model. Keep these in sync with the ingest
# regex set; the marker matches so already-redacted lines are unaffected.
# --------------------------------------------------------------------------
_MASK = "***REDACTED***"
_REDACTORS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(password\s*(?:=\s*)?')[^']*(')"), r"\1" + _MASK + r"\2"),
    (re.compile(r"(?i)\b(password|passwd|pgpassword|secret|token|api[_-]?key|access[_-]?key)(\s*[=:]\s*)[^\s'\";,&]+"),
     r"\1\2" + _MASK),
    (re.compile(r"(://)[^:@/\s]+:[^@/\s]+(@)"), r"\1" + _MASK + r"\2"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1" + _MASK),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), _MASK),
    (re.compile(r"\b\d{13,19}\b"), _MASK),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), _MASK),
]


def redact(text: str) -> str:
    for rx, repl in _REDACTORS:
        text = rx.sub(repl, text)
    return text


def _decode_body(line: str) -> tuple[str, dict[str, Any]]:
    """Decode an OpenShift/ViaQ envelope without losing the original record."""
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return str(line or ""), {}
    if not isinstance(record, dict):
        return str(line or ""), {}
    message = record.get("message") or record.get("msg") or record.get("body")
    return (str(message) if message is not None else str(line or "")), record


_PG_LEVEL = re.compile(r"\b(PANIC|FATAL|ERROR|WARNING|LOG|DETAIL|STATEMENT|HINT)\b\s*:", re.I)
_PATRONI_LEVEL = re.compile(r"\b(INFO|WARNING|ERROR|CRITICAL)\b\s*:\s", re.I)


def parse_body(message: str, container: str) -> tuple[str, str]:
    """Derive component/severity from the body; neither is a Loki label here."""
    if (container or "").lower() == "pgbouncer":
        match = _PG_LEVEL.search(message or "") or _PATRONI_LEVEL.search(message or "")
        return "pgbouncer", (match.group(1).upper() if match else "INFO")
    pg_match = _PG_LEVEL.search(message or "")
    if pg_match:
        return "postgres", pg_match.group(1).upper()
    patroni_match = _PATRONI_LEVEL.search(message or "")
    if patroni_match:
        return "patroni", patroni_match.group(1).upper()
    return "database", "INFO"


def normalize_entry(stream: dict[str, str], ts_ns: str, line: str) -> dict[str, Any]:
    """One Loki value -> a flat UI-friendly log record (redacted at egress)."""
    message, envelope = _decode_body(line)
    kubernetes = envelope.get("kubernetes") if isinstance(envelope.get("kubernetes"), dict) else {}
    container = (stream.get(LBL_CONTAINER, "") or stream.get("container", "")
                 or kubernetes.get("container_name", ""))
    component, raw_level = parse_body(message, container)
    return {
        "ts": _iso(ts_ns),
        "ts_ns": str(ts_ns),
        "level": raw_level,
        "severity": severity(raw_level),
        "component": component,
        "pod": stream.get(LBL_POD, "") or stream.get("pod", ""),
        "container": container,
        "namespace": stream.get(LBL_NAMESPACE, "") or stream.get("namespace", ""),
        "node": stream.get(LBL_NODE, "") or stream.get("node_name", ""),
        "message": redact(message),
        "raw": redact(line),
    }


def flatten(streams: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    """Loki ``streams`` result -> a flat, newest-first list of records."""
    out: list[dict[str, Any]] = []
    for s in streams:
        labels = s.get("stream", {})
        for ts_ns, line in s.get("values", []):
            out.append(normalize_entry(labels, ts_ns, line))
    out.sort(key=lambda r: r["ts_ns"], reverse=True)
    return out[:limit] if limit else out


# --------------------------------------------------------------------------
# LogQL construction (defense-in-depth: only label/line filters, never exec)
# --------------------------------------------------------------------------
_LABEL_VAL = re.compile(r"[^A-Za-z0-9_.:/\-]")


def _clean_vals(vals: Iterable[str]) -> list[str]:
    """Whitelist label values so they can't break out of the selector."""
    out = []
    for v in vals:
        v = (v or "").strip()
        if v and not _LABEL_VAL.search(v):
            out.append(v)
    return out


def build_selector(
    components: Iterable[str] | None = None,
    levels: Iterable[str] | None = None,
    pod: str | None = None,
    container: str | None = None,
) -> str:
    """Build a LogQL stream selector scoped to this cluster's namespace."""
    parts = [f'{LBL_NAMESPACE}="{S.NS}"']
    # "component" has no dedicated label in the k8s/OTEL schema; the collector's
    # closest structural equivalent is the container name.
    comps = _clean_vals(components or [])
    if comps:
        parts.append(f'{LBL_CONTAINER}=~"{"|".join(comps)}"')
    # Severity is body text in OpenShift/ViaQ, not a stream label. The levels
    # argument remains accepted for API compatibility but is never selected.
    if pod:
        p = _clean_vals([pod])
        if p:
            parts.append(f'{LBL_POD}="{p[0]}"')
    if container:
        c = _clean_vals([container])
        if c:
            parts.append(f'{LBL_CONTAINER}="{c[0]}"')
    return "{" + ", ".join(parts) + "}"


def _escape_line_filter(q: str) -> str:
    """Escape a free-text query for a LogQL ``|=`` line filter (a Go string)."""
    return q.replace("\\", "\\\\").replace('"', '\\"')


def build_query(
    q: str | None = None,
    components: Iterable[str] | None = None,
    levels: Iterable[str] | None = None,
    pod: str | None = None,
    container: str | None = None,
    line_regex: str | None = None,
) -> str:
    sel = build_selector(components, levels, pod, container)
    if line_regex and line_regex.strip():
        return f'{sel} |~ "{_escape_line_filter(line_regex.strip())}"'
    if q and q.strip():
        return f'{sel} |= "{_escape_line_filter(q.strip())}"'
    return sel
