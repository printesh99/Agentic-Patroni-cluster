"""Whitelist-based executor for AI DBA recommendations.

The executor never runs arbitrary LLM text. It accepts only a tiny set of DBA
statements and still requires approval plus explicit execution flags.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .. import jobs, ai_config
from .. import sources as S


_TRUE_VALUES = {"1", "true", "yes", "on"}
_BLOCKED_PATTERNS = [
    r"\bdrop\s+table\b",
    r"\btruncate\b",
    r"\bdelete\s+from\b",
    r"\bupdate\s+",
    r"\balter\s+system\b",
    r"\bvacuum\s+full\b",
    r"\bfailover\b",
    r"\bswitchover\b",
    r"\bpgbackrest\s+restore\b",
    r"\brestart\s+pod\b",
    r"\bscale\s+pod\b",
    r"\bcopy\s+.*\bprogram\b",
    r"\bdo\s+\$\$",
    r"\bcreate\s+extension\b",
    r"\balter\s+role\b",
    r"\balter\s+user\b",
    r"\bgrant\b",
    r"\brevoke\b",
    r"\bcreate\s+user\b",
    r"\bdrop\s+user\b",
]


@dataclass
class SqlSafety:
    allowed: bool
    action_type: str
    mutating: bool
    reason: str


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def execution_enabled() -> bool:
    return _env_bool("AI_AGENT_EXECUTION_ENABLED", False)


def _strip_sql_comments(sql: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    text = re.sub(r"--.*?$", " ", text, flags=re.M)
    return " ".join(text.strip().split())


def _single_statement(sql: str) -> bool:
    stripped = sql.strip()
    if not stripped:
        return False
    if stripped.endswith(";"):
        stripped = stripped[:-1]
    return ";" not in stripped


def classify_sql_safety(sql: str | None) -> SqlSafety:
    if not sql or not str(sql).strip():
        return SqlSafety(False, "none", False, "no SQL was provided")
    normalized = _strip_sql_comments(str(sql))
    lowered = normalized.lower()
    if not _single_statement(normalized):
        return SqlSafety(False, "multi_statement", False, "multiple SQL statements are not allowed")
    if any(re.search(pattern, lowered) for pattern in _BLOCKED_PATTERNS):
        return SqlSafety(False, "blocked", True, "statement matches a blocked DBA action")
    if re.match(r"^explain(\s|\()", lowered):
        return SqlSafety(True, "EXPLAIN", False, "read-only diagnostic")
    if re.match(r"^select\s+pg_cancel_backend\s*\(\s*\d+\s*\)\s*;?$", lowered):
        return SqlSafety(True, "CANCEL_BACKEND", True, "cancel backend requires approval")
    if re.match(r"^select\b", lowered):
        return SqlSafety(True, "SELECT", False, "read-only diagnostic")
    if re.match(r"^analyze\s+(verbose\s+)?[a-zA-Z0-9_\".]+\s*;?$", normalized, flags=re.I):
        return SqlSafety(True, "ANALYZE", True, "ANALYZE table is approved-safe")
    if re.match(r"^create\s+index\s+concurrently\b", lowered):
        return SqlSafety(True, "CREATE_INDEX_CONCURRENTLY", True, "concurrent index creation requires approval")
    if re.match(r"^drop\s+index\s+concurrently\b", lowered):
        return SqlSafety(True, "DROP_INDEX_CONCURRENTLY", True, "concurrent rollback index drop requires approval")
    return SqlSafety(False, "unknown", False, "statement is not in the AI agent executor whitelist")


def execute_sql(sql: str | None, confirm: bool = False) -> dict[str, Any]:
    if not ai_config.action_execution_allowed():
        response = ai_config.execution_disabled_response()
        return {**response, "status": "BLOCKED", "action_type": "disabled"}
    safety = classify_sql_safety(sql)
    if not safety.allowed:
        return {
            "executed": False,
            "status": "BLOCKED",
            "action_type": safety.action_type,
            "reason": safety.reason,
        }
    if not confirm:
        return {
            "executed": False,
            "status": "PREVIEW_ONLY",
            "action_type": safety.action_type,
            "reason": "execution confirmation missing",
        }
    if safety.mutating and not jobs.mutations_enabled():
        return {
            "executed": False,
            "status": "BLOCKED",
            "action_type": safety.action_type,
            "reason": "PGC_ALLOW_MUTATIONS is not enabled",
        }
    if safety.action_type in {"CREATE_INDEX_CONCURRENTLY", "DROP_INDEX_CONCURRENTLY"} and not _env_bool("AI_AGENT_INDEX_DDL_ENABLED", False):
        return {
            "executed": False,
            "status": "BLOCKED",
            "action_type": safety.action_type,
            "reason": "AI_AGENT_INDEX_DDL_ENABLED is not enabled",
        }
    if not execution_enabled():
        return {
            "executed": False,
            "status": "PREVIEW_ONLY",
            "action_type": safety.action_type,
            "reason": "AI_AGENT_EXECUTION_ENABLED is false",
        }
    try:
        rows = S.sql(str(sql), timeout=60)
        return {
            "executed": True,
            "status": "EXECUTED",
            "action_type": safety.action_type,
            "rows": rows[:50],
            "row_count": len(rows),
        }
    except Exception as exc:
        return {
            "executed": False,
            "status": "FAILED",
            "action_type": safety.action_type,
            "reason": str(exc),
        }
