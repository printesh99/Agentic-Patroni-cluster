#!/usr/bin/env python3
"""Generate 500 independent evidence-contract probes (25 domains x 20 conditions)."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "assistant_evidence_gap_500.jsonl"

# subject, accepted intents, authoritative sources, representative required fields
DOMAINS = [
    ("replication_alerts", "physical replication alert state", ["alerts", "replication_lag"],
     ["pg_stat_replication", "alert_rules"], ["replay_lag_bytes", "threshold_bytes"]),
    ("upgrade_history", "PostgreSQL upgrade history", ["upgrade_history", "unknown"],
     ["kubernetes_controllerrevision", "postgresql_startup_log", "audit"], ["version", "observed_at"]),
    ("primary_timeline", "primary identity and timeline transitions", ["cluster-state", "failover"],
     ["patroni_history", "pg_control_checkpoint"], ["primary_member", "timeline"]),
    ("backup_integrity", "backup integrity and restore readiness", ["backups"],
     ["pgbackrest"], ["backup_label", "stop_time"]),
    ("archive_continuity", "WAL archive continuity", ["wal_archive", "multi_intent"],
     ["pg_stat_archiver", "pgbackrest"], ["last_archived_wal", "failed_count"]),
    ("connection_capacity", "connection capacity and saturation", ["sessions", "metrics"],
     ["pg_stat_activity", "pg_settings"], ["active_connections", "max_connections"]),
    ("blocking_chains", "blocking lock chains", ["locks"],
     ["pg_locks", "pg_stat_activity"], ["blocked_pid", "blocking_pid"]),
    ("transaction_age", "long-running transaction age", ["sessions", "locks"],
     ["pg_stat_activity"], ["pid", "transaction_age_seconds"]),
    ("vacuum_health", "vacuum and analyze health", ["vacuum_bloat"],
     ["pg_stat_user_tables"], ["n_dead_tup", "last_autovacuum"]),
    ("wraparound_risk", "transaction-ID wraparound risk", ["vacuum_bloat"],
     ["pg_database", "pg_class"], ["xid_age", "freeze_max_age"]),
    ("database_storage", "database and relation storage", ["storage_wal"],
     ["pg_database_size", "pg_total_relation_size"], ["size_bytes", "database"]),
    ("pvc_headroom", "persistent-volume headroom", ["storage_wal", "metrics"],
     ["kubelet_volume_stats", "kubernetes_pvc"], ["capacity_bytes", "available_bytes"]),
    ("memory_pressure", "database-container memory pressure", ["cpu_capacity", "metrics"],
     ["kubernetes", "prometheus", "pg_settings"], ["working_set_bytes", "limit_bytes"]),
    ("cpu_throttling", "database-container CPU throttling", ["cpu_capacity", "metrics"],
     ["prometheus", "kubernetes"], ["usage_cores", "throttled_seconds"]),
    ("query_interval", "slow-query behavior during a requested interval", ["slow_queries"],
     ["pg_stat_statements_delta", "pg_profile"], ["queryid", "delta_exec_time"]),
    ("plan_regression", "SQL execution-plan regression", ["slow_queries", "unknown"],
     ["pg_profile", "plan_history"], ["queryid", "plan_hash"]),
    ("temporary_spill", "temporary-file and sort spill activity", ["slow_queries", "metrics"],
     ["pg_stat_statements", "pg_stat_database"], ["temp_blks_written", "temp_bytes"]),
    ("cache_io", "buffer-cache and physical I/O behavior", ["metrics"],
     ["pg_stat_database", "prometheus"], ["blks_hit", "blks_read"]),
    ("checkpoint_pressure", "checkpoint and background-writer pressure", ["metrics"],
     ["pg_stat_checkpointer", "pg_stat_bgwriter"], ["num_requested", "write_time"]),
    ("logical_slots", "logical-replication slot retention", ["logical_replication"],
     ["pg_replication_slots"], ["slot_name", "retained_wal_bytes"]),
    ("privilege_drift", "database role and privilege drift", ["security_roles"],
     ["pg_roles", "information_schema"], ["role_name", "privilege"]),
    ("certificate_expiry", "PostgreSQL and Patroni certificate expiry", ["unknown", "security"],
     ["kubernetes_secret", "x509_certificate"], ["not_after", "secret_name"]),
    ("operator_reconcile", "Postgres operator reconciliation state", ["unknown", "failover"],
     ["kubernetes_events", "postgrescluster_status"], ["condition", "reason"]),
    ("node_disruption", "database pod and node disruption history", ["failover", "unknown"],
     ["kubernetes_events", "patroni_history"], ["event_time", "reason"]),
    ("evidence_provenance", "assistant evidence provenance and freshness", ["unknown"],
     ["evidence_contract", "audit"], ["source", "collected_at"]),
]

PROBES = [
    ("current", "What is the current {subject}, with the collection timestamp and exact source?",
     ["answered", "partial", "source_unavailable"], ["current", "source"]),
    ("last_change", "When did {subject} last change, and what evidence proves the transition?",
     ["answered", "partial", "insufficient_evidence"], ["evidence", "change"]),
    ("trend", "Show the 24-hour trend for {subject}, including window, samples, minimum, maximum and average.",
     ["answered", "partial", "insufficient_evidence"], ["24", "trend"]),
    ("threshold", "Is {subject} breaching a configured threshold now? Return value, threshold and comparison.",
     ["answered", "partial", "insufficient_evidence"], ["threshold"]),
    ("unavailable", "If the authoritative source for {subject} is unavailable, identify it and do not invent a zero.",
     ["source_unavailable", "partial", "insufficient_evidence"], ["unavailable", "source"]),
    ("stale", "If evidence for {subject} is stale, report its age and refuse a current-state conclusion.",
     ["partial", "insufficient_evidence", "source_unavailable"], ["stale", "evidence"]),
    ("conflict", "How would you report {subject} when two authoritative sources disagree?",
     ["partial", "insufficient_evidence"], ["evidence", "source"]),
    ("provenance", "List the authoritative sources and exact evidence fields used to answer {subject}.",
     ["answered", "partial", "insufficient_evidence"], ["source", "evidence"]),
    ("cross_source", "Correlate PostgreSQL, Kubernetes and metrics evidence for {subject} without merging timestamps.",
     ["answered", "partial", "insufficient_evidence"], ["evidence"]),
    ("timeline", "Build a UTC event timeline for {subject}, separating observed facts from hypotheses.",
     ["answered", "partial", "insufficient_evidence"], ["timeline", "evidence"]),
    ("partial", "Answer {subject} when one required source succeeds and another times out; preserve the successful section.",
     ["partial", "source_unavailable"], ["source", "partial"]),
    ("identity_change", "Detect whether a primary or timeline change occurred while collecting {subject}.",
     ["answered", "partial", "insufficient_evidence"], ["timeline", "evidence"]),
    ("reset", "Explain whether a restart or statistics reset invalidates the requested {subject} comparison.",
     ["answered", "partial", "insufficient_evidence"], ["reset", "evidence"]),
    ("empty", "If no rows are returned for {subject}, distinguish a valid empty result from source failure.",
     ["answered", "partial", "source_unavailable"], ["source", "evidence"]),
    ("fallback", "Which fallback source, if any, is allowed for {subject}, and how is it labelled as noncanonical?",
     ["answered", "partial", "insufficient_evidence"], ["source"]),
    ("machine_contract", "Return {subject} with machine-readable status, missing evidence and sources checked.",
     ["answered", "partial", "insufficient_evidence", "source_unavailable"], ["status", "evidence"]),
    ("confidence", "State what is known and unknown about {subject}; do not turn temporal correlation into causation.",
     ["answered", "partial", "insufficient_evidence"], ["evidence", "unknown"]),
    ("next_check", "When {subject} cannot be proven, name the smallest safe read-only check that would resolve it.",
     ["insufficient_evidence", "partial", "answered"], ["read-only", "evidence"]),
    ("before_after", "Compare {subject} before and after the most recent relevant event, preserving both timestamps.",
     ["answered", "partial", "insufficient_evidence"], ["evidence"]),
    ("claim_binding", "Give a concise conclusion for {subject} and bind every factual claim to evidence identifiers.",
     ["answered", "partial", "insufficient_evidence"], ["evidence"]),
]


def build() -> list[dict]:
    rows = []
    for category, subject, intents, sources, fields in DOMAINS:
        for index, (probe, template, statuses, terms) in enumerate(PROBES, 1):
            rows.append({
                "id": f"gap-{category}-{index:02d}",
                "category": f"gap_{category}",
                "scenario": probe,
                "question": template.format(subject=subject),
                "critical": probe in {"unavailable", "conflict", "partial", "identity_change", "claim_binding"},
                "read_only": True,
                "expected_intents_any": intents,
                "expected_sources_any": sources,
                "expected_status_any": statuses,
                "expected_evidence_fields_any": fields,
                "required_answer_terms_any": terms,
                "forbidden_answer_terms": [
                    "definitely caused by", "0 errors" if probe == "unavailable" else "mutation completed"
                ],
                "require_claim_evidence": probe == "claim_binding",
                "max_latency_ms": 15000,
            })
    return rows


def main() -> None:
    rows = build()
    assert len(rows) == 500
    assert len({row["id"] for row in rows}) == 500
    assert len({row["question"].lower() for row in rows}) == 500
    old = {
        json.loads(line)["question"].strip().lower()
        for line in (HERE / "assistant_500.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert not old.intersection(row["question"].strip().lower() for row in rows)
    OUTPUT.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} independent cases to {OUTPUT}")


if __name__ == "__main__":
    main()
