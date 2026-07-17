"""Environment-only pg_profile configuration with safe, disabled defaults."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

_TRUE = {"1", "true", "yes", "on"}
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class PgProfileConfigError(ValueError):
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in _TRUE


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except (TypeError, ValueError) as exc:
        raise PgProfileConfigError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise PgProfileConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class PgProfileSettings:
    enabled: bool
    schema: str
    sample_interval_minutes: int
    report_storage: str
    store_html: bool
    compress_html: bool
    max_html_bytes: int
    sample_timeout_seconds: int
    report_timeout_seconds: int
    collection_lock_timeout_seconds: int
    retention_days: int
    max_report_range_hours: int
    incident_sampling_enabled: bool
    auto_report_enabled: bool
    feature_extraction_enabled: bool
    require_ssl: bool
    allowed_sslmodes: tuple[str, ...]
    html_sanitization_enabled: bool
    secret_mount_dir: Path
    trusted_identity_headers: bool
    service_token_env: str
    allowed_environments: tuple[str, ...]
    min_baseline_samples: int
    query_text_enabled: bool
    query_text_max_length: int

    def public_dict(self) -> dict:
        return {
            "enabled": self.enabled, "schema": self.schema,
            "sample_interval_minutes": self.sample_interval_minutes,
            "report_storage": self.report_storage, "store_html": self.store_html,
            "compress_html": self.compress_html, "max_html_bytes": self.max_html_bytes,
            "sample_timeout_seconds": self.sample_timeout_seconds,
            "report_timeout_seconds": self.report_timeout_seconds,
            "retention_days": self.retention_days,
            "max_report_range_hours": self.max_report_range_hours,
            "incident_sampling_enabled": self.incident_sampling_enabled,
            "auto_report_enabled": self.auto_report_enabled,
            "feature_extraction_enabled": self.feature_extraction_enabled,
            "require_ssl": self.require_ssl,
            "allowed_sslmodes": list(self.allowed_sslmodes),
            "html_sanitization_enabled": self.html_sanitization_enabled,
            "allowed_environments": list(self.allowed_environments),
            "min_baseline_samples": self.min_baseline_samples,
            "query_text_enabled": self.query_text_enabled,
        }


def load_settings() -> PgProfileSettings:
    schema = os.getenv("PGPROFILE_SCHEMA", "profile").strip().lower()
    if not _IDENTIFIER.fullmatch(schema):
        raise PgProfileConfigError("PGPROFILE_SCHEMA is not a valid PostgreSQL identifier")
    storage = os.getenv("PGPROFILE_REPORT_STORAGE", "database").strip().lower()
    if storage not in {"database", "metadata-only"}:
        raise PgProfileConfigError("PGPROFILE_REPORT_STORAGE must be database or metadata-only")
    sslmodes = tuple(x.strip().lower() for x in os.getenv(
        "PGPROFILE_ALLOWED_SSLMODES", "verify-full,verify-ca").split(",") if x.strip())
    valid_ssl = {"verify-full", "verify-ca"}
    if not sslmodes or any(x not in valid_ssl for x in sslmodes):
        raise PgProfileConfigError("PGPROFILE_ALLOWED_SSLMODES contains an unsafe or unknown mode")
    environments = tuple(x.strip().lower() for x in os.getenv(
        "PGPROFILE_ALLOWED_ENVIRONMENTS", "uat").split(",") if x.strip())
    return PgProfileSettings(
        enabled=_bool("PGPROFILE_ENABLED", False), schema=schema,
        sample_interval_minutes=_int("PGPROFILE_SAMPLE_INTERVAL_MINUTES", 15, 1, 1440),
        report_storage=storage, store_html=_bool("PGPROFILE_STORE_HTML", True),
        compress_html=_bool("PGPROFILE_COMPRESS_HTML", True),
        max_html_bytes=_int("PGPROFILE_MAX_HTML_BYTES", 10485760, 1024, 104857600),
        sample_timeout_seconds=_int("PGPROFILE_SAMPLE_TIMEOUT_SECONDS", 120, 5, 3600),
        report_timeout_seconds=_int("PGPROFILE_REPORT_TIMEOUT_SECONDS", 180, 5, 3600),
        collection_lock_timeout_seconds=_int("PGPROFILE_COLLECTION_LOCK_TIMEOUT_SECONDS", 5, 1, 300),
        retention_days=_int("PGPROFILE_RETENTION_DAYS", 90, 1, 3650),
        max_report_range_hours=_int("PGPROFILE_MAX_REPORT_RANGE_HOURS", 24, 1, 744),
        incident_sampling_enabled=_bool("PGPROFILE_INCIDENT_SAMPLING_ENABLED", True),
        auto_report_enabled=_bool("PGPROFILE_AUTO_REPORT_ENABLED", True),
        feature_extraction_enabled=_bool("PGPROFILE_FEATURE_EXTRACTION_ENABLED", True),
        require_ssl=_bool("PGPROFILE_REQUIRE_SSL", True), allowed_sslmodes=sslmodes,
        html_sanitization_enabled=_bool("PGPROFILE_HTML_SANITIZATION_ENABLED", True),
        secret_mount_dir=Path(os.getenv("PGPROFILE_SECRET_MOUNT_DIR", "/var/run/secrets/pg-profile")),
        trusted_identity_headers=_bool("PGPROFILE_TRUSTED_IDENTITY_HEADERS", False),
        service_token_env=os.getenv("PGPROFILE_SERVICE_TOKEN_ENV", "PGPROFILE_SERVICE_TOKEN"),
        allowed_environments=environments,
        min_baseline_samples=_int("PGPROFILE_MIN_BASELINE_SAMPLES", 12, 3, 10000),
        query_text_enabled=_bool("PGPROFILE_QUERY_TEXT_ENABLED", False),
        query_text_max_length=_int("PGPROFILE_QUERY_TEXT_MAX_LENGTH", 500, 64, 4000),
    )


settings = load_settings()
