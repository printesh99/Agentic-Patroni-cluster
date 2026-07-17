"""Single fail-closed resolver for cluster-owned metadata rows."""
from __future__ import annotations

from sqlalchemy import select

from .. import sources as S
from ..db.models import ClusterInventory


class InventoryResolutionError(RuntimeError):
    pass


def resolve(db, *, create: bool = False, cluster_name: str | None = None) -> ClusterInventory:
    cfg = S.resolve_cluster_or_raise(S.CLUSTER_ID)
    if cluster_name is not None and cluster_name != cfg.cluster_name:
        raise InventoryResolutionError("requested cluster does not match the verified active cluster")
    rows = db.execute(
        select(ClusterInventory).where(ClusterInventory.cluster_name == cfg.cluster_name)
    ).scalars().all()
    if len(rows) > 1:
        raise InventoryResolutionError("multiple inventory rows match the verified cluster")
    if rows:
        inv = rows[0]
        if not inv.active:
            raise InventoryResolutionError("verified cluster inventory is disabled")
        if inv.namespace != cfg.namespace:
            raise InventoryResolutionError("inventory namespace does not match verified cluster configuration")
        return inv
    if not create:
        raise InventoryResolutionError("verified cluster inventory does not exist")
    inv = ClusterInventory(
        region="uae", dc="dc1", env="unknown", namespace=cfg.namespace,
        cluster_name=cfg.cluster_name, prometheus_url=cfg.prom_url or None, active=True,
    )
    db.add(inv)
    db.flush()
    return inv
