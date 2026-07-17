from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, ClusterHealthSnapshot, ClusterInventory, AiIncident


def test_latest_snapshot_query_can_be_inventory_scoped():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        a = ClusterInventory(region="x", dc="x", env="x", namespace="a", cluster_name="a")
        b = ClusterInventory(region="x", dc="x", env="x", namespace="b", cluster_name="b")
        db.add_all([a, b]); db.flush()
        db.add_all([ClusterHealthSnapshot(inventory_id=a.id), ClusterHealthSnapshot(inventory_id=b.id)])
        db.commit()
        rows = db.execute(select(ClusterHealthSnapshot).where(ClusterHealthSnapshot.inventory_id == a.id)).scalars().all()
        assert len(rows) == 1 and rows[0].inventory_id == a.id


def test_incidents_have_inventory_ownership():
    assert "inventory_id" in AiIncident.__table__.columns
