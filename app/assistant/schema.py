from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OverallStatus(str, Enum):
    ANSWERED = "answered"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SOURCE_UNAVAILABLE = "source_unavailable"
    UNSAFE_REQUEST = "unsafe_request"
    GENERATION_FAILED = "generation_failed"


class SectionStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"
    STALE = "stale"
    SOURCE_UNAVAILABLE = "source_unavailable"
    INCONSISTENT_SNAPSHOT = "inconsistent_snapshot"


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    intents: list[str]
    conditions: list[str] = Field(default_factory=list)
    required_sources: list[str] = Field(default_factory=list)
    answer_obligations: list[str] = Field(default_factory=list)
    deadline_ms: int = Field(default=3000, ge=1, le=3000)
    source_timeout_ms: int = Field(default=1000, ge=1, le=3000)
    injection_detected: bool = False
    unsafe_only: bool = False


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    contract: str
    source: str
    collected_at: datetime
    collection_started_at: datetime
    freshness_seconds: int = 0
    payload: dict[str, Any]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str
    status: SectionStatus
    source: str
    evidence_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    source_errors: list[str] = Field(default_factory=list)
    text: str = ""


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    evidence_ids: list[str]
    type: str = "fact"


class PhysicalReplicationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_member: str | None
    patroni_ok: bool
    standbys: list[dict[str, Any]]
    logical_walsenders: int
    collected_at: datetime


class WalArchiverEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_wal_segment: str
    current_wal_lsn: str
    last_archived_wal: str | None
    last_archived_time: datetime | None
    archived_count: int
    failed_count: int
    last_failed_wal: str | None
    last_failed_time: datetime | None
    collected_at: datetime
