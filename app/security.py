"""Trusted caller identity boundary for privileged APIs."""
from __future__ import annotations

from dataclasses import dataclass
import hmac
import os
import ipaddress

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
    next_token = os.getenv("AGENTIC_SERVICE_TOKEN_NEXT", "")
    supplied = request.headers.get("x-agentic-service-token", "")
    if supplied and any(token and hmac.compare_digest(token, supplied) for token in (expected, next_token)):
        return Principal("service:agentic", "agentic-service", frozenset({"service"}),
                         "service-token", "service", True)
    bearer = request.headers.get("authorization", "")
    if bearer.lower().startswith("bearer ") and os.getenv("JWT_ISSUER") and os.getenv("JWT_AUDIENCE"):
        try:
            import jwt
            token = bearer.split(None, 1)[1]
            key = os.getenv("JWT_PUBLIC_KEY") or os.getenv("JWT_HS256_SECRET")
            algorithm = os.getenv("JWT_ALGORITHM", "RS256")
            if not key or algorithm not in {"RS256", "ES256", "HS256"}:
                return None
            claims = jwt.decode(token, key, algorithms=[algorithm], audience=os.environ["JWT_AUDIENCE"],
                                issuer=os.environ["JWT_ISSUER"], options={"require": ["exp", "iat", "sub"]})
            strength = str(claims.get("acr") or claims.get("amr") or "")
            if os.getenv("JWT_REQUIRED_ACR") and os.getenv("JWT_REQUIRED_ACR") != strength:
                return None
            roles = claims.get("groups") or claims.get("roles") or []
            if isinstance(roles, str):
                roles = roles.split(",")
            return Principal(str(claims["sub"])[:255], str(claims.get("name") or claims["sub"])[:255],
                             frozenset(str(x).strip().lower() for x in roles if str(x).strip()),
                             "signed-jwt", strength or None, bool(claims.get("service_account", False)))
        except Exception:
            return None
    if os.getenv("TRUSTED_IDENTITY_HEADERS", "false").lower() not in {"1", "true", "yes", "on"}:
        return None
    secret = os.getenv("TRUSTED_PROXY_SHARED_SECRET", "")
    supplied_secret = request.headers.get("x-trusted-proxy-secret", "")
    client = request.client.host if request.client else ""
    cidrs = [x.strip() for x in os.getenv("TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128").split(",") if x.strip()]
    try:
        trusted_source = any(ipaddress.ip_address(client) in ipaddress.ip_network(c, strict=False) for c in cidrs)
    except ValueError:
        trusted_source = False
    if not (secret and supplied_secret and hmac.compare_digest(secret, supplied_secret) and trusted_source):
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
