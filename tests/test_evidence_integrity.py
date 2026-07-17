from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import sources as S
from app.db.models import Base, ClusterInventory
from app.services import evidence_service, cluster_identity_service


def session(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    cfg = S.ClusterConfig("test", "cluster-test", "ns-test", "http://prom")
    monkeypatch.setattr(S, "CLUSTER_REGISTRY", {"test": cfg})
    token = S._active_cluster_id.set("test")
    inv = ClusterInventory(region="uae", dc="dc1", env="test", namespace="ns-test",
                           cluster_name="cluster-test", active=True)
    db.add(inv); db.commit()
    return db, token


def test_canonical_hash_and_redaction_are_deterministic():
    left = {"b": 2, "a": 1, "password": "secret"}
    right = {"password": "different", "a": 1, "b": 2}
    assert evidence_service.canonical_sha256(left) == evidence_service.canonical_sha256(right)
    assert "secret" not in evidence_service.canonical_json(left)


def test_append_only_items_are_unique_and_stale_is_deterministic(monkeypatch):
    db, token = session(monkeypatch)
    try:
        bundle = evidence_service.create_bundle(db)
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        one = evidence_service.append_item(db, bundle, source_type="identity", source_name="test",
            collector_name="test", collector_version="v1", payload={"token": "nope", "ok": True},
            source_timestamp=old, max_age_seconds=60)
        two = evidence_service.append_item(db, bundle, source_type="identity", source_name="test",
            collector_name="test", collector_version="v1", payload={"ok": True})
        assert one.evidence_id != two.evidence_id
        assert one.freshness_status == "STALE"
        assert one.payload["token"] == "<REDACTED>"
        assert len(one.payload_sha256) == 64
    finally:
        S._active_cluster_id.reset(token); db.close()


def test_wrong_inventory_bundle_is_rejected(monkeypatch):
    db, token = session(monkeypatch)
    try:
        bundle = evidence_service.create_bundle(db)
        bundle.inventory_id += 99
        import pytest
        with pytest.raises(Exception):
            evidence_service.append_item(db, bundle, source_type="x", source_name="x",
                collector_name="x", collector_version="v1", payload={})
    finally:
        S._active_cluster_id.reset(token); db.close()


def test_verified_identity_bundle_persists_end_to_end(monkeypatch):
    db, token = session(monkeypatch)
    try:
        monkeypatch.setattr(S, "kubectl_json", lambda args: {"metadata": {"uid": "uid-1", "resourceVersion": "9", "generation": 3},
                                                              "status": {"observedGeneration": 3}})
        monkeypatch.setattr(S, "patroni_cluster", lambda: {"scope": "cluster-test-ha", "members": [{"name": "primary-0", "role": "leader"}]})
        monkeypatch.setattr(S, "sql_one", lambda query: ["system-1", "7", "18.1"])
        bundle, item = cluster_identity_service.persist(db)
        db.commit()
        assert item.payload["inventory_id"] == bundle.inventory_id
        assert item.payload["system_identifier"] == "system-1"
        assert item.payload["current_primary"] == "primary-0"
        assert bundle.quality_status == "COMPLETE"
        assert bundle.action_ready is False
    finally:
        S._active_cluster_id.reset(token); db.close()


def test_identity_contradiction_blocks_action_readiness(monkeypatch):
    db, token = session(monkeypatch)
    try:
        monkeypatch.setattr(S, "kubectl_json", lambda args: {"metadata": {}, "status": {}})
        monkeypatch.setattr(S, "patroni_cluster", lambda: {"scope": "another-cluster", "members": []})
        monkeypatch.setattr(S, "sql_one", lambda query: ["system-1", "7", "18.1"])
        bundle, _ = cluster_identity_service.persist(db)
        assert bundle.quality_status == "CONTRADICTORY"
        assert bundle.action_ready is False
    finally:
        S._active_cluster_id.reset(token); db.close()
