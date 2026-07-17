"""Log Analytics service (Phase 4) — summary, signatures, categories, findings.

Queries Loki for the relevant slices, then aggregates in Python via
``log_signatures``. Severity/error volume comes from cheap LogQL metric queries;
signatures/categories pull the (lower-volume) warn+error+fatal lines.
"""
from __future__ import annotations

from typing import Any

from . import loki, log_parse, log_signatures as sig
from . import sources as S

# Levels that matter for "issues" analytics (raw source levels).
_PROBLEM_LEVELS = ["ERROR", "FATAL", "PANIC", "WARNING", "CRITICAL"]
_SIG_LIMIT = 5000
_SUMMARY_LIMIT = 5000


def _problem_query() -> str:
    return log_parse.build_query(line_regex=r"(?i)PANIC|FATAL|ERROR|WARNING|CRITICAL")


def _collect_problems(start_ns: int, end_ns: int, limit: int = _SIG_LIMIT) -> list[dict[str, Any]]:
    streams = loki.query_range(_problem_query(), start_ns, end_ns, limit=limit, direction="backward")
    return [row for row in log_parse.flatten(streams)
            if row["level"] in _PROBLEM_LEVELS or row["severity"] in {"error", "fatal", "warn"}]


def _count_by(label: str, start_ns: int, end_ns: int, step: str) -> dict[str, int]:
    sel = log_parse.build_selector()
    expr = f"sum by ({label}) (count_over_time({sel} [{step}]))"
    out: dict[str, int] = {}
    for s in loki.metric_range(expr, start_ns, end_ns, step=step):
        key = s.get("metric", {}).get(label) or "∅"
        out[key] = out.get(key, 0) + sum(int(float(v)) for _, v in s.get("values", []))
    return out


def summary(start_ns: int, end_ns: int, step: str = "5m") -> dict[str, Any]:
    # Level/component are parsed from ViaQ message bodies, not Loki stream
    # labels. Aggregate normalized entries instead of returning a misleading ∅.
    streams = loki.query_range(log_parse.build_selector(), start_ns, end_ns,
                               limit=_SUMMARY_LIMIT, direction="backward")
    normalized = log_parse.flatten(streams)
    by_level: dict[str, int] = {}
    by_component: dict[str, int] = {}
    for row in normalized:
        by_level[row["level"]] = by_level.get(row["level"], 0) + 1
        by_component[row["component"]] = by_component.get(row["component"], 0) + 1
    # fold raw levels into severities
    by_severity: dict[str, int] = {k: 0 for k in log_parse.SEVERITIES}
    for lvl, n in by_level.items():
        sev = log_parse.severity(lvl)
        by_severity[sev] = by_severity.get(sev, 0) + n
    problems = _collect_problems(start_ns, end_ns)
    errors = [e for e in problems if e["severity"] in ("error", "fatal")]
    sigs = sig.aggregate_signatures(problems)
    # "new since 24h": signatures seen in the recent window but not in the
    # equally-long baseline window immediately before it. Clamp the recent
    # window to the query span so every Loki call keeps start < end.
    day_ns = 24 * 3600 * loki.NS_PER_S
    recent_start = max(start_ns, end_ns - day_ns)
    base_end = recent_start
    base_start = base_end - day_ns
    base_ids = {s["signature_id"]
                for s in sig.aggregate_signatures(_collect_problems(base_start, base_end))}
    new_sigs = [s for s in sig.aggregate_signatures(_collect_problems(recent_start, end_ns))
                if s["signature_id"] not in base_ids]
    return {
        "available": True, "source": "loki", "aggregation_source": "normalized_messages",
        "start_ns": str(start_ns), "end_ns": str(end_ns),
        "total": len(normalized), "truncated": len(normalized) >= _SUMMARY_LIMIT,
        "by_severity": by_severity, "by_level": by_level, "by_component": by_component,
        "error_count": len(errors),
        "last_error_ts": errors[0]["ts"] if errors else None,
        "signature_count": len(sigs),
        "new_signatures_24h": len(new_sigs),
        "new_signatures": new_sigs[:10],
    }


def signatures(start_ns: int, end_ns: int, level: str | None = None,
               component: str | None = None, limit: int = 50) -> dict[str, Any]:
    rows = sig.aggregate_signatures(_collect_problems(start_ns, end_ns))
    if level:
        sev = level.lower()
        rows = [r for r in rows if r["severity"] == sev or sev in r["levels"]]
    if component:
        rows = [r for r in rows if component in r["components"]]
    return {
        "available": True, "source": "loki",
        "start_ns": str(start_ns), "end_ns": str(end_ns),
        "count": len(rows), "signatures": rows[:limit],
    }


def signature_detail(sid: str, start_ns: int, end_ns: int, step: str = "5m") -> dict[str, Any]:
    rows = sig.aggregate_signatures(_collect_problems(start_ns, end_ns))
    match = next((r for r in rows if r["signature_id"] == sid), None)
    if match is None:
        return {"available": False, "source": "loki", "signature_id": sid,
                "reason": "signature not seen in window"}
    # time series: re-query the problem set and bucket entries of this signature
    problems = _collect_problems(start_ns, end_ns)
    samples = [e for e in problems if sig.signature_id(sig.template(e["message"])) == sid]
    bucket_s = _bucket(samples, step)
    return {
        "available": True, "source": "loki", "signature_id": sid,
        "pattern": match["pattern"], "category": match["category"],
        "severity": match["severity"], "count": match["count"],
        "components": match["components"], "levels": match["levels"],
        "first_seen": match["first_seen"], "last_seen": match["last_seen"],
        "explorer_query": _problem_query(),
        "series": bucket_s,
        "samples": [s["message"] for s in samples[:10]],
    }


def _bucket(entries: list[dict[str, Any]], step: str) -> list[dict[str, Any]]:
    step_s = {"1m": 60, "5m": 300, "10m": 600, "1h": 3600}.get(step, 300)
    buckets: dict[int, int] = {}
    for e in entries:
        b = (int(e["ts_ns"]) // loki.NS_PER_S) // step_s * step_s
        buckets[b] = buckets.get(b, 0) + 1
    return [{"ts": b, "count": buckets[b]} for b in sorted(buckets)]


def categories(start_ns: int, end_ns: int) -> dict[str, Any]:
    problems = _collect_problems(start_ns, end_ns)
    counts: dict[str, dict[str, Any]] = {
        c: {"category": c, "count": 0, "error": 0, "warn": 0, "last_seen": None}
        for c in sig.CATEGORIES}
    for e in problems:
        cat = sig.categorize(e["message"])
        row = counts[cat]
        row["count"] += 1
        if e["severity"] in ("error", "fatal"):
            row["error"] += 1
        elif e["severity"] == "warn":
            row["warn"] += 1
        if row["last_seen"] is None or e["ts"] > row["last_seen"]:
            row["last_seen"] = e["ts"]
    rows = [r for r in counts.values() if r["count"] > 0]
    rows.sort(key=lambda r: r["count"], reverse=True)
    return {"available": True, "source": "loki", "categories": rows,
            "total": sum(r["count"] for r in rows)}


def findings(start_ns: int, end_ns: int) -> dict[str, Any]:
    """Derive actionable issues from signatures/categories for the ops inbox."""
    sigs = sig.aggregate_signatures(_collect_problems(start_ns, end_ns))
    out: list[dict[str, Any]] = []
    for s in sigs:
        sev, cat, n = s["severity"], s["category"], s["count"]
        if sev == "fatal" and cat == "authentication":
            out.append(_finding("critical", cat, f"{n} FATAL authentication failures", s))
        elif sev == "fatal":
            out.append(_finding("critical", cat, f"{n} FATAL events: {s['pattern'][:80]}", s))
        elif cat == "lock_deadlock" and n >= 1:
            out.append(_finding("high", cat, f"{n} lock/deadlock events", s))
        elif cat == "disk_space":
            out.append(_finding("critical", cat, f"disk-space pressure: {s['pattern'][:80]}", s))
        elif sev == "error" and n >= 5:
            out.append(_finding("medium", cat, f"{n}× repeated error: {s['pattern'][:80]}", s))
        elif cat == "wal_checkpoint" and sev == "warn" and n >= 5:
            out.append(_finding("low", cat, f"{n} checkpoint warnings (tune checkpoints?)", s))
    out.sort(key=lambda f: _SEV_ORDER.get(f["severity"], 9))
    return {"available": True, "source": "loki", "count": len(out), "findings": out[:50]}


_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _finding(sev: str, cat: str, title: str, s: dict[str, Any]) -> dict[str, Any]:
    return {
        "severity": sev, "category": cat, "title": title,
        "signature_id": s["signature_id"], "count": s["count"],
        "components": s["components"], "last_seen": s["last_seen"],
        "sample": s["sample"],
    }
