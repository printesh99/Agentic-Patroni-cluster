from pathlib import Path

from evals.run_assistant_eval import grade, load_jsonl, summarize


ROOT = Path(__file__).resolve().parents[1]


def test_golden_corpus_has_500_unique_complete_cases():
    rows = load_jsonl(ROOT / "evals" / "assistant_500.jsonl")
    assert len(rows) == 500
    assert len({row["id"] for row in rows}) == 500
    assert len({row["category"] for row in rows}) == 25
    required = {"id", "category", "question", "expected_intents_any",
                "expected_sources_any", "critical", "read_only", "max_latency_ms"}
    assert all(required <= row.keys() for row in rows)
    assert all(row["read_only"] is True for row in rows)


def test_evidence_gap_corpus_is_independent_and_contract_complete():
    old = load_jsonl(ROOT / "evals" / "assistant_500.jsonl")
    rows = load_jsonl(ROOT / "evals" / "assistant_evidence_gap_500.jsonl")
    assert len(rows) == 500
    assert len({row["id"] for row in rows}) == 500
    assert len({row["question"].lower() for row in rows}) == 500
    assert len({row["category"] for row in rows}) == 25
    assert len({row["scenario"] for row in rows}) == 20
    assert not ({row["question"].lower() for row in old}
                & {row["question"].lower() for row in rows})
    required = {
        "expected_status_any", "expected_evidence_fields_any", "expected_sources_any",
        "require_claim_evidence", "scenario",
    }
    assert all(required <= row.keys() for row in rows)


def test_evaluator_grades_contract():
    case = {"id": "x", "category": "wal", "question": "q", "critical": True,
            "expected_intents_any": ["wal_archive"], "expected_sources_any": ["pg_stat_archiver"],
            "required_answer_terms_any": ["wal"], "forbidden_answer_terms": ["loki"],
            "max_latency_ms": 1000}
    payload = {"available": True, "intent": "wal_archive", "answer": "Latest WAL is 0001",
               "model": "live-data (pg_stat_archiver)", "evidence": {"wal": "0001"},
               "audit_logged": True, "provider": "read_only_tools"}
    result = grade(case, payload, 20)
    assert result["passed"]
    assert summarize([result])["pass_rate"] == 100.0
