from __future__ import annotations

from app.services import ai_provider


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "ok"}}]}


def _configure(monkeypatch, deployment: str) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://unit-test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", deployment)
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unit-test-key")
    monkeypatch.delenv("AZURE_OPENAI_SEND_TEMPERATURE", raising=False)


def test_gpt5_azure_request_omits_temperature(monkeypatch) -> None:
    _configure(monkeypatch, "gpt-5.6-sol")
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(ai_provider.httpx, "post", fake_post)
    result = ai_provider._azure_openai("test")

    assert result.available is True
    assert "temperature" not in captured["json"]


def test_non_gpt5_azure_request_keeps_temperature(monkeypatch) -> None:
    _configure(monkeypatch, "gpt-4o-mini")
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(ai_provider.httpx, "post", fake_post)
    result = ai_provider._azure_openai("test")

    assert result.available is True
    assert captured["json"]["temperature"] == 0.1


def test_temperature_can_be_explicitly_enabled_for_alias(monkeypatch) -> None:
    _configure(monkeypatch, "gpt-5.6-sol")
    monkeypatch.setenv("AZURE_OPENAI_SEND_TEMPERATURE", "true")
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(ai_provider.httpx, "post", fake_post)
    result = ai_provider._azure_openai("test")

    assert result.available is True
    assert captured["json"]["temperature"] == 0.1
