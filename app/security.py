"""Trusted caller identity boundary for privileged APIs."""
from __future__ import annotations

from dataclasses import dataclass
import hmac
import os

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Principal:
    subject_id: str
    display_name: str
    roles: frozenset[str]
    auth_source: str
    auth_strength: str | None = None
    service_account: bool = False


def principal_from_request(request: Request) -> Principal | None:
    expected = os.getenv("AGENTIC_SERVICE_TOKEN", "")
    supplied = request.headers.get("x-agentic-service-token", "")
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return Principal("service:agentic", "agentic-service", frozenset({"service"}),
                         "service-token", "service", True)
    if os.getenv("TRUSTED_IDENTITY_HEADERS", "false").lower() not in {"1", "true", "yes", "on"}:
        return None
    subject = request.headers.get("x-forwarded-user") or request.headers.get("x-remote-user")
    if not subject:
        return None
    roles = frozenset(x.strip().lower() for x in request.headers.get("x-forwarded-groups", "").split(",") if x.strip())
    return Principal(subject[:255], subject[:255], roles, "trusted-proxy",
                     request.headers.get("x-auth-strength"), False)


def require_principal(request: Request) -> Principal:
    principal = principal_from_request(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="trusted authenticated identity required")
    return principal
