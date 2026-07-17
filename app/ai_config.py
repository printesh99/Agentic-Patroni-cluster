"""Configuration helpers for the AI/ML DBA layer."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from . import sources as S

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_ROOT = REPO_ROOT / "server"
INFRA_AI = REPO_ROOT / "infra" / "ai"


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def safe_env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return False


def safe_agentic_mode() -> str:
    value = os.environ.get("AGENTIC_MODE", "SHADOW").strip().upper()
    return value if value in {"SHADOW", "ADVISORY", "CONTROLLED"} else "SHADOW"


def _derive_meta_dsn() -> str:
    """Resolve the metadata DSN.

    Precedence:
      1. ``PGC_META_DSN`` if set (explicit wins).
      2. A PostgreSQL DSN derived from the standard ``PG*`` env vars when a host
         is available (so the local compose DB / a deployed PG is used).
      3. SQLite fallback when no PG env is present.
    """
    explicit = os.environ.get("PGC_META_DSN")
    if explicit:
        return explicit

    host = os.environ.get("PGHOST")
    if host:
        port = os.environ.get("PGPORT", "5432")
        database = os.environ.get("PGDATABASE", "postgres")
        user = os.environ.get("PGUSER", "postgres")
        password = os.environ.get("PGPASSWORD", "")
        sslmode = os.environ.get("PGSSLMODE")
        auth = f"{user}:{password}@" if password else f"{user}@"
        dsn = f"postgresql+psycopg://{auth}{host}:{port}/{database}"
        if sslmode:
            dsn += f"?sslmode={sslmode}"
        return dsn

    return f"sqlite:///{(SERVER_ROOT / 'metadata.db').as_posix()}"


PGC_META_DSN = _derive_meta_dsn()
AI_ML_ENABLED = env_bool("AI_ML_ENABLED", True)
AI_ML_MODEL_DIR = Path(os.environ.get("AI_ML_MODEL_DIR", str(SERVER_ROOT / "models")))
AI_ML_DEFAULT_CONTAMINATION = float(os.environ.get("AI_ML_DEFAULT_CONTAMINATION", "0.02"))
AI_ML_MIN_TRAINING_ROWS = int(os.environ.get("AI_ML_MIN_TRAINING_ROWS", "500"))
AI_ML_SCORING_INTERVAL_SECONDS = int(os.environ.get("AI_ML_SCORING_INTERVAL_SECONDS", "300"))
AI_FORECAST_ENABLED = env_bool("AI_FORECAST_ENABLED", True)
AI_RISK_SCORE_ENABLED = env_bool("AI_RISK_SCORE_ENABLED", True)
AI_LLM_RCA_ENABLED = env_bool("AI_LLM_RCA_ENABLED", True)
AI_ACTION_APPROVAL_REQUIRED = env_bool("AI_ACTION_APPROVAL_REQUIRED", True)
AGENTIC_WORKFLOW_ENABLED = safe_env_bool("AGENTIC_WORKFLOW_ENABLED")
MCP_DIAGNOSTICS_ENABLED = safe_env_bool("MCP_DIAGNOSTICS_ENABLED")
MCP_OPERATIONS_ENABLED = safe_env_bool("MCP_OPERATIONS_ENABLED")
AI_ACTION_EXECUTION_ENABLED = safe_env_bool("AI_ACTION_EXECUTION_ENABLED")
EMERGENCY_FAILOVER_ENABLED = safe_env_bool("EMERGENCY_FAILOVER_ENABLED")
AGENTIC_MODE = safe_agentic_mode()


def action_execution_allowed() -> bool:
    return bool(AGENTIC_WORKFLOW_ENABLED and AI_ACTION_EXECUTION_ENABLED and AGENTIC_MODE == "CONTROLLED")


def execution_disabled_response() -> dict[str, Any]:
    return {"available": False, "executed": False, "status": "execution_disabled",
            "mode": AGENTIC_MODE, "reason": "Agentic action execution is disabled by policy."}

METRICS_MAP_PATH = Path(os.environ.get("AI_METRICS_MAP", str(INFRA_AI / "metrics-map.yaml")))
THRESHOLDS_PATH = Path(os.environ.get("AI_THRESHOLDS", str(INFRA_AI / "thresholds.yaml")))


class ConfigError(RuntimeError):
    """Raised when AI/ML config cannot be loaded or validated."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file must contain a mapping: {path}")
    return data


def load_metrics_map() -> dict[str, Any]:
    data = _read_yaml(METRICS_MAP_PATH)
    metrics = data.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ConfigError("metrics-map.yaml must define non-empty metrics")
    for name, spec in metrics.items():
        if not isinstance(spec, dict) or not spec.get("prometheus_query"):
            raise ConfigError(f"metric {name!r} must define prometheus_query")
    return data


def load_thresholds() -> dict[str, Any]:
    data = _read_yaml(THRESHOLDS_PATH)
    defaults = data.get("defaults")
    if not isinstance(defaults, dict) or not defaults:
        raise ConfigError("thresholds.yaml must define non-empty defaults")
    return data


def runtime_summary() -> dict[str, Any]:
    return {
        "enabled": AI_ML_ENABLED,
        "meta_dsn": _redact_dsn(PGC_META_DSN),
        "model_dir": str(AI_ML_MODEL_DIR),
        "min_training_rows": AI_ML_MIN_TRAINING_ROWS,
        "scoring_interval_seconds": AI_ML_SCORING_INTERVAL_SECONDS,
        "forecast_enabled": AI_FORECAST_ENABLED,
        "risk_score_enabled": AI_RISK_SCORE_ENABLED,
        "llm_rca_enabled": AI_LLM_RCA_ENABLED,
        "action_approval_required": AI_ACTION_APPROVAL_REQUIRED,
        "agentic_workflow_enabled": AGENTIC_WORKFLOW_ENABLED,
        "mcp_diagnostics_enabled": MCP_DIAGNOSTICS_ENABLED,
        "mcp_operations_enabled": MCP_OPERATIONS_ENABLED,
        "ai_action_execution_enabled": AI_ACTION_EXECUTION_ENABLED,
        "emergency_failover_enabled": EMERGENCY_FAILOVER_ENABLED,
        "agentic_mode": AGENTIC_MODE,
        "action_execution_allowed": action_execution_allowed(),
    }


def _redact_dsn(dsn: str) -> str:
    if "@" not in dsn or "://" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    if "@" not in rest:
        return dsn
    return f"{scheme}://***@{rest.rsplit('@', 1)[1]}"


def validate() -> dict[str, Any]:
    AI_ML_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    metrics = load_metrics_map()
    thresholds = load_thresholds()
    return {
        "runtime": runtime_summary(),
        "metrics_count": len(metrics["metrics"]),
        "threshold_count": len(thresholds["defaults"]),
        "cluster_id": S.CLUSTER_ID,
    }
