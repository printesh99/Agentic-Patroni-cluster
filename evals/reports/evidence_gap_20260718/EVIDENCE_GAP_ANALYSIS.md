# AI Assistant Independent Evidence-Gap Analysis

- Cases: 500
- Fully passed: 1
- Critical failures: 125
- Median latency: 571.6 ms
- P95 latency: 16857.9 ms

This is a contract-discovery score, not an LLM accuracy score. Cases intentionally request
stale, conflicting, unavailable, historical and cross-source evidence.

## Root causes

| Root cause | Cases |
| --- | ---: |
| status_contract | 484 |
| typed_evidence_contract | 402 |
| answer_obligation | 397 |
| source_registry_or_collector | 236 |
| planner_or_routing | 217 |
| performance | 36 |
| claim_grounding | 24 |
| audit_contract | 1 |
| evidence_presence | 1 |
| transport_or_endpoint | 1 |

## Prioritized next phase

| Priority | Work | Cases exposed |
| --- | --- | ---: |
| P0 | Serve schema-v2 status/sections/missing_evidence on every assistant path. | 484 |
| P0 | Replace raw legacy dictionaries with versioned evidence contracts. | 402 |
| P0 | Bind factual claims to evidence IDs; deterministic fallback on validation failure. | 24 |
| P1 | Add canonical source registry and missing history/range collectors. | 236 |
| P1 | Plan evidence-condition intents instead of routing conditional probes as live facts. | 217 |
| P1 | Render required value, timestamp, threshold, uncertainty and provenance fields. | 397 |
| P2 | Bound slow fallback/model paths and add source-level single-flight/timeouts. | 36 |
| P2 | Ensure every outcome, including transport/source failure, has audit metadata. | 1 |

## Highest-gap domains

| Domain | Total contract failures |
| --- | ---: |
| gap_pvc_headroom | 101 |
| gap_temporary_spill | 101 |
| gap_certificate_expiry | 100 |
| gap_node_disruption | 100 |
| gap_operator_reconcile | 88 |
| gap_connection_capacity | 81 |
| gap_privilege_drift | 81 |
| gap_plan_regression | 80 |
| gap_query_interval | 80 |
| gap_wraparound_risk | 80 |
| gap_checkpoint_pressure | 79 |
| gap_replication_alerts | 79 |
| gap_memory_pressure | 77 |
| gap_cpu_throttling | 72 |
| gap_transaction_age | 61 |
| gap_backup_integrity | 60 |
| gap_blocking_chains | 60 |
| gap_logical_slots | 60 |
| gap_primary_timeline | 60 |
| gap_vacuum_health | 60 |
| gap_cache_io | 59 |
| gap_evidence_provenance | 57 |
| gap_upgrade_history | 56 |
| gap_database_storage | 44 |
| gap_archive_continuity | 23 |
