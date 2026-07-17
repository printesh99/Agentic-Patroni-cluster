from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import sources as S
from app.db.models import Base, ClusterInventory, AiActionApproval, AiActionPlan
from app.security import Principal
from app.services import approval_service, action_control_service, readiness_service

def configured(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(approval_service, "SessionLocal", Session)
    cfg = S.ClusterConfig("test", "cluster-test", "ns-test", "http://prom")
    monkeypatch.setattr(S, "CLUSTER_REGISTRY", {"test": cfg})
    token = S._active_cluster_id.set("test")
    with Session() as db:
        db.add(ClusterInventory(region="uae", dc="dc1", env="test", namespace="ns-test",
                                cluster_name="cluster-test", active=True)); db.commit()
    return Session, token

def test_plan_is_hashed_and_approvals_are_normalized(monkeypatch):
    Session, token = configured(monkeypatch)
    try:
        requester = Principal("requester", "Requester", frozenset({"dba"}), "test")
        approver = Principal("approver", "Approver", frozenset({"senior-dba"}), "test")
        action = approval_service.request_action({"action_level": "L3", "action_type": "analyze",
                                                  "command_preview": "ANALYZE safe_table"}, requester)
        assert len(action["plan_sha256"]) == 64
        approved = approval_service.approve_action(action["id"], {}, approver)
        assert approved["approvals_received"] == 1 and approved["execution_status"] == "approved"
        with Session() as db:
            assert db.query(AiActionPlan).count() == 1
            assert db.query(AiActionApproval).count() == 1
    finally: S._active_cluster_id.reset(token)

def test_readiness_is_fail_closed_then_allows_only_l3_allowlist(monkeypatch):
    Session, token = configured(monkeypatch)
    try:
        monkeypatch.setattr(action_control_service.ai_config, "action_execution_allowed", lambda: True)
        monkeypatch.setenv("PGC_ALLOW_MUTATIONS", "1")
        admin = Principal("admin", "Admin", frozenset({"senior-dba"}), "test")
        with Session() as db:
            inv = db.query(ClusterInventory).one()
            ok, reasons = action_control_service.readiness(db, inv.id, "L3", "analyze")
            assert not ok and reasons
            readiness_service.record(db, "shadow_validation", "PASS", {"hours": 720}, admin)
            readiness_service.record(db, "backup_recovery", "PASS", {"restore_test": "passed"}, admin)
            db.commit()
            ok, reasons = action_control_service.readiness(db, inv.id, "L3", "analyze")
            assert ok and not reasons
            assert not action_control_service.readiness(db, inv.id, "L4", "switchover")[0]
    finally: S._active_cluster_id.reset(token)
