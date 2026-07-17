from app import log_ai


def test_failover_context_uses_real_selector(monkeypatch):
    captured = {}
    monkeypatch.setattr(log_ai.loki, "query_range", lambda query, *a, **k: captured.setdefault("query", query) and [])
    monkeypatch.setattr(log_ai.A, "categories", lambda *a, **k: {"categories": []})
    result = log_ai.gather_context("when was patroni restarted", 1, 2)
    assert result["intent"] == "failover"
    assert "database" in captured["query"]
    assert "level=~" not in captured["query"]
    assert "postmaster" in captured["query"]


def test_fresh_semantic_store_precedes_live_loki(monkeypatch):
    from app.ai import log_embeddings
    entry = {"ts": "2026-07-16T00:00:00+00:00", "ts_ns": "1", "message": "ERROR: fixture",
             "level": "ERROR", "severity": "error", "component": "postgres",
             "pod": "pod-a", "container": "database", "evidence_source": "store"}
    monkeypatch.setattr(log_embeddings, "search", lambda *a, **k: {
        "available": True, "fresh": True, "status": "ok", "lag_seconds": 10,
        "last_indexed_at": entry["ts"], "entries": [entry],
    })
    monkeypatch.setattr(log_ai.loki, "query_range", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("live Loki must not run when store evidence is fresh")))
    result = log_ai.gather_context("why error", 1, 2)
    assert result["evidence_source"] == "store"
    assert result["entries"][0]["message"] == "ERROR: fixture"


def test_structured_patroni_restart_evidence(monkeypatch):
    monkeypatch.setattr(log_ai.S, "patroni_history", lambda: [[17, 100, "reason", "2026-07-11T11:06:17Z", "node"]])
    monkeypatch.setattr(log_ai.S, "patroni_status", lambda: {"role": "master", "timeline": 17, "postmaster_start_time": "2026-07-11 11:00:48+00:00"})
    monkeypatch.setattr(log_ai.S, "pods", lambda ttl=0: [{"name": "pod-a", "restarts": 1,
        "containers": [{"name": "database", "restart_count": 1, "last_termination": {"reason": "Error"}}]}])
    monkeypatch.setattr(log_ai.S, "kubernetes_events", lambda limit=30: [{"reason": "Killing", "name": "pod-a"}])
    evidence = log_ai._restart_evidence()
    assert evidence["history"]
    assert evidence["status"]["timeline"] == 17
    assert evidence["pods"][0]["restarts"] == 1
    assert evidence["events"][0]["reason"] == "Killing"
    text = log_ai._evidence_text({"items": []}, {"intent": "failover", "signatures": [], "categories": [], "entries": [], "patroni_restart": evidence})
    assert "timeline" in text
    assert "2026-07-11" in text
    assert "Kubernetes restart evidence" in text
