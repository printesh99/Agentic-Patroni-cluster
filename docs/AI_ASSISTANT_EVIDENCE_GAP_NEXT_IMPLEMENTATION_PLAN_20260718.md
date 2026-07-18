# AI Assistant Evidence-Gap: End-to-End Next Implementation Plan

Date: 2026-07-18  
Environment: UAT UAE PostgreSQL HA on OpenShift  
Application: Agentic Patroni Cluster / Object Monitor  
Status: approved implementation baseline  
Safety boundary: runtime diagnostic collectors are read-only. Implementation may write only Object Monitor metadata, source, tests and reviewed Object Monitor build/deployment resources. It does not authorize mutations to monitored PostgreSQL data, Patroni, PostgresCluster resources, PVCs or backups.

## 1. Purpose

Implement the evidence pipeline required to answer DBA questions using authoritative, timestamped and machine-verifiable evidence. The work is driven by a second independent corpus containing 500 unique questions across 25 domains and 20 evidence conditions, with no exact overlap with the original corpus.

Discovery baseline:

| Result | Count |
| --- | ---: |
| Fully passed | 1/500 |
| Missing schema-v2 status | 484 |
| Missing typed evidence fields | 402 |
| Answer-obligation failures | 397 |
| Missing or incorrect source contracts | 236 |
| Planner/routing failures | 217 |
| Claim-grounding failures | 24 |
| Latency failures | 36 |
| Critical failures | 125 |
| P95 latency | 16,857.9 ms |

This is an application and evidence-contract score, not a language-model intelligence score.

## 2. Required outcome

Every request must:

1. Detect all operational intents and evidence conditions.
2. Select only registered read-only collectors.
3. Collect concurrently with bounded timeouts.
4. Validate source identity, freshness, fields and cluster consistency.
5. Separate facts, hypotheses, unknowns and unavailable evidence.
6. Bind every factual claim to evidence identifiers.
7. Return schema v2 on every path.
8. Persist a redacted audit record.
9. Avoid model generation when required evidence is absent.
10. Pass all release gates in this document.

## 3. Target request flow

~~~mermaid
flowchart TD
    A[Assistant request] --> B[Safety preprocessor]
    B --> C[Intent and evidence-condition planner]
    C --> D[Typed QueryPlan]
    D --> E[Bounded concurrent executor]
    E --> F[Read-only source transports]
    F --> G[Typed evidence objects]
    G --> H[Freshness and consistency validator]
    H --> I[Claim builder]
    I --> J[Deterministic composer]
    J --> K[Optional constrained LLM renderer]
    K --> L[Post-render claim validator]
    L --> M[Schema-v2 response and audit]
~~~

The LLM is never the source of operational facts. It may classify ambiguous wording or render validated claims, but cannot choose arbitrary tools, create evidence values, authorize mutations or promote hypotheses to facts.

## 4. Universal response contract

All deterministic, safety, RCA, fallback and error paths return:

~~~json
{
  "status": "answered | partial | insufficient_evidence | source_unavailable | unsafe_request | generation_failed",
  "intent": "legacy-compatible-intent",
  "intents": ["typed_intent"],
  "sections": [],
  "evidence_items": [],
  "claims": [],
  "missing_evidence": [],
  "sources_checked": [],
  "unsupported_claims": [],
  "safety": {
    "read_only": true,
    "mutation_executed": false,
    "injection_detected": false
  },
  "audit": {}
}
~~~

Status rules:

- answered: every required section is complete and fresh.
- partial: at least one section is complete and another is incomplete, including when another section's required source failed.
- insufficient_evidence: no authoritative evidence supports the conclusion.
- source_unavailable: no section is complete and a required collector failed, timed out or was forbidden.
- unsafe_request: no safe factual portion remains.
- generation_failed: optional rendering failed after evidence validation; return the validated deterministic response.
- Missing status is release-blocking.

Deterministic precedence is `unsafe_request`, `generation_failed`, `answered`, `partial`, `source_unavailable`, then `insufficient_evidence`. Per-section outcomes are always preserved and are never hidden by the overall status.

### 4.1 Deadline and retry budget

- The end-to-end deterministic deadline is 3,000 ms, measured from request admission through schema-v2 validation.
- Each source defaults to a 1,000 ms timeout unless its registry contract specifies a smaller value.
- Collectors run concurrently and receive the request deadline and a cancellation signal.
- Pending collectors are cancelled when the global deadline expires; late results are discarded.
- An idempotent collector may retry once only for a topology-fence change and only when the remaining global budget can accommodate its source timeout.
- Transport retries for timeout, unavailable, forbidden or malformed outcomes are disabled by default. The registry must explicitly authorize any retry and the total remains inside the 3,000 ms deadline.

## 5. Planner changes

The planner detects domain intents plus these evidence conditions:

~~~text
current
historical
trend
threshold
source_unavailable
stale
conflicting
cross_source
timeline
partial_source
identity_change
statistics_reset
empty_result
fallback
machine_contract
known_unknown
before_after
claim_binding
~~~

Example upgrade plan:

~~~json
{
  "intents": ["upgrade_history"],
  "conditions": ["historical", "timeline", "claim_binding"],
  "required_sources": [
    "kubernetes_controllerrevision",
    "postgresql_startup_log",
    "change_audit"
  ]
}
~~~

Conditional questions such as “if Loki is unavailable” must not query healthy Loki and present current counts. Tests use controlled fault transports; normal requests explain the registered failure contract.

All corpus cases that make factual assertions require claim binding. Evaluators verify every fact references existing evidence IDs, reject altered or unsupported claims, and apply a 3,000 ms ceiling to every case.

## 6. Canonical source registry

Create one versioned registry used by runtime, evaluator and corpus tooling:

~~~text
intent
canonical source name
transport
required fields
optional fields
freshness TTL
timeout
row and payload limits
redaction policy
allowed fallback
answer obligations
~~~

The registry is the only route to a collector. Query plans reference registered source identifiers and produce typed evidence; raw dictionaries and unregistered transport calls are rejected.

Source labels must remain truthful:

- derived_replication_threshold is not grafana_alerts;
- pg_stat_statements totals are not pg_stat_statements_delta;
- pod creation time is not an upgrade timestamp;
- a current WAL segment is not necessarily archived.

## 7. Collector backlog

### 7.1 P0: upgrade-history collector

Sources:

- PostgresCluster postgresVersion;
- Kubernetes ControllerRevision timestamps and pod-template images;
- current and retained image digests;
- PostgreSQL startup version logs;
- Kubernetes events;
- application and change audit history.

Required fields:

~~~text
current_version
current_image_digest
previous_image_digest
rollout_observed_at
first_startup_observed_at
version_change_proven
evidence_limitations
~~~

Distinguish cluster creation, image rollout, minor upgrade, major upgrade and restart. If the previous version is unavailable, report the rollout timestamp as an observation and explicitly state that it does not prove an upgrade.

### 7.2 P0: alert-evidence collector

Sources:

- Alertmanager or Grafana API when configured;
- derived threshold rule;
- live measured value;
- threshold configuration;
- collection status and timestamp.

Required fields:

~~~text
alert_name
alert_source
state
measured_value
threshold
comparison
collected_at
source_available
~~~

Say “no derived replication-lag alert” rather than “no Grafana alert” when Alertmanager was not queried. Source errors must not silently become an empty alert list.

### 7.3 P0: universal evidence adapter

Wrap every legacy deterministic result with:

- versioned contract;
- section status;
- evidence IDs;
- canonical source;
- collection timestamps;
- freshness;
- answer obligations.

No raw dictionary bypasses validation.

### 7.4 P1: historical snapshot store

Persist timestamped observations for:

- PostgreSQL version and image digest;
- primary member and timeline;
- replication lag and alerts;
- PostgreSQL settings;
- CPU and memory;
- database, WAL and PVC storage;
- certificate expiry;
- operator conditions;
- query-statistics reset identity.

Each snapshot includes system identifier, primary, timeline, source, collection window, schema version and redaction state.

### 7.4.1 Metadata storage and governance

- Store snapshots and audits only in the Object Monitor metadata database, never in a monitored PostgreSQL cluster.
- Add reviewed, forward-only migrations for versioned snapshot and audit tables. Migration failure must fail closed and leave the prior schema usable.
- Use a dedicated least-privilege metadata role limited to the assistant schema; collectors receive no DDL, monitored-database write or Kubernetes mutation privilege.
- Encrypt metadata in transit and at rest. Never persist decoded credentials, authorization headers, connection strings, raw certificates, sensitive query literals or unredacted prompts.
- Apply source-specific redaction before persistence and audit serialization. Redaction failure prevents persistence and records only a bounded, non-sensitive failure code.
- Bound evidence, request, response and audit payloads and enforce registry row limits. Oversized collections become explicit partial outcomes.
- Audit persistence is bounded and best effort; expose `audit.persistence_status`. Failure must not alter evidence facts or cause a monitored-system write.
- Retain audits for 30 days and snapshots for 90 days by default. Extensions require documented data-owner approval; bounded deletion applies only to Object Monitor metadata.

### 7.5 P1: missing collectors

Implement:

- PVC used, available, capacity and growth;
- X.509 not-before and not-after;
- operator conditions and reconciliation events;
- node and pod disruption history;
- CPU throttling counters and rates;
- temporary spill interval metrics;
- pg_stat_statements deltas with reset detection;
- pg_profile interval evidence;
- plan hash and plan-history comparison;
- checkpoint and background-writer range metrics;
- logical-slot retained-WAL history;
- privilege-drift snapshots.

## 8. Freshness and consistency

Every evidence contract has a TTL. Stale evidence must include its age and cannot support words such as current, healthy, safe or ready.

Cross-source requests record collection start/end, system identifier, primary and timeline. Fence topology before and after collection. Retry an idempotent collector once following a topology change; otherwise mark inconsistent_snapshot.

Never combine pre-failover and post-failover values as one consistent observation.

## 9. Source outcome model

| State | Meaning |
| --- | --- |
| Empty | Source succeeded and authoritatively returned no rows |
| Unavailable | Transport failed, timed out or was forbidden |
| Missing | Required collector does not exist or was not planned |
| Stale | Evidence exists but exceeds TTL |
| Conflicting | Valid sources disagree |
| Partial | Some required evidence is complete |

Unavailable evidence must never become zero, empty, healthy or not firing. Conflicts show both observations, sources and timestamps.

## 10. Claim grounding

Every operational statement becomes a claim:

~~~json
{
  "id": "claim-1",
  "type": "fact",
  "text": "Replication replay lag is 0 bytes.",
  "evidence_ids": ["ev-1"]
}
~~~

Rules:

- facts require valid evidence IDs;
- hypotheses include supporting and contradicting evidence;
- limitations reference missing evidence;
- altered values, times, identities or states fail validation;
- unsupported claims are removed and recorded;
- generation failure falls back to deterministic text.

## 11. Fault-injection harness

Dependency-injected transports support:

~~~text
success
timeout
unavailable
forbidden
malformed
empty
partial
stale
conflicting
topology_change
statistics_reset
~~~

The production image must not expose an HTTP fault header. Any integration adapter is excluded from production packaging.

Packaging tests inspect the production build context and image module inventory to prove fault adapters, routes, headers and test-only dependencies are absent.

## 12. RCA contract

RCA output separates:

- UTC timeline;
- facts;
- hypotheses;
- contradicting evidence;
- missing evidence;
- safe read-only checks;
- bounded confidence derived from completeness.

Temporal proximity is not causation. Record clock uncertainty, scrape interval, ingestion delay, duplicates, gaps and reset events.

## 13. ML and LLM training workflow

Failed runtime answers are not training truth. The current queue is:

~~~text
evals/reports/evidence_gap_20260718/training_candidates_needs_adjudication.jsonl
~~~

Every row starts as training_eligible=false and needs_dba_adjudication.

DBA labels:

~~~text
runtime_defect
missing_collector
unsupported_question
source_unavailable
evaluation_calibration
approved_training_example
rejected_training_example
~~~

Training workflow:

1. Collect redacted prompt, plan, evidence and response.
2. Run contract validation.
3. Assign automated root-cause labels.
4. Obtain DBA adjudication.
5. Write corrected evidence-grounded answers.
6. Scan for secrets and personal data.
7. Version dataset and hashes.
8. Split by scenario and domain, not paraphrase.
9. Train or index approved rows only.
10. Evaluate against held-out adversarial and fault cases.

Never train the model to compensate for a missing collector by guessing.

## 14. Evaluation strategy

Maintain two independent suites:

1. Original 500-case regression suite.
2. Independent 500-case evidence-gap suite.

Add held-out cases for upgrade-versus-rollout, derived-versus-Grafana alerts, stale evidence, simultaneous timeout and failover, conflicting topology, statistics resets, authoritative empty results, redaction and multi-turn freshness.

Golden expectations remain independently reviewed and are never generated from runtime output.

## 15. Implementation phases

### Phase A: universal schema and adapters

- Wrap every legacy tool.
- Add status derivation, evidence IDs and claims.
- Introduce canonical source registry.

Exit gate: zero missing statuses; original corpus remains 100%.

### Phase B: alert and upgrade evidence

- Implement both P0 collectors.
- Correct UI attribution.
- Test rollout-versus-upgrade distinction.

Exit gate: both evidence-gap domains at least 18/20; unavailable sources never become empty success.

### Phase C: history and missing collectors

- Add snapshot store and P1 collectors.

Exit gate: each implemented domain at least 18/20; typed-evidence failures reduced by at least 80%.

### Phase D: fault, conflict and topology consistency

- Add fault matrix, conflict validator, topology fences and reset detection.

Exit gate: unavailable, stale, partial, conflict and topology-change scenarios pass 100%.

### Phase E: DBA adjudication and training preparation

- Review candidates, correct approved examples, redact and version datasets.

Exit gate: no unreviewed row enters training.

### Phase F: UAT rollout

- Build immutable image.
- Copy the currently deployed registry manifest to a dedicated rollback reference before replacing the deployment.
- Record its immutable digest and prove it is pullable by digest.
- Deploy and validate health.
- Run both 500-case suites.
- Verify PostgreSQL HA remains unchanged.

## 16. Release gates

| Gate | Requirement |
| --- | --- |
| Original corpus | 100%, zero critical failures |
| Evidence-gap corpus | At least 95%, zero critical failures |
| Status contract | Zero missing statuses |
| Claim grounding | Zero unsupported factual claims |
| Fault suite | 100% |
| Safety suite | 100% |
| Deterministic P95 | At most 3,000 ms |
| Production mutation | None |
| HA health | Leader plus streaming synchronous standby |
| Audit | Every outcome recorded and redacted |

## 17. Rollout and rollback

Before rollout:

- record current digest and revision;
- copy the current registry manifest to a rollback reference without rebuilding it;
- verify the copied manifest has the expected immutable digest and is pullable by digest before deployment replacement;
- retain evaluation evidence;
- preserve unrelated working-tree changes.

Rollback triggers:

- critical evaluation failure;
- readiness or liveness failure;
- credential leakage;
- unsupported confident claim;
- mutation execution;
- source pressure affecting PostgreSQL or Patroni;
- latency regression.

Rollback changes only Object Monitor. It must not alter PostgreSQL, Patroni, PostgresCluster resources, PVCs or backups.

## 18. Required artifacts

~~~text
app/assistant/contracts.py
app/assistant/registry.py
app/assistant/evidence.py
app/assistant/validator.py
app/assistant/transports.py
app/assistant/history.py
app/assistant/audit.py
app/assistant/redaction.py
app/assistant/migrations/
tests/test_assistant_schema_v2.py
tests/test_assistant_source_registry.py
tests/test_assistant_query_plan.py
tests/test_assistant_upgrade_history.py
tests/test_assistant_alert_evidence.py
tests/test_assistant_fault_matrix.py
tests/test_assistant_claim_grounding.py
tests/test_assistant_snapshot_consistency.py
tests/test_assistant_audit_redaction.py
tests/test_assistant_snapshot_migration.py
tests/test_assistant_production_package.py
evals/assistant_evidence_gap_500.jsonl
evals/reports/evidence_gap_20260718/
~~~

## 19. Source evidence

- evals/assistant_evidence_gap_500.jsonl
- evals/reports/evidence_gap_20260718/assistant_eval.json
- evals/reports/evidence_gap_20260718/evidence_gap_analysis.json
- evals/reports/evidence_gap_20260718/EVIDENCE_GAP_ANALYSIS.md
- evals/reports/evidence_gap_20260718/training_candidates_needs_adjudication.jsonl

## 20. Definition of done

Complete only when:

- all six phases pass;
- both 500-case suites meet their gates;
- failures have adjudication records;
- unsupported domains return machine-readable inability;
- every operational claim is evidence-bound;
- training inputs are DBA-approved and redacted;
- Object Monitor is healthy and reversible;
- the rollback registry manifest digest is recorded and pullable;
- PostgreSQL HA remains unchanged and healthy.

## 21. Next-session checkpoint

Checkpoint date: 2026-07-18

The only valid measured evidence-gap result remains the discovery baseline: **1/500 passed, 499/500 failed, 125 critical failures, and P95 16,857.9 ms**. Do not subtract failures based only on code changes. Regenerate and rerun the suite before reporting improved counts.

### 21.1 Foundation completed after the discovery run

- Corrected this plan's safety boundary, deterministic status precedence, 3,000 ms global deadline, 1,000 ms default source timeout, metadata governance, production fault-adapter exclusion and registry-manifest rollback requirements.
- Added `app/assistant/contracts.py` with typed source outcomes and source contracts.
- Added `app/assistant/registry.py` with registered physical-replication and WAL-archiver sources.
- Extended `QueryPlan` with evidence conditions, required sources, answer obligations and deadline fields.
- Added `generation_failed` and factual claim type support to the schema.
- Added `app/assistant/validator.py` to reject factual claims with absent or invalid evidence IDs.
- Routed the two typed collectors through the registry and claim validator.
- Focused regression after these changes: **12 passed in 0.40s**.

These changes address part of the common foundation only. They do not prove any of the 499 discovery failures are resolved.

### 21.2 Remaining discovery issues and required solutions

| Discovery issue | Baseline cases | Remaining implementation |
| --- | ---: | --- |
| Missing schema-v2 status | 484 | Route every legacy, fallback, safety, RCA and exception path through one universal response adapter; add schema-v2 tests for every terminal path. |
| Missing typed evidence fields | 402 | Implement `evidence.py`; convert all raw legacy dictionaries into versioned contracts; validate required fields, payload limits, timestamps and freshness. |
| Answer obligations not rendered | 397 | Make obligations executable per section and render required value, timestamp, threshold/comparison, uncertainty, provenance, limitations and smallest safe next check. |
| Missing/wrong source contract | 236 | Complete the canonical registry for all 25 domains and implement missing current, historical and range collectors with truthful source labels. |
| Planner/routing failures | 217 | Detect all 20 evidence conditions, multi-intent questions and conditional fault questions; reject unregistered sources and raw collector bypasses. |
| Latency failures | 36 | Add the 3,000 ms global deadline, per-source timeout, cancellation, late-result discard, bounded retry and single-flight/cache behavior; prevent an unbounded model fallback. |
| Claim-grounding failures | 24 | Require claim binding for every factual answer, validate all referenced IDs and values, remove unsupported claims and use deterministic fallback after render failure. |
| Audit contract | 1 | Persist a bounded redacted audit for every status, including source and transport failures, and expose persistence status. |
| Evidence/transport availability | 1 each | Return explicit source outcomes and missing evidence; never convert unavailable, forbidden or timeout into zero or authoritative empty. |

### 21.3 Remaining domain collectors

All domains require universal schema, condition semantics, obligations, audit and claim validation. Domain-specific work is:

| Domain | Baseline | Required collector or correction |
| --- | ---: | --- |
| Archive continuity | 19/20 failed | Finish typed pgBackRest correlation and historical continuity; retain the truthful current-WAL versus archived-WAL distinction. |
| Replication alerts | 20/20 failed | Add configured Alertmanager/Grafana evidence plus derived-rule evidence, measured lag, threshold and truthful attribution. |
| Upgrade history | 20/20 failed | Correlate PostgresCluster version, ControllerRevisions, image digests, startup logs, events and change audit; distinguish rollout/restart from proven upgrade. |
| Primary timeline | 20/20 failed | Add Patroni history, checkpoint timeline and before/after topology fences. |
| Backup integrity | 20/20 failed | Add authoritative pgBackRest repository/backup evidence and limitations; do not infer remote readiness from the known local permission error. |
| Connection, blocking and transaction age | 60/60 failed | Type `pg_stat_activity`, settings and lock-chain evidence with timestamps and empty-result semantics. |
| Vacuum and wraparound | 40/40 failed | Type table/database age, freeze thresholds and vacuum/analyze observations. |
| Database storage and PVC headroom | 40/40 failed | Add database/relation sizes plus PVC capacity, available, used and snapshot-derived growth. |
| Memory and CPU throttling | 40/40 failed | Correlate Kubernetes limits with Prometheus working set, CPU usage and throttling counters/rates. |
| Query interval and plan regression | 40/40 failed | Add statement deltas with reset identity, pg_profile intervals and plan hash/history comparison. |
| Temporary spill and cache I/O | 40/40 failed | Add interval deltas for temp blocks/bytes and buffer reads/hits; do not label cumulative totals as interval evidence. |
| Checkpoint pressure | 20/20 failed | Add checkpointer/background-writer interval or range evidence. |
| Logical slots | 20/20 failed | Add retained-WAL current evidence and snapshot history. |
| Privilege drift | 20/20 failed | Add roles/privileges snapshots, normalized comparison and redaction. |
| Certificate expiry | 20/20 failed | Parse X.509 not-before/not-after from approved secrets without persisting certificate or key material. |
| Operator reconciliation | 20/20 failed | Add namespace-scoped PostgresCluster conditions and Kubernetes reconciliation events. |
| Node disruption | 20/20 failed | Add pod/node events and Patroni history with deduplication and clock uncertainty. |
| Evidence provenance | 20/20 failed | Complete audit, source identity, freshness, collection window and redaction contracts. |

### 21.4 Corpus and evaluator correction required before rerun

The checked-in generator and corpus are still discovery versions:

- `require_claim_evidence` is true only for the 25 `claim_binding` scenarios. Change it to true for every case that may return factual claims, then verify every factual claim references an existing evidence ID.
- `max_latency_ms` is 15,000 for all 500 cases. Change it to 3,000.
- Add checks for section-status precedence, invalid evidence IDs, unsupported or altered values, freshness, empty versus unavailable, conflicts, topology changes, statistics resets, audit redaction and production-package exclusion.
- Regenerate `evals/assistant_evidence_gap_500.jsonl`; assert 500 unique cases, 25 domains, 20 scenarios per domain, 125 critical cases and zero overlap with the original corpus.
- Preserve the discovery report; write the new run to a new timestamped report directory.

### 21.5 Exact next-session order

1. Run the focused 12-test baseline and inspect the working tree without overwriting unrelated changes.
2. Add tests for the source registry, typed query plan and claim validator.
3. Implement `evidence.py` and the universal legacy adapter; remove raw-dictionary bypasses.
4. Implement `transports.py` with the global deadline, cancellation and dependency-injected fault outcomes.
5. Implement the P0 upgrade-history and replication-alert collectors and tests.
6. Implement `redaction.py`, `audit.py`, `history.py` and forward-only metadata migrations with bounded retention.
7. Implement P1 collectors in the domain order in section 21.3.
8. Add topology fences, conflict/stale/empty/partial/reset semantics and the complete fault matrix.
9. Correct and regenerate the independent corpus, then run focused tests, the full offline suite, original 500 and independent 500 suites.
10. Perform read-only UAT validation. Only after all release gates pass, create and verify the registry-level rollback manifest, build and deploy Object Monitor, and revalidate both suites and unchanged PostgreSQL HA.

No PostgreSQL, Patroni, PostgresCluster, PVC, backup or deployment mutation is authorized by this checkpoint.
