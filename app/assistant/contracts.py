from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SourceOutcome(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    MISSING = "missing"
    STALE = "stale"
    CONFLICTING = "conflicting"
    PARTIAL = "partial"


class SourceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    intent: str
    transport: str
    evidence_contract: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()
    freshness_ttl_seconds: int = Field(ge=0)
    timeout_ms: int = Field(default=1000, ge=1, le=3000)
    row_limit: int = Field(default=1000, ge=1)
    payload_limit_bytes: int = Field(default=262144, ge=1024)
    redaction_policy: str = "operational"
    allowed_fallback: str | None = None
    answer_obligations: tuple[str, ...] = ()
    idempotent: bool = True
