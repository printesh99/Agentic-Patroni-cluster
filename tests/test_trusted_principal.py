import os

from starlette.requests import Request

from app.security import principal_from_request
from app.security import Principal
from app.db.models import Base
from app.services import approval_service
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest


def request(headers):
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw,
                    "client": ("127.0.0.1", 12345)})


def test_payload_roles_are_not_identity_inputs(monkeypatch):
    monkeypatch.setenv("TRUSTED_IDENTITY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_PROXY_SHARED_SECRET", "proxy-secret")
    principal = principal_from_request(request({"x-forwarded-user": "alice", "x-forwarded-groups": "dba",
                                                "x-trusted-proxy-secret": "proxy-secret"}))
    payload = {"actor": "mallory", "roles": ["platform-admin"]}
    assert principal.subject_id == "alice"
    assert principal.roles == frozenset({"dba"})
    assert payload["actor"] != principal.subject_id
    assert "platform-admin" not in principal.roles


def test_untrusted_headers_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRUSTED_IDENTITY_HEADERS", raising=False)
    assert principal_from_request(request({"x-forwarded-user": "alice", "x-forwarded-groups": "platform-admin"})) is None


def test_proxy_headers_require_secret_and_trusted_source(monkeypatch):
    monkeypatch.setenv("TRUSTED_IDENTITY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_PROXY_SHARED_SECRET", "expected")
    assert principal_from_request(request({"x-forwarded-user": "alice"})) is None
    assert principal_from_request(request({"x-forwarded-user": "alice", "x-trusted-proxy-secret": "wrong"})) is None


def test_service_token_rotation_accepts_current_and_next(monkeypatch):
    monkeypatch.setenv("AGENTIC_SERVICE_TOKEN", "current")
    monkeypatch.setenv("AGENTIC_SERVICE_TOKEN_NEXT", "next")
    assert principal_from_request(request({"x-agentic-service-token": "current"})).service_account
    assert principal_from_request(request({"x-agentic-service-token": "next"})).service_account


def test_requester_cannot_self_approve_and_payload_cannot_replace_actor(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(approval_service, "SessionLocal", sessionmaker(bind=engine))
    alice = Principal("alice-id", "Alice", frozenset({"senior-dba"}), "test")
    action = approval_service.request_action({"action_level": "L4", "command_preview": "preview",
                                              "actor": "mallory", "roles": ["platform-admin"]}, alice)
    assert action["requested_by"] == "alice-id"
    with pytest.raises(Exception, match="cannot approve"):
        approval_service.approve_action(action["id"], {"actor": "mallory"}, alice)
