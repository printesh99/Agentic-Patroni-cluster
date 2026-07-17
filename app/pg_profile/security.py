"""Secret redaction, identity enforcement, and report HTML isolation."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
import hmac
import os
import re

from fastapi import HTTPException, Request

from .config import settings

_SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[=:]\s*([^\s,;]+)"),
    re.compile(r"(?i)(postgres(?:ql)?://)[^@\s]+@"),
]


def sanitize_error(value: object, limit: int = 500) -> str:
    text = str(value).replace("\x00", " ").replace("\n", " ")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) + "=<REDACTED>") if m.lastindex == 2 else m.group(1) + "<REDACTED>@", text)
    return text[:limit]


@dataclass(frozen=True)
class Actor:
    name: str
    roles: frozenset[str]
    service: bool = False


def actor_from_request(request: Request) -> Actor | None:
    configured = os.getenv(settings.service_token_env, "")
    supplied = request.headers.get("x-pgprofile-service-token", "")
    if configured and supplied and hmac.compare_digest(configured, supplied):
        return Actor("pgprofile-service", frozenset({"service"}), service=True)
    if not settings.trusted_identity_headers:
        return None
    user = request.headers.get("x-forwarded-user") or request.headers.get("x-remote-user")
    roles = request.headers.get("x-forwarded-groups", "")
    if not user:
        return None
    return Actor(user[:255], frozenset(x.strip().lower() for x in roles.split(",") if x.strip()))


def require_dba(request: Request) -> Actor:
    actor = actor_from_request(request)
    if actor is None:
        raise HTTPException(status_code=401, detail="trusted pg_profile identity required")
    if not actor.service and not (actor.roles & {"platform-admin", "dba", "senior-dba", "sre"}):
        raise HTTPException(status_code=403, detail="DBA role required")
    return actor


class _Sanitizer(HTMLParser):
    blocked = {"script", "iframe", "object", "embed", "form", "input", "button", "base", "link", "meta"}
    url_attrs = {"href", "src", "action", "formaction", "xlink:href"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.blocked:
            self.depth += 1
            return
        if self.depth:
            return
        clean = []
        for name, value in attrs:
            name = name.lower()
            value = value or ""
            if name.startswith("on") or name in self.url_attrs or name == "srcdoc":
                continue
            if name == "style" and re.search(r"(?i)url\s*\(|expression\s*\(|@import", value):
                continue
            clean.append(f' {name}="{escape(value, quote=True)}"')
        self.parts.append(f"<{tag}{''.join(clean)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if not self.depth and tag.lower() not in self.blocked:
            self.parts[-1] = self.parts[-1][:-1] + "/>"

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.blocked and self.depth:
            self.depth -= 1
            return
        if not self.depth:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.depth:
            safe = re.sub(r"(?i)@import|url\s*\([^)]*\)|expression\s*\([^)]*\)", "", data)
            self.parts.append(safe)

    def handle_entityref(self, name: str) -> None:
        if not self.depth:
            self.parts.append(f"&{name};")


def sanitize_html(html: str) -> str:
    if not settings.html_sanitization_enabled:
        raise ValueError("HTML sanitization cannot be bypassed for stored reports")
    parser = _Sanitizer()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


REPORT_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "script-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)
