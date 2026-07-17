import importlib

from app import ai_config
from app import jobs, sources as S
from app.services import ai_agent_executor


def test_agentic_defaults_are_non_mutating(monkeypatch):
    for name in ("AGENTIC_WORKFLOW_ENABLED", "MCP_DIAGNOSTICS_ENABLED",
                 "MCP_OPERATIONS_ENABLED", "AI_ACTION_EXECUTION_ENABLED",
                 "EMERGENCY_FAILOVER_ENABLED", "AGENTIC_MODE"):
        monkeypatch.delenv(name, raising=False)
    cfg = importlib.reload(ai_config)
    assert cfg.AGENTIC_MODE == "SHADOW"
    assert not cfg.AGENTIC_WORKFLOW_ENABLED
    assert not cfg.MCP_OPERATIONS_ENABLED
    assert not cfg.AI_ACTION_EXECUTION_ENABLED
    assert not cfg.EMERGENCY_FAILOVER_ENABLED
    assert not cfg.action_execution_allowed()


def test_shadow_and_advisory_never_execute(monkeypatch):
    monkeypatch.setenv("AGENTIC_WORKFLOW_ENABLED", "true")
    monkeypatch.setenv("AI_ACTION_EXECUTION_ENABLED", "true")
    for mode in ("SHADOW", "ADVISORY", "invalid"):
        monkeypatch.setenv("AGENTIC_MODE", mode)
        cfg = importlib.reload(ai_config)
        assert not cfg.action_execution_allowed()
        assert cfg.execution_disabled_response()["executed"] is False


def test_sql_and_generic_job_cannot_bypass_central_guard(monkeypatch):
    called = []
    monkeypatch.setattr(S, "sql", lambda *a, **k: called.append(True))
    result = ai_agent_executor.execute_sql("select pg_cancel_backend(123)", confirm=True)
    assert result["executed"] is False and not called
    job = jobs.submit("generic", {"roles": ["platform-admin"]}, actor="payload-user",
                      actor_roles=["platform-admin"], dry_run=False,
                      executor=lambda: called.append(True))
    assert job["status"] == "blocked" and not called
