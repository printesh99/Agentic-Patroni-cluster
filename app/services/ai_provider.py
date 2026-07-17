"""Environment-driven AI provider abstraction for the DBA agent.

The agent must keep working when no model provider is configured, so this module
returns deterministic fallback text unless an explicit provider is available.
"""
from __future__ import annotations

import os
import logging
import re
import time
from dataclasses import dataclass, replace
from typing import Any

import httpx


_TRUE_VALUES = {"1", "true", "yes", "on"}
logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|password|token|authorization|client_secret)\s*[:=]\s*\S+"
)
_SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


@dataclass
class ProviderResult:
    available: bool
    provider: str
    model: str | None
    content: str
    error: str | None = None
    error_category: str | None = None
    http_status: int | None = None
    latency_ms: int | None = None
    request_id: str | None = None


def _safe_request_id(value: Any) -> str | None:
    candidate = str(value or "").strip()
    return candidate if _SAFE_REQUEST_ID_RE.fullmatch(candidate) else None


def _response_request_id(response: Any) -> str | None:
    headers = getattr(response, "headers", {}) or {}
    for name in ("apim-request-id", "x-request-id", "request-id", "x-ms-request-id"):
        value = headers.get(name)
        if value:
            return _safe_request_id(value)
    return None


def _http_error_category(status: int) -> str:
    if status == 400:
        return "HTTP_400"
    if status == 401:
        return "HTTP_401"
    if status == 403:
        return "HTTP_403"
    if status == 429:
        return "HTTP_429"
    if 500 <= status <= 599:
        return "HTTP_5XX"
    return "HTTP_ERROR"


def _safe_error_text(value: Any, category: str | None = None, status: int | None = None) -> str:
    """Return a bounded diagnostic that cannot contain provider URLs or keys."""
    if status is not None:
        return f"provider request failed with HTTP {status}"
    if category == "PROVIDER_TIMEOUT":
        return "provider request timed out"
    if category == "PROVIDER_CONNECTIVITY":
        return "provider connection failed"
    if category == "EMPTY_RESPONSE":
        return "provider returned an empty response"
    text = _URL_RE.sub("<REDACTED_URL>", str(value or ""))
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=<REDACTED>", text)
    # Keep only known-safe configuration explanations. Other exception strings
    # can contain request URLs or response bodies and are reduced to category.
    if any(marker in text for marker in (
        "is not configured", "AI provider disabled", "AI_AGENT_LLM_ENABLED is false",
        "unsupported AI_PROVIDER",
    )):
        return text[:300]
    return (category or "PROVIDER_ERROR").replace("_", " ").lower()


def _failure_from_exception(
    provider: str,
    model: str | None,
    exc: Exception,
    started: float,
) -> ProviderResult:
    status = None
    request_id = None
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code
        request_id = _response_request_id(response)
        category = _http_error_category(status)
    elif isinstance(exc, httpx.TimeoutException) or "timeout" in type(exc).__name__.lower():
        category = "PROVIDER_TIMEOUT"
    elif isinstance(exc, httpx.RequestError):
        category = "PROVIDER_CONNECTIVITY"
    else:
        maybe_status = getattr(exc, "status_code", None)
        if isinstance(maybe_status, int):
            status = maybe_status
            category = _http_error_category(status)
        else:
            category = "PROVIDER_ERROR"
    return ProviderResult(
        False,
        provider,
        model,
        "",
        _safe_error_text(exc, category, status),
        error_category=category,
        http_status=status,
        latency_ms=max(0, round((time.monotonic() - started) * 1000)),
        request_id=request_id,
    )


def _finalize_result(result: ProviderResult, started: float) -> ProviderResult:
    latency_ms = result.latency_ms
    if latency_ms is None:
        latency_ms = max(0, round((time.monotonic() - started) * 1000))
    category = result.error_category
    error = result.error
    if not result.available and category is None:
        low = str(error or "").lower()
        if "not configured" in low or "disabled" in low or "unsupported" in low:
            category = "CONFIGURATION_ERROR"
        elif "empty" in low:
            category = "EMPTY_RESPONSE"
        else:
            category = "PROVIDER_ERROR"
    if error:
        error = _safe_error_text(error, category, result.http_status)
    final = replace(
        result,
        error=error,
        error_category=category,
        latency_ms=latency_ms,
        request_id=_safe_request_id(result.request_id),
    )
    # Metadata only. Never log prompt, answer, evidence, URL, headers, key, or
    # raw provider response/error body.
    logger.info(
        "ai_provider_result provider=%s model=%s available=%s error_category=%s "
        "http_status=%s latency_ms=%s request_id=%s",
        final.provider,
        final.model,
        final.available,
        final.error_category or "NONE",
        final.http_status if final.http_status is not None else "NONE",
        final.latency_ms,
        final.request_id or "NONE",
    )
    return final


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def provider_status() -> dict[str, Any]:
    provider = (os.environ.get("AI_PROVIDER") or "disabled").strip().lower()
    model = os.environ.get("AI_MODEL") or os.environ.get("ANTHROPIC_MODEL") or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    configured = provider not in {"", "disabled", "none", "off"}
    has_key = bool(
        os.environ.get("AI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("AZURE_OPENAI_API_KEY")
    )
    return {
        "provider": provider or "disabled",
        "model": model,
        "configured": configured,
        "api_key_present": has_key,
        "base_url": _safe_base_url(),
    }


def embed(texts: list[str], timeout_s: float = 60.0) -> list[list[float]]:
    """Return Azure 1536-d embeddings; fail closed so watermarks never advance."""
    if not texts:
        return []
    endpoint = (os.environ.get("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
    deployment = os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT") or ""
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-12-01-preview"
    key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AI_API_KEY") or ""
    if not endpoint or not deployment or not key:
        raise RuntimeError("Azure embedding deployment is not configured")
    response = httpx.post(
        f"{endpoint}/openai/deployments/{deployment}/embeddings",
        params={"api-version": api_version}, headers={"api-key": key},
        json={"input": texts}, timeout=timeout_s,
    )
    response.raise_for_status()
    rows = sorted(response.json().get("data") or [], key=lambda row: row.get("index", 0))
    vectors = [row.get("embedding") or [] for row in rows]
    if len(vectors) != len(texts) or any(len(vector) != 1536 for vector in vectors):
        raise RuntimeError("embedding provider returned an invalid vector shape")
    return vectors


def _safe_base_url() -> str | None:
    raw = os.environ.get("AI_BASE_URL") or os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not raw:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(raw)
        return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None
    except Exception:
        return None


def _openai_compatible(prompt: str, timeout_s: float | None = None) -> ProviderResult:
    provider = (os.environ.get("AI_PROVIDER") or "openai").strip().lower()
    model = os.environ.get("AI_MODEL") or "gpt-4o-mini"
    key = os.environ.get("AI_API_KEY") or ""
    base = (os.environ.get("AI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    if not key and provider != "local":
        return ProviderResult(False, provider, model, "", "AI_API_KEY is not configured")
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a read-only PostgreSQL DBA recommendation assistant. "
                    "Do not propose destructive actions. Keep output concise and evidence based."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    # CPU-only local models (e.g. Ollama on a 32-vCPU pod, no GPU) can take
    # well over a minute per RCA-length answer — the hosted-API default of 20s
    # only fits a real remote provider. AI_REQUEST_TIMEOUT_S is env-tunable
    # per provider; "local" gets a much longer default.
    default_timeout = 300 if provider == "local" else 20
    timeout = timeout_s if timeout_s is not None else _env_float("AI_REQUEST_TIMEOUT_S", default_timeout)
    max_tokens = _env_int("AI_MAX_TOKENS", 320 if provider == "local" else 700)
    body["max_tokens"] = max_tokens
    try:
        response = httpx.post(f"{base}/chat/completions", headers=headers, json=body, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return ProviderResult(True, provider, model, str(content or "").strip())
    except Exception as exc:
        return ProviderResult(False, provider, model, "", str(exc))


def _ollama_native(prompt: str, timeout_s: float | None = None) -> ProviderResult:
    provider = "local"
    model = os.environ.get("AI_MODEL") or "object-monitor-llm"
    base = (os.environ.get("AI_BASE_URL") or "http://object-monitor-llm.monitoring.svc:11434").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    timeout = timeout_s if timeout_s is not None else _env_float("AI_REQUEST_TIMEOUT_S", 300)
    max_tokens = _env_int("AI_MAX_TOKENS", 320)
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": max_tokens,
        },
    }
    try:
        response = httpx.post(f"{base}/api/generate", json=body, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        content = str(payload.get("response") or "").strip()
        if not content:
            return ProviderResult(False, provider, model, "", "Ollama returned an empty response")
        return ProviderResult(True, provider, model, content)
    except Exception as exc:
        return ProviderResult(False, provider, model, "", str(exc))


def _azure_openai(prompt: str, timeout_s: float | None = None) -> ProviderResult:
    provider = "azure_openai"
    endpoint = (os.environ.get("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or os.environ.get("AI_MODEL")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-02-15-preview"
    key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AI_API_KEY") or ""
    if not endpoint or not deployment or not key:
        return ProviderResult(
            False, provider, deployment, "",
            "Azure OpenAI endpoint/deployment/key is not configured",
            error_category="CONFIGURATION_ERROR",
        )
    body = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a read-only PostgreSQL DBA recommendation assistant. "
                    "Do not propose destructive actions. Keep output concise and evidence based."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    # GPT-5-family Azure deployments reject custom temperature values and
    # accept only their service default.  Deployment names are customer
    # controlled, so an explicit env override remains available for unusual
    # aliases; the safe default for a gpt-5* deployment is to omit the field.
    deployment_family = deployment.strip().lower()
    send_temperature_default = not deployment_family.startswith("gpt-5")
    if _env_bool("AZURE_OPENAI_SEND_TEMPERATURE", send_temperature_default):
        body["temperature"] = 0.1
    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions"
    started = time.monotonic()
    try:
        timeout = timeout_s if timeout_s is not None else _env_float("AI_REQUEST_TIMEOUT_S", 20)
        response = httpx.post(
            url,
            params={"api-version": api_version},
            headers={"api-key": key},
            json=body,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        content = str(payload["choices"][0]["message"]["content"] or "").strip()
        if not content:
            return ProviderResult(
                False, provider, deployment, "", "provider returned an empty response",
                error_category="EMPTY_RESPONSE",
                http_status=getattr(response, "status_code", 200),
                latency_ms=max(0, round((time.monotonic() - started) * 1000)),
                request_id=_response_request_id(response),
            )
        return ProviderResult(
            True,
            provider,
            deployment,
            content,
            http_status=getattr(response, "status_code", 200),
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
            request_id=_response_request_id(response),
        )
    except Exception as exc:
        return _failure_from_exception(provider, deployment, exc, started)


def _anthropic(prompt: str) -> ProviderResult:
    provider = "anthropic"
    model = os.environ.get("AI_MODEL") or os.environ.get("ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest"
    key = os.environ.get("AI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""
    if not key:
        return ProviderResult(False, provider, model, "", "ANTHROPIC_API_KEY is not configured")
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": 700,
            "system": (
                "You are a read-only PostgreSQL DBA recommendation assistant. "
                "Do not propose destructive actions. Keep output concise and evidence based."
            ),
            "messages": [{"role": "user", "content": prompt}],
        }
        model_family = tuple(model.split("-", 3)[:3])
        no_temperature_models = {
            ("claude", "sonnet", "5"),
            ("claude", "opus", "5"),
            ("claude", "fable", "5"),
        }
        if model_family not in no_temperature_models:
            request["temperature"] = 0.1
        msg = client.messages.create(**request)
        chunks = []
        for block in msg.content:
            text = getattr(block, "text", None)
            if text:
                chunks.append(text)
        return ProviderResult(True, provider, model, "\n".join(chunks).strip())
    except Exception as exc:
        return ProviderResult(False, provider, model, "", str(exc))


def generate_rca(prompt: str, timeout_s: float | None = None) -> ProviderResult:
    """Run the configured provider. ``timeout_s`` bounds the local (Ollama /
    OpenAI-compatible) HTTP call, overriding AI_REQUEST_TIMEOUT_S — used by the
    interactive assistant so a slow CPU model degrades to the heuristic instead
    of hanging the request past the route timeout (504). ``None`` keeps the
    long background-agent default."""
    started = time.monotonic()
    provider = (os.environ.get("AI_PROVIDER") or "disabled").strip().lower()
    if not provider or provider in {"disabled", "none", "off"}:
        result = ProviderResult(
            False, "disabled", os.environ.get("AI_MODEL"), "", "AI provider disabled",
            error_category="CONFIGURATION_ERROR",
        )
    elif not _env_bool("AI_AGENT_LLM_ENABLED", True):
        result = ProviderResult(
            False, provider, os.environ.get("AI_MODEL"), "", "AI_AGENT_LLM_ENABLED is false",
            error_category="CONFIGURATION_ERROR",
        )
    elif provider == "azure_openai":
        result = _azure_openai(prompt, timeout_s=timeout_s)
    elif provider == "anthropic":
        result = _anthropic(prompt)
    elif provider == "local":
        mode = (os.environ.get("AI_LOCAL_API_MODE") or "ollama").strip().lower()
        if mode in {"openai", "openai-compatible", "openai_compatible"}:
            result = _openai_compatible(prompt, timeout_s=timeout_s)
        else:
            result = _ollama_native(prompt, timeout_s=timeout_s)
    elif provider == "openai":
        result = _openai_compatible(prompt, timeout_s=timeout_s)
    else:
        result = ProviderResult(
            False, provider, os.environ.get("AI_MODEL"), "", f"unsupported AI_PROVIDER {provider}",
            error_category="CONFIGURATION_ERROR",
        )
    return _finalize_result(result, started)
