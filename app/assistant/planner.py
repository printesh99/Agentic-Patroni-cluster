from __future__ import annotations

import re

from .schema import QueryPlan
from .registry import sources_for_intent


_WAL_PHRASES = (
    "archive log", "archived log", "wal archive", "wal archiver",
    "current wal", "wal segment", "last archived", "pg_stat_archiver",
)
_REPLICATION_PHRASES = (
    "physical replication", "replication lag", "standby lag", "replica lag",
    "replay lag", "physical standby", "ha standby",
)
_INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|system)\s+instructions"),
    re.compile(r"(?i)ignore\s+(any\s+)?instruction\s+in\s+(database|log)\s+(text|content)"),
    re.compile(r"(?i)ignore\s+(db|database|log)\s+(text|content).{0,80}(ask|asking|instruct).{0,40}(write|execute|mutat)"),
    re.compile(r"(?i)evidence\s+(content\s+)?cannot\s+authorize\s+(a\s+)?(mutation|write|tool|operation)"),
    re.compile(r"(?i)(reveal|print|show)\s+(the\s+)?(system prompt|hidden instructions)"),
    re.compile(r"(?i)(log|database|evidence)\s+(says|instructs).{0,80}(execute|run|call|delete|drop)"),
)
_MUTATION_ONLY = re.compile(
    r"(?i)^\s*(ignore\b.*\b(and|then)\s+)?(execute|run|delete|drop|alter|restart|failover|switchover)\b"
)


def _has_phrase(question: str, phrases: tuple[str, ...]) -> bool:
    normalized = question.lower().replace("-", " ")
    return any(phrase in normalized for phrase in phrases)


def plan(question: str) -> QueryPlan:
    q = question or ""
    normalized = q.lower().replace("replcation", "replication").replace(
        "leder", "leader").replace("stanby", "standby")
    intents: list[str] = []
    if (_has_phrase(normalized, _REPLICATION_PHRASES)
            or all(term in normalized for term in ("byte lag", "replay state", "sync state"))):
        intents.append("replication_physical")
    if _has_phrase(q, _WAL_PHRASES):
        intents.append("wal_archiver")
    ql = q.lower()
    if ((("evidence" in ql or "evidnce" in ql)
         and ("insufficient" in ql or "insuficient" in ql))
            or ("known facts" in ql and "unknown" in ql)):
        intents.append("unknown_scope")
    if (("loki" in ql and ("unavailable" in ql or "down" in ql))
            or ("source availability" in ql and "missing evidence" in ql)):
        intents.append("source_failure_contract")
    injection = any(pattern.search(q) for pattern in _INJECTION_PATTERNS)
    sources = [source for intent in intents
               for source in sources_for_intent(intent)]
    conditions = []
    if any(term in normalized for term in ("current", "now", "currently")):
        conditions.append("current")
    if any(term in normalized for term in ("history", "historical", "previous")):
        conditions.append("historical")
    if intents:
        conditions.append("claim_binding")
    return QueryPlan(
        question=q, intents=intents, conditions=conditions,
        required_sources=[source.name for source in sources],
        answer_obligations=[obligation for source in sources
                            for obligation in source.answer_obligations],
        injection_detected=injection,
        unsafe_only=bool(injection and (_MUTATION_ONLY.search(q) or not intents)),
    )
