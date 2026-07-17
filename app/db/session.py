"""SQLAlchemy session setup for AI/ML metadata."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .. import ai_config

connect_args = {"check_same_thread": False} if ai_config.PGC_META_DSN.startswith("sqlite") else {}
engine = create_engine(ai_config.PGC_META_DSN, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
