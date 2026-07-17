"""Typed pg_profile API and strict RCA schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ServerCreate(BaseModel):
    server_name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    inventory_id: int | None = None
    region: str | None = Field(default=None, max_length=64)
    dc: str | None = Field(default=None, max_length=64)
    environment: str = Field(max_length=32)
    namespace: str = Field(min_length=1, max_length=255)
    cluster_name: str = Field(min_length=1, max_length=255)
    database_name: str = Field(default="postgres", min_length=1, max_length=255)
    credential_reference: str = Field(min_length=1, max_length=512)
    endpoint_host: str = Field(min_length=1, max_length=512)
    endpoint_port: int = Field(default=5555, ge=1, le=65535)
    sslmode: Literal["verify-full", "verify-ca"] = "verify-full"
    enabled: bool = True

    @field_validator("credential_reference")
    @classmethod
    def credential_reference_is_indirect(cls, value: str) -> str:
        if not (value.startswith("env:") or value.startswith("file:")):
            raise ValueError("credential_reference must use env: or file:")
        return value

    @field_validator("endpoint_host")
    @classmethod
    def endpoint_is_host_only(cls, value: str) -> str:
        value = value.strip()
        if not value or not all(ch.isalnum() or ch in ".:-_" for ch in value):
            raise ValueError("endpoint_host must be a DNS name or IP address without a URL or path")
        return value


class SampleRequest(BaseModel):
    trigger_type: Literal["MANUAL", "SCHEDULED", "INCIDENT_START", "INCIDENT_RECOVERY"] = "MANUAL"
    incident_id: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=255)


class ReportCreate(BaseModel):
    pgprofile_server_id: int
    start_sample_id: int | None = Field(default=None, ge=1)
    end_sample_id: int | None = Field(default=None, ge=1)
    period_start: datetime | None = None
    period_end: datetime | None = None
    report_type: Literal["REGULAR", "DIFF"] = "REGULAR"
    compare_start_sample_id: int | None = Field(default=None, ge=1)
    compare_end_sample_id: int | None = Field(default=None, ge=1)
    incident_id: int | None = None

    @field_validator("period_start", "period_end")
    @classmethod
    def report_times_are_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("report times must include a timezone")
        return value

    @model_validator(mode="after")
    def complete_ranges(self):
        sample_range = self.start_sample_id is not None or self.end_sample_id is not None
        time_range = self.period_start is not None or self.period_end is not None
        if sample_range == time_range:
            raise ValueError("provide exactly one complete sample range or time range")
        if sample_range and (self.start_sample_id is None or self.end_sample_id is None):
            raise ValueError("both sample bounds are required")
        if time_range and (self.period_start is None or self.period_end is None):
            raise ValueError("both time bounds are required")
        if self.start_sample_id is not None and self.end_sample_id is not None and self.start_sample_id >= self.end_sample_id:
            raise ValueError("start_sample_id must be less than end_sample_id")
        if self.report_type == "DIFF":
            if self.compare_start_sample_id is None or self.compare_end_sample_id is None:
                raise ValueError("DIFF reports require comparison sample bounds")
            if self.compare_start_sample_id >= self.compare_end_sample_id:
                raise ValueError("comparison start must be less than comparison end")
        return self


class RetentionRequest(BaseModel):
    dry_run: bool = True
    server_id: int | None = None


class BaselineFeedback(BaseModel):
    state: Literal[
        "CORRECT", "PARTIALLY_CORRECT", "INCORRECT", "FALSE_POSITIVE",
        "KNOWN_CHANGE", "NEEDS_MORE_EVIDENCE",
    ]
    note: str | None = Field(default=None, max_length=1000)


class EvidenceFact(BaseModel):
    statement: str
    evidence_ids: list[str] = Field(min_length=1)


class RootCause(BaseModel):
    cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)


class StructuredRCA(BaseModel):
    incident_type: str
    severity: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    confirmed_facts: list[EvidenceFact]
    likely_root_causes: list[RootCause]
    alternative_causes: list[RootCause] = Field(default_factory=list)
    business_impact: str
    immediate_safe_checks: list[str]
    remediation_plan: list[str]
    rollback_plan: list[str]
    approval_required: bool = True
    missing_evidence: list[str]
    runbook_references: list[str]
    pgprofile_report_ids: list[int]


class Page(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
