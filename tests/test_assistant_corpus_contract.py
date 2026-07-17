from pathlib import Path

from evals.run_assistant_eval import grade, load_jsonl


def test_every_golden_case_is_machine_gradable_with_a_matching_fixture():
    corpus = load_jsonl(Path(__file__).resolve().parents[1] / "evals" / "assistant_500.jsonl")
    for case in corpus:
        intent = case["expected_intents_any"][0]
        source = case["expected_sources_any"][0] if case["expected_sources_any"] else "evidence"
        required = case["required_answer_terms_any"][0] if case["required_answer_terms_any"] else "evidence"
        payload = {"available": True, "intent": intent, "answer": f"{required}: fixture",
                   "model": f"fixture ({source})", "evidence": {"source": source},
                   "audit_logged": True, "provider": "read_only_tools"}
        result = grade(case, payload, 1)
        assert result["passed"], (case["id"], result["failed_checks"])
