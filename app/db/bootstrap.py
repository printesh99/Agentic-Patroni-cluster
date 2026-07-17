"""Bootstrap and seed AI/ML metadata storage."""
from __future__ import annotations

from typing import Any
from sqlalchemy import func, select

from .. import ai_config
from .. import sources as S

_STATUS: dict[str, Any] = {
    "available": False,
    "bootstrapped": False,
    "error": None,
    "inventory_seeded": False,
}


def bootstrap() -> dict[str, Any]:
    """Create metadata tables and seed the live cluster inventory.

    This is intentionally tolerant: missing optional DB dependencies should not
    prevent the read-only console from starting.
    """
    global _STATUS
    try:
        ai_config.validate()
        from .models import Base, ClusterInventory
        from .session import SessionLocal, engine
        from ..services import inventory_service

        Base.metadata.create_all(bind=engine)
        with SessionLocal() as db:
            before = db.execute(select(func.count()).select_from(ClusterInventory)).scalar_one()
            inventory_service.resolve(db, create=True)
            db.commit()
            seeded = before == 0
        _STATUS = {
            "available": True,
            "bootstrapped": True,
            "error": None,
            "inventory_seeded": seeded,
            "config": ai_config.runtime_summary(),
        }
    except Exception as exc:  # pragma: no cover - startup resilience
        _STATUS = {
            "available": False,
            "bootstrapped": False,
            "error": str(exc),
            "inventory_seeded": False,
            "config": ai_config.runtime_summary(),
        }
    return _STATUS


def status() -> dict[str, Any]:
    return dict(_STATUS)
