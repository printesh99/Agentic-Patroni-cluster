"""Log signature extraction + categorization (Phase 4 — Log Analytics).

Turns raw log lines into stable *signatures* (templated messages with variable
parts stripped) so the analytics center can show "top errors", trends and
"new since yesterday" the way Aurora Log Insights / Azure Diagnostics do.
Also maps each line to a coarse operational *category* via a curated rule set.

Pure functions over already-redacted Loki entries — no source access here.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Prefix stripping — drop the log_line_prefix / Patroni metadata so signatures
# key off the message, not the per-event timestamp/pid/user.
# --------------------------------------------------------------------------
_PG_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[.,]\d+ \S+ \[\d+\] "
    r"(?:\S+@\S+/\S* )?(?:LOG|ERROR|FATAL|PANIC|WARNING|NOTICE|DETAIL|HINT|STATEMENT|CONTEXT):\s*")
_PATRONI_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ [A-Z]+:\s*")
_PGBOUNCER_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+ \w+ \[\d+\] \w+\s*")
_LOGFMT_TIME = re.compile(r'^time="[^"]*"\s+level=\w+\s+msg=')


def strip_prefix(message: str) -> str:
    for rx in (_PG_PREFIX, _PATRONI_PREFIX, _PGBOUNCER_PREFIX, _LOGFMT_TIME):
        m = rx.match(message)
        if m:
            return message[m.end():].strip()
    return message.strip()


# --------------------------------------------------------------------------
# Templating — replace variable tokens with placeholders (order matters).
# --------------------------------------------------------------------------
_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b[0-9A-F]+/[0-9A-F]+\b"), "<lsn>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*ms\b"), "<dur>"),
    (re.compile(r"'[^']*'"), "'<s>'"),
    (re.compile(r'"[^"]*"'), '"<s>"'),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b\d+\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def template(message: str) -> str:
    out = strip_prefix(message)
    for rx, repl in _SUBS:
        out = rx.sub(repl, out)
    return out.strip()[:300]


def signature_id(pattern: str) -> str:
    return hashlib.sha1(pattern.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# Categorization — coarse operational classes, curated for PG/Patroni/pgBackRest.
# First matching rule wins; order from most to least specific.
# --------------------------------------------------------------------------
CATEGORIES = [
    "authentication", "connection", "replication", "failover", "wal_checkpoint",
    "disk_space", "lock_deadlock", "vacuum", "backup", "query_error", "config", "other",
]
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("authentication", re.compile(r"password authentication failed|authentication failed|no pg_hba|role .* does not exist|permission denied|peer authentication|ident authentication", re.I)),
    ("failover", re.compile(r"failover|switchover|promot|leader|i am the leader|new leader|demot|lost leader|key not found|acquire.*lock|patroni", re.I)),
    ("replication", re.compile(r"replication|standby|wal sender|wal receiver|streaming|replica|recovery|slot|primary_conninfo|synchronous", re.I)),
    ("wal_checkpoint", re.compile(r"checkpoint|wal|archive_command|archiver|pg_wal|segment", re.I)),
    ("disk_space", re.compile(r"no space left|disk full|could not extend|out of disk|insufficient", re.I)),
    ("lock_deadlock", re.compile(r"deadlock|could not obtain lock|lock timeout|still waiting for|canceling statement due to lock", re.I)),
    ("vacuum", re.compile(r"vacuum|autovacuum|wraparound|bloat|freeze", re.I)),
    ("backup", re.compile(r"pgbackrest|backup|restore|repo1|stanza|archive-push|archive-get", re.I)),
    ("connection", re.compile(r"connection|too many clients|remaining connection slots|terminating connection|client closed|could not connect|time(?:d? ?out)|reset by peer|server conn crashed", re.I)),
    ("query_error", re.compile(r"syntax error|duplicate key|violates|division by zero|relation .* does not exist|column .* does not exist|invalid input|out of range|statement:", re.I)),
    ("config", re.compile(r"parameter|configuration|reload|pg_settings|guc|cannot be changed", re.I)),
]


def categorize(message: str) -> str:
    text = strip_prefix(message)
    for name, rx in _RULES:
        if rx.search(text):
            return name
    return "other"


# --------------------------------------------------------------------------
# Aggregation over normalized entries (from log_parse.flatten).
# --------------------------------------------------------------------------
def aggregate_signatures(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group entries by signature; return rows sorted by count desc."""
    sigs: dict[str, dict[str, Any]] = {}
    for e in entries:
        pat = template(e["message"])
        if not pat:
            continue
        sid = signature_id(pat)
        row = sigs.get(sid)
        if row is None:
            row = sigs[sid] = {
                "signature_id": sid, "pattern": pat, "count": 0,
                "severity": e["severity"], "category": categorize(e["message"]),
                "first_seen": e["ts"], "last_seen": e["ts"],
                "first_ns": e["ts_ns"], "last_ns": e["ts_ns"],
                "components": set(), "levels": set(), "sample": e["message"],
            }
        row["count"] += 1
        row["components"].add(e["component"])
        if e.get("level"):
            row["levels"].add(e["level"])
        if e["ts_ns"] < row["first_ns"]:
            row["first_ns"], row["first_seen"] = e["ts_ns"], e["ts"]
        if e["ts_ns"] > row["last_ns"]:
            row["last_ns"], row["last_seen"], row["sample"] = e["ts_ns"], e["ts"], e["message"]
        # keep the most severe severity seen
        if _SEV_RANK.get(e["severity"], 0) > _SEV_RANK.get(row["severity"], 0):
            row["severity"] = e["severity"]
    rows = []
    for r in sigs.values():
        r["components"] = sorted(r["components"])
        r["levels"] = sorted(r["levels"])
        rows.append(r)
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows


_SEV_RANK = {"info": 0, "warn": 1, "error": 2, "fatal": 3}
