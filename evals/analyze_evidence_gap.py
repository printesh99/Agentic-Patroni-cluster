#!/usr/bin/env python3
"""Turn the discovery evaluation into an evidence-backed implementation backlog."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT_CAUSES = {
    "available": "transport_or_endpoint",
    "audit": "audit_contract",
    "evidence": "evidence_presence",
    "status": "status_contract",
    "evidence_fields": "typed_evidence_contract",
    "source": "source_registry_or_collector",
    "intent": "planner_or_routing",
    "required_terms": "answer_obligation",
    "claim_binding": "claim_grounding",
    "latency": "performance",
}

PRIORITY = [
    ("P0", "status_contract", "Serve schema-v2 status/sections/missing_evidence on every assistant path."),
    ("P0", "typed_evidence_contract", "Replace raw legacy dictionaries with versioned evidence contracts."),
    ("P0", "claim_grounding", "Bind factual claims to evidence IDs; deterministic fallback on validation failure."),
    ("P1", "source_registry_or_collector", "Add canonical source registry and missing history/range collectors."),
    ("P1", "planner_or_routing", "Plan evidence-condition intents instead of routing conditional probes as live facts."),
    ("P1", "answer_obligation", "Render required value, timestamp, threshold, uncertainty and provenance fields."),
    ("P2", "performance", "Bound slow fallback/model paths and add source-level single-flight/timeouts."),
    ("P2", "audit_contract", "Ensure every outcome, including transport/source failure, has audit metadata."),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    cases = {
        row["id"]: row
        for row in (
            json.loads(line) for line in args.corpus.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    cause_counts: Counter[str] = Counter()
    scenario_counts: dict[str, Counter[str]] = defaultdict(Counter)
    domain_counts: dict[str, Counter[str]] = defaultdict(Counter)
    intent_counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    enriched = []
    for result in report["results"]:
        case = cases[result["id"]]
        causes = sorted({ROOT_CAUSES.get(check, check) for check in result["failed_checks"]})
        for cause in causes:
            cause_counts[cause] += 1
            scenario_counts[case["scenario"]][cause] += 1
            domain_counts[case["category"]][cause] += 1
            if len(examples[cause]) < 3:
                examples[cause].append({
                    "id": result["id"], "question": result["question"],
                    "actual_intent": result["actual_intent"],
                    "failed_checks": result["failed_checks"],
                })
        intent_counts[result["actual_intent"]] += 1
        enriched.append({
            **result,
            "scenario": case["scenario"],
            "root_causes": causes,
            "expected_contract": {
                "intents_any": case.get("expected_intents_any", []),
                "sources_any": case.get("expected_sources_any", []),
                "statuses_any": case.get("expected_status_any", []),
                "evidence_fields_any": case.get("expected_evidence_fields_any", []),
                "required_answer_terms_any": case.get("required_answer_terms_any", []),
                "require_claim_evidence": case.get("require_claim_evidence", False),
            },
            "training_status": "needs_dba_adjudication",
            "training_eligible": False,
        })

    output = {
        "schema_version": 1,
        "source_report": str(args.report),
        "summary": report["summary"],
        "root_cause_counts": dict(cause_counts.most_common()),
        "actual_intent_counts": dict(intent_counts.most_common()),
        "by_scenario": {key: dict(value.most_common()) for key, value in sorted(scenario_counts.items())},
        "by_domain": {key: dict(value.most_common()) for key, value in sorted(domain_counts.items())},
        "examples": examples,
        "prioritized_backlog": [
            {"priority": priority, "root_cause": cause, "count": cause_counts.get(cause, 0),
             "action": action}
            for priority, cause, action in PRIORITY
        ],
        "results": enriched,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "evidence_gap_analysis.json").write_text(
        json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    candidates = [
        {
            "id": row["id"],
            "prompt": row["question"],
            "actual_answer": row["answer"],
            "actual_intent": row["actual_intent"],
            "actual_status": row["actual_status"],
            "root_causes": row["root_causes"],
            "expected_contract": row["expected_contract"],
            "training_status": row["training_status"],
            "training_eligible": row["training_eligible"],
        }
        for row in enriched if not row["passed"]
    ]
    (args.output_dir / "training_candidates_needs_adjudication.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in candidates),
        encoding="utf-8",
    )

    lines = [
        "# AI Assistant Independent Evidence-Gap Analysis",
        "",
        f"- Cases: {report['summary']['total']}",
        f"- Fully passed: {report['summary']['passed']}",
        f"- Critical failures: {report['summary']['critical_failed']}",
        f"- Median latency: {report['summary']['latency_ms']['median']} ms",
        f"- P95 latency: {report['summary']['latency_ms']['p95']} ms",
        "",
        "This is a contract-discovery score, not an LLM accuracy score. Cases intentionally request",
        "stale, conflicting, unavailable, historical and cross-source evidence.",
        "",
        "## Root causes",
        "",
        "| Root cause | Cases |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {cause} | {count} |" for cause, count in cause_counts.most_common())
    lines += ["", "## Prioritized next phase", "", "| Priority | Work | Cases exposed |",
              "| --- | --- | ---: |"]
    for priority, cause, action in PRIORITY:
        lines.append(f"| {priority} | {action} | {cause_counts.get(cause, 0)} |")
    lines += ["", "## Highest-gap domains", "", "| Domain | Total contract failures |",
              "| --- | ---: |"]
    ranked_domains = sorted(
        ((domain, sum(counts.values())) for domain, counts in domain_counts.items()),
        key=lambda item: (-item[1], item[0]),
    )
    lines.extend(f"| {domain} | {count} |" for domain, count in ranked_domains)
    (args.output_dir / "EVIDENCE_GAP_ANALYSIS.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
