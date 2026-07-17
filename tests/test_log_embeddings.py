import pytest

from app.ai import log_embeddings
from app.services import ai_provider


def test_worker_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LOG_INDEX_ENABLED", raising=False)
    result = log_embeddings.index_cluster_logs("uat")
    assert result == {"available": True, "enabled": False, "status": "disabled", "chunks_indexed": 0}


def test_vector_dimension_is_enforced():
    with pytest.raises(ValueError):
        log_embeddings._vector([0.0] * 384)
    assert log_embeddings._vector([0.0] * 1536).startswith("[")


def test_embed_requires_explicit_deployment(monkeypatch):
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_EMBED_DEPLOYMENT", "AZURE_OPENAI_API_KEY", "AI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        ai_provider.embed(["safe fixture"])


def test_positive_int_is_bounded_and_safe(monkeypatch):
    monkeypatch.setenv("LOG_RETENTION_DAYS", "0")
    assert log_embeddings._positive_int("LOG_RETENTION_DAYS", 90) == 1
    monkeypatch.setenv("LOG_RETENTION_DAYS", "invalid")
    assert log_embeddings._positive_int("LOG_RETENTION_DAYS", 90) == 90


def test_chunks_deduplicate_content_hash():
    streams = [{"stream": {"k8s_container_name": "database"}, "values": [
        ["1783767648000000000", "LOG: ready"],
        ["1783767648000000000", "LOG: ready"],
    ]}]
    chunks = log_embeddings._chunks(streams)
    assert len(chunks) == 1
    assert len(chunks[0]["content_hash"]) == 64
