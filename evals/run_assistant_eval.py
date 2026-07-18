#!/usr/bin/env python3
"""Grade the real assistant HTTP pipeline against the golden DBA corpus.

Only POSTs to the read-only assistant endpoint. It never calls action endpoints.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import os
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_CORPUS = HERE / "assistant_500.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: {exc}") from exc
    return rows


def response_sources(payload: dict[str, Any]) -> str:
    values = [payload.get("model"), payload.get("provider"), payload.get("backend")]
    values.extend(payload.get("sources_checked") or payload.get("sources") or [])
    values.append(payload.get("evidence"))
    return json.dumps(values, sort_keys=True, default=str).lower()


def grade(case: dict[str, Any], payload: dict[str, Any], latency_ms: float) -> dict[str, Any]:
    answer = str(payload.get("answer") or payload.get("content") or "")
    lower_answer = answer.lower()
    actual_intent = str(payload.get("intent") or "unknown").lower()
    actual_intents = {actual_intent}
    actual_intents.update(str(x).lower() for x in (payload.get("intents") or []))
    expected_intents = [str(x).lower() for x in case.get("expected_intents_any", [])]
    sources = response_sources(payload)
    expected_statuses = [str(x).lower() for x in case.get("expected_status_any", [])]
    actual_status = str(payload.get("status") or "").lower()
    evidence_blob = json.dumps(
        [payload.get("evidence"), payload.get("evidence_items"), payload.get("sections"),
         payload.get("missing_evidence"), payload.get("claims")],
        sort_keys=True, default=str,
    ).lower()
    expected_fields = [str(x).lower() for x in case.get("expected_evidence_fields_any", [])]
    claims = payload.get("claims") or []
    checks = {
        "available": payload.get("available") is not False and bool(answer),
        "intent": not expected_intents or bool(actual_intents.intersection(expected_intents)),
        "source": not case.get("expected_sources_any") or any(
            str(term).lower() in sources for term in case["expected_sources_any"]),
        "status": not expected_statuses or actual_status in expected_statuses,
        "evidence_fields": not expected_fields or any(field in evidence_blob for field in expected_fields),
        "claim_binding": not case.get("require_claim_evidence") or (
            bool(claims) and all(claim.get("evidence_ids") for claim in claims)
        ),
        "required_terms": not case.get("required_answer_terms_any") or any(
            str(term).lower() in lower_answer for term in case["required_answer_terms_any"]),
        "forbidden_terms": not any(
            str(term).lower() in lower_answer for term in case.get("forbidden_answer_terms", [])),
        "latency": latency_ms <= float(case.get("max_latency_ms", 15000)),
        "read_only": payload.get("executed") is not True and not payload.get("mutation_executed", False),
        "audit": payload.get("audit_logged") is True,
        "evidence": payload.get("evidence") is not None or bool(payload.get("sources_checked")),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "id": case["id"], "category": case["category"], "question": case["question"],
        "critical": bool(case.get("critical")), "passed": not failed,
        "failed_checks": failed, "checks": checks, "latency_ms": round(latency_ms, 1),
        "expected_intents_any": expected_intents, "actual_intent": actual_intent,
        "actual_intents": sorted(actual_intents), "actual_status": actual_status,
        "model": payload.get("model"), "provider": payload.get("provider"),
        "response_mode": payload.get("response_mode"), "fallback_used": payload.get("fallback_used"),
        "answer": answer, "evidence_count": payload.get("evidence_count"),
    }


def ask(base_url: str, case: dict[str, Any], timeout: float, token: str | None,
        insecure: bool = False) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api/v1/assistant/ask"
    body = json.dumps({"cluster_id": "uat", "question": case["question"], "stream": False,
                       "time_range": "24h", "range_hours": 24}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "assistant-eval/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    started = time.monotonic()
    try:
        context = ssl._create_unverified_context() if insecure else None
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        payload = {"available": False, "answer": "", "intent": "transport_error",
                   "transport_error": f"{type(exc).__name__}: {exc}"}
    return grade(case, payload, (time.monotonic() - started) * 1000)


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [row for row in results if not row["passed"]]
    critical = [row for row in failures if row["critical"]]
    latencies = [row["latency_ms"] for row in results]
    by_category: dict[str, dict[str, int]] = {}
    for row in results:
        item = by_category.setdefault(row["category"], {"total": 0, "passed": 0, "failed": 0})
        item["total"] += 1
        item["passed" if row["passed"] else "failed"] += 1
    return {
        "total": len(results), "passed": len(results) - len(failures), "failed": len(failures),
        "critical_failed": len(critical),
        "pass_rate": round(100 * (len(results) - len(failures)) / max(1, len(results)), 2),
        "latency_ms": {"median": round(statistics.median(latencies), 1) if latencies else 0,
                       "p95": sorted(latencies)[min(len(latencies)-1, int(len(latencies)*.95))] if latencies else 0,
                       "max": max(latencies, default=0)},
        "failed_checks": dict(Counter(check for row in failures for check in row["failed_checks"])),
        "by_category": by_category,
    }


def write_html(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    rows = []
    for item in report["results"]:
        tone = "pass" if item["passed"] else "fail"
        rows.append("<tr class='%s'><td>%s</td><td>%s</td><td>%s</td><td>%.1f</td><td>%s</td><td><details><summary>answer</summary><pre>%s</pre></details></td></tr>" % (
            tone, html.escape(item["id"]), html.escape(item["category"]),
            html.escape(", ".join(item["failed_checks"]) or "PASS"), item["latency_ms"],
            html.escape(item["actual_intent"]), html.escape(item["answer"])))
    doc = """<!doctype html><meta charset='utf-8'><title>DBA Assistant Evaluation</title>
<style>body{font:14px system-ui;margin:24px;color:#17202a}table{border-collapse:collapse;width:100%%}th,td{border:1px solid #ccd;padding:6px;vertical-align:top}.fail{background:#ffe8e8}.pass{background:#effaf0}pre{white-space:pre-wrap;max-width:900px}.cards{display:flex;gap:16px}.card{padding:12px;border:1px solid #ccd;border-radius:6px}</style>
<h1>DBA Assistant Evaluation</h1><div class='cards'><div class='card'>Pass rate<br><b>%.2f%%</b></div><div class='card'>Passed<br><b>%d/%d</b></div><div class='card'>Critical failures<br><b>%d</b></div><div class='card'>P95 latency<br><b>%sms</b></div></div>
<h2>Results</h2><table><thead><tr><th>ID</th><th>Category</th><th>Checks</th><th>Latency ms</th><th>Intent</th><th>Response</th></tr></thead><tbody>%s</tbody></table>""" % (
        summary["pass_rate"], summary["passed"], summary["total"], summary["critical_failed"],
        summary["latency_ms"]["p95"], "".join(rows))
    path.write_text(doc, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Console origin, e.g. https://object-monitor.example")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output-dir", type=Path, default=HERE / "reports")
    parser.add_argument("--categories", help="Comma-separated category filter")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--min-pass-rate", type=float, default=95.0)
    parser.add_argument("--allow-critical-failures", type=int, default=0)
    parser.add_argument("--token-env", default="ASSISTANT_EVAL_TOKEN")
    parser.add_argument(
        "--insecure", action="store_true",
        help="Disable TLS certificate verification for an explicitly trusted UAT route only",
    )
    args = parser.parse_args()
    cases = load_jsonl(args.corpus)
    if args.categories:
        wanted = set(args.categories.split(","))
        cases = [row for row in cases if row["category"] in wanted]
    if args.limit:
        cases = cases[:args.limit]
    token = os.environ.get(args.token_env)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(
            lambda case: ask(args.base_url, case, args.timeout, token, args.insecure), cases))
    results.sort(key=lambda row: row["id"])
    report = {"schema_version": 1, "generated_at_unix": int(time.time()),
              "base_url": args.base_url, "summary": summarize(results), "results": results}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path, html_path = args.output_dir / "assistant_eval.json", args.output_dir / "assistant_eval.html"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    write_html(html_path, report)
    summary = report["summary"]
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"JSON: {json_path}\nHTML: {html_path}")
    return 0 if (summary["pass_rate"] >= args.min_pass_rate and
                 summary["critical_failed"] <= args.allow_critical_failures) else 1


if __name__ == "__main__":
    sys.exit(main())
