from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import httpx

try:  # The focused unit tests mock every SQL/Kubernetes call.
    import psycopg  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - local lightweight test env
    sys.modules["psycopg"] = types.SimpleNamespace(connect=None)

from app import assistant_tools, log_ai, pg_ops, sources as S
from app.services import ai_provider


class _SuccessResponse:
    status_code = 200
    headers = {"apim-request-id": "safe-request-123"}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "model answer"}}]}


def _configure_azure(monkeypatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "azure_openai")
    monkeypatch.setenv("AI_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://unit-test.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.6-sol")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unit-test-secret-key")


def test_provider_success_has_safe_attribution_and_metadata_log(monkeypatch, caplog) -> None:
    _configure_azure(monkeypatch)
    monkeypatch.setattr(ai_provider.httpx, "post", lambda *a, **k: _SuccessResponse())
    caplog.set_level(logging.INFO, logger="app.services.ai_provider")

    result = ai_provider.generate_rca("sensitive prompt must never be logged")

    assert result.available is True
    assert result.provider == "azure_openai"
    assert result.model == "gpt-5.6-sol"
    assert result.http_status == 200
    assert result.request_id == "safe-request-123"
    assert result.latency_ms is not None
    log_text = caplog.text
    assert "ai_provider_result" in log_text
    assert "available=True" in log_text
    assert "sensitive prompt" not in log_text
    assert "unit-test-secret-key" not in log_text
    assert "unit-test.openai.azure.com" not in log_text
    assert "model answer" not in log_text


def test_provider_429_is_classified_without_url_key_or_raw_body(monkeypatch, caplog) -> None:
    _configure_azure(monkeypatch)
    request = httpx.Request(
        "POST",
        "https://unit-test.openai.azure.com/openai/deployments/gpt-5.6-sol/chat/completions",
    )
    response = httpx.Response(
        429,
        request=request,
        headers={"apim-request-id": "rate-limit-123"},
        json={"error": {"message": "quota exceeded for secret tenant"}},
    )
    monkeypatch.setattr(ai_provider.httpx, "post", lambda *a, **k: response)
    caplog.set_level(logging.INFO, logger="app.services.ai_provider")

    result = ai_provider.generate_rca("why did failover happen")

    assert result.available is False
    assert result.error_category == "HTTP_429"
    assert result.http_status == 429
    assert result.request_id == "rate-limit-123"
    assert result.error == "provider request failed with HTTP 429"
    combined = result.error + caplog.text
    assert "unit-test.openai.azure.com" not in combined
    assert "unit-test-secret-key" not in combined
    assert "quota exceeded" not in combined


def test_provider_timeout_is_classified(monkeypatch) -> None:
    _configure_azure(monkeypatch)
    request = httpx.Request("POST", "https://unit-test.openai.azure.com")

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timed out at https://unit-test.openai.azure.com?api-key=secret", request=request)

    monkeypatch.setattr(ai_provider.httpx, "post", timeout)
    result = ai_provider.generate_rca("why")

    assert result.available is False
    assert result.error_category == "PROVIDER_TIMEOUT"
    assert result.error == "provider request timed out"
    assert "http" not in result.error
    assert "secret" not in result.error


def _set_cluster_constants(monkeypatch) -> None:
    monkeypatch.setattr(S, "NS", "uat-pgcluster-uae", raising=False)
    monkeypatch.setattr(S, "CLUSTER_NAME", "uat-pgcluster-uae", raising=False)
    monkeypatch.setattr(S, "DB_CONTAINER", "database", raising=False)


def test_cpu_question_returns_exact_pod_allocation_and_usage(monkeypatch) -> None:
    _set_cluster_constants(monkeypatch)
    pod_doc = {
        "items": [
            {
                "metadata": {"name": "uat-db-0"},
                "status": {"phase": "Running"},
                "spec": {"containers": [{
                    "name": "database",
                    "resources": {"requests": {"cpu": "16"}, "limits": {"cpu": "16"}},
                }]},
            },
            {
                "metadata": {"name": "uat-db-1"},
                "status": {"phase": "Running"},
                "spec": {"containers": [{
                    "name": "database",
                    "resources": {"requests": {"cpu": "16000m"}, "limits": {"cpu": "16"}},
                }]},
            },
        ]
    }
    monkeypatch.setattr(S, "kubectl_json", lambda *a, **k: pod_doc)
    monkeypatch.setattr(S, "prom_scalar", lambda *a, **k: 3.25)

    result = assistant_tools.route("how many CPU on my current UAT UAE cluster")

    assert result is not None
    assert result["intent"] == "cpu_capacity"
    assert result["model"] == "live-data (OpenShift pod specs + Prometheus)"
    assert result["evidence"]["pod_count"] == 2
    assert result["evidence"]["total_request_cores"] == 32
    assert result["evidence"]["total_limit_cores"] == 32
    assert result["evidence"]["current_usage_cores_5m"] == 3.25
    assert "Total CPU request: 32 cores" in result["answer"]
    assert "Current five-minute CPU usage: 3.25 cores" in result["answer"]


def test_cpu_question_never_invents_default_when_specs_unavailable(monkeypatch) -> None:
    _set_cluster_constants(monkeypatch)

    def unavailable(*args, **kwargs):
        raise S.SourceError("forbidden")

    monkeypatch.setattr(S, "kubectl_json", unavailable)
    result = assistant_tools.route("how many CPU on this cluster")

    assert result is not None
    assert result["intent"] == "cpu_capacity"
    assert result["evidence"]["pod_specs_available"] is False
    assert "No CPU count is inferred" in result["answer"]
    assert "16 cores" not in result["answer"]


def test_deterministic_answer_marks_provider_not_attempted(monkeypatch) -> None:
    monkeypatch.setattr(
        assistant_tools,
        "route",
        lambda q: {
            "answer": "32 cores from pod specs",
            "model": "live-data (OpenShift pod specs)",
            "intent": "cpu_capacity",
            "evidence": {"total_limit_cores": 32},
        },
    )
    monkeypatch.setattr(log_ai.jobs, "_audit", lambda *a, **k: None)

    result = log_ai.ask("how many CPU", 1, 2)

    assert result["response_mode"] == "deterministic"
    assert result["provider_attempted"] is False
    assert result["fallback_used"] is False


def test_heuristic_fallback_returns_safe_structured_reason(monkeypatch) -> None:
    monkeypatch.setattr(log_ai, "_claude_available", lambda: False)
    monkeypatch.setattr(
        log_ai.ai_provider,
        "generate_rca",
        lambda *a, **k: ai_provider.ProviderResult(
            False,
            "azure_openai",
            "gpt-5.6-sol",
            "",
            "provider request failed with HTTP 429",
            error_category="HTTP_429",
            http_status=429,
            latency_ms=321,
            request_id="safe-id",
        ),
    )
    readiness = {"items": [], "summary": {"score": 100}}
    context = {"intent": "failover", "signatures": [], "categories": [], "entries": []}

    result = log_ai.summarize("why", readiness, context)

    assert result["model"] == "heuristic"
    assert result["response_mode"] == "heuristic_fallback"
    assert result["fallback_used"] is True
    assert result["fallback_reason_code"] == "HTTP_429"
    assert result["provider_http_status"] == 429
    assert "HTTP_429" in result["answer"]
    assert "http://" not in result["answer"]
    assert "https://" not in result["answer"]


def test_full_assistant_payload_preserves_llm_attribution(monkeypatch) -> None:
    monkeypatch.setattr(assistant_tools, "route", lambda q: None)
    monkeypatch.setattr(log_ai, "_live_cluster_answer", lambda q: None)
    monkeypatch.setattr(
        log_ai,
        "gather_context",
        lambda *a: {
            "intent": "failover",
            "entries": [],
            "signatures": [],
            "categories": [],
        },
    )
    monkeypatch.setattr(pg_ops, "readiness", lambda: {"items": [], "summary": {"score": 100}})
    monkeypatch.setattr(
        log_ai,
        "summarize",
        lambda *a: {
            "answer": "grounded model answer",
            "model": "azure_openai:gpt-5.6-sol",
            "provider_attempted": True,
            "provider": "azure_openai",
            "response_mode": "llm",
            "fallback_used": False,
            "fallback_reason_code": None,
            "provider_http_status": 200,
            "provider_latency_ms": 456,
            "provider_request_id": "safe-request-456",
        },
    )
    monkeypatch.setattr(log_ai.jobs, "_audit", lambda *a, **k: None)

    result = log_ai.ask("why did the earlier failover happen", 1, 2)

    assert result["model"] == "azure_openai:gpt-5.6-sol"
    assert result["provider_attempted"] is True
    assert result["provider"] == "azure_openai"
    assert result["response_mode"] == "llm"
    assert result["fallback_used"] is False
    assert result["provider_http_status"] == 200
    assert result["provider_latency_ms"] == 456
    assert result["provider_request_id"] == "safe-request-456"


def test_frontend_preserves_model_metadata_and_has_no_stale_provider_branding() -> None:
    root = Path(__file__).resolve().parents[1]
    assistant = (root / "static" / "assistant.jsx").read_text(encoding="utf-8")
    app = (root / "static" / "app.jsx").read_text(encoding="utf-8")

    assert "responseAttribution" in assistant
    assert "body.model || null" in assistant
    assert "body.response_mode || null" in assistant
    assert "body.provider_attempted" in assistant
    assert "body.provider_http_status" in assistant
    assert "body.provider_request_id" in assistant
    assert "evt.fallback_reason_code" in assistant
    assert "evt.provider_http_status" in assistant
    assert "evt.provider_request_id" in assistant
    assert "Claude" not in assistant
    assert "Vertex AI" not in assistant
    assert "Claude" not in app
    assert "Vertex AI" not in app
