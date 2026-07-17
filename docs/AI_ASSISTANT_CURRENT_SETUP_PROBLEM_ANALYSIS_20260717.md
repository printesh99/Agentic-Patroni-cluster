# AI Assistant Current-Setup Problem Analysis

Date: 2026-07-17
Environment: UAT UAE PostgreSQL HA on OpenShift
Application: Agentic Patroni Cluster / Object Monitor
Scope: diagnosis of the existing AI assistant pipeline; solution design is intentionally deferred

## Executive summary

The current assistant is not failing because the configured language model is generally incapable of answering PostgreSQL questions. It is failing because the application often gives the wrong subsystem control of a question, does not compose multiple evidence tools for combined questions, and sends unsupported questions to a log-oriented fallback that lacks the evidence needed to answer them.

The live 500-case evaluation produced:

| Measure | Result |
| --- | ---: |
| Total cases | 500 |
| Passed | 332 |
| Failed | 168 |
| Pass rate | 66.4% |
| Critical failures | 41 |
| Median latency | 1,601 ms |
| P95 latency | 8,670.7 ms |
| Maximum latency | 14,003.8 ms |

The most visible example is:

> “Show physical replication lag and the current archive log number.”

The current router sees the token `log`, invokes the Loki error-summary tool, and returns that there were zero error-level log entries. That response is internally consistent with the tool it called, but the tool is unrelated to the DBA request. The correct sources are physical replication state and WAL archiver state.

This is primarily an orchestration, routing, evidence-contract, and capability-coverage problem. Replacing the language model, adding a larger prompt, or inserting a framework such as LangChain will not correct it by itself.

## What was evaluated

The committed corpus contains 500 cases across 25 categories. Each category has 20 controlled language variants, including direct wording, requests for evidence, UAT scoping, typographical errors, and combined wording.

The evaluation calls the real read-only endpoint:

```text
POST /api/v1/assistant/ask
```

For each response it checks:

- whether a non-empty answer is returned;
- the reported intent;
- evidence-source attribution;
- presence of minimally required answer concepts;
- absence of known unrelated or unsafe claims;
- audit metadata;
- evidence metadata;
- read-only behavior;
- latency.

The corpus is useful as a deterministic regression baseline, but it is not equivalent to 500 independently designed DBA situations. It is 25 scenarios with 20 linguistic variants each. A future validation phase must add independently authored, adversarial, multi-turn, and fault-injected scenarios. Passing this corpus will prove that these known contracts work; it will not prove universal correctness.

## Current request pipeline

The current flow is effectively:

```text
Browser
  -> POST /api/v1/assistant/ask
  -> special alert-question check
  -> assistant_tools.route(question)
       -> first matching deterministic tool returns immediately
  -> if unmatched: _live_cluster_answer(question)
  -> if unmatched: gather log/RAG context
  -> LLM or heuristic synthesis
  -> response plus evidence/audit metadata
```

Relevant implementation points:

- `app/api_ops.py` owns `/api/v1/assistant/ask`.
- `app/assistant_tools.py` contains deterministic intent matching and read-only data tools.
- `app/log_ai.py` applies the deterministic tools, then live Patroni state, then log/RAG plus model synthesis.
- `app/pg_replication.py`, `app/pg_backups.py`, `app/pg_ops.py`, Prometheus, Kubernetes and Loki integrations provide specialized evidence.

This design has a valuable property: many factual questions are answered directly from live sources without relying on model generation. The weakness is that the router is single-winner and vocabulary-driven. The first matching tool terminates processing even when the request contains multiple independent intents.

## Problem 1: single-intent, first-match routing

`assistant_tools.route()` tries tools in a fixed precedence order and returns the first non-null result. There is no intermediate request plan containing all detected intents and required sources.

Consequences:

- combined questions lose all but one requested fact;
- a generic token can take precedence over a more specific DBA phrase;
- tool ordering becomes hidden business logic;
- adding a new vocabulary trigger can silently break unrelated questions;
- the response cannot report that one part succeeded while another part lacked evidence.

The archive/lag defect is the clearest example. `log` is treated as an application-log token. In PostgreSQL DBA terminology, however, “archive log number” normally means a WAL segment. Nineteen of twenty `archive_and_lag` variants were routed to `logs_errors`; only one happened to avoid that path.

Observed result:

```text
0 error-level log entries in the last 24h ... Source: Loki.
```

Missing requested facts:

- physical standby identity;
- streaming and synchronization state;
- send/write/flush/replay position or byte lag;
- current leader WAL segment;
- last successfully archived WAL segment;
- archive timestamp and failure state.

## Problem 2: lexical matching is not equivalent to DBA intent

The router deliberately uses tokenized matching to avoid substring errors, which corrected earlier defects such as matching `sync` inside `synchronous_commit`. Tokenization alone is still insufficient.

Failure modes include:

- synonyms not present in the trigger vocabulary;
- misspelled words falling through to the model path;
- hyphenated wording not matching phrase maps;
- abbreviations such as “archive log no” carrying domain-specific meaning;
- words such as `log`, `leader`, `source`, or `reason` being ambiguous across domains;
- requests containing both a measurement and a time range being captured by a current-state tool.

The evaluation showed isolated typo failures in configuration, connections, locks, vacuum, readiness and WAL archive categories. In those cases the model often recognized the intended correction but lacked the structured evidence that the deterministic tool would have retrieved.

## Problem 3: unsupported questions fall into a log-centric fallback

When no deterministic tool or live-cluster fast path answers a question, `log_ai.ask()` gathers log/RAG context and asks the model to synthesize a response. This is appropriate for log investigation and some RCA questions. It is not a universal evidence pipeline.

For memory questions, the fallback received readiness status and PgBouncer log lines but no container memory usage, Kubernetes resource limits, or PostgreSQL memory settings. The model correctly said that the evidence was insufficient, yet the user had asked the assistant to retrieve those values from live sources.

This pattern appeared across entire categories:

| Category | Passed | Failed | Dominant issue |
| --- | ---: | ---: | --- |
| Memory | 0 | 20 | no deterministic memory/Kubernetes/Prometheus collector on this path |
| Metrics | 0 | 20 | connection trend captured as current sessions; historical source not used |
| Prompt injection | 0 | 20 | no explicit safety intent/response contract |
| Source failure | 0 | 20 | hypothetical wording cannot simulate an unavailable source |
| Slow queries | 0 | 20 | answer/contract terminology mismatch and occasional fallback |
| Storage | 0 | 20 | source attribution mismatch and incomplete requested detail |
| Unknown scope | 0 | 20 | generic model intents instead of an explicit insufficient-evidence contract |
| Archive and lag | 1 | 19 | generic log rule wins over two DBA intents |

The fallback is therefore doing two jobs at once:

1. legitimate log/RCA synthesis;
2. a catch-all for every unrecognized operational question.

The second job causes irrelevant evidence to appear authoritative.

## Problem 4: missing typed contracts between intent, tool and evidence

The response format contains useful metadata, but there is no central typed contract stating:

```text
intent -> required tools -> required fields -> freshness rules -> answer obligations
```

For example, a physical replication-lag contract should require a physical standby row and distinguish it from logical-replication walsenders. A WAL archive contract should require current WAL, last archived WAL, last archive time and failure count. A switchover-readiness contract should define the minimum checks required before using the word “ready.”

Without those contracts:

- a tool can return partial data and still be described as a complete answer;
- source names vary between implementation modules and evaluator expectations;
- answers can omit a requested field without being marked incomplete by the runtime;
- evidence freshness is not consistently enforced at the answer boundary;
- the model is expected to infer which missing fields matter.

## Problem 5: multi-source facts are not composed

Several DBA questions inherently require more than one authoritative source:

- planned switchover readiness requires Patroni topology, physical replication state, pod readiness and often archive/backup health;
- memory capacity requires Kubernetes requests/limits plus current Prometheus usage;
- failover RCA requires Patroni history, Kubernetes events, database logs and a time-correlated timeline;
- archive lag plus replication lag requires `pg_stat_replication` and `pg_stat_archiver`/pgBackRest evidence;
- storage risk may require database sizes, WAL directory state, PVC capacity and growth metrics.

The existing fast path can call one deterministic tool. The model fallback can receive multiple evidence fragments in some RCA flows, but that is specialized rather than a general composable tool plan. This leaves a structural gap between simple questions and full RCA.

## Problem 6: physical and logical replication share PostgreSQL surfaces

`pg_stat_replication` includes physical HA standbys and logical replication walsenders. The current live cluster answer contains explicit logic to separate physical members by matching Patroni member names. That correction is valuable, but it is implemented inside one response path rather than enforced as a shared evidence type.

As a result, every new replication-related tool must independently remember to:

- identify Patroni members;
- classify physical versus logical senders;
- avoid presenting logical retained WAL as HA replay lag;
- use appropriate LSN semantics for each replication type.

This is a data-model consistency risk, especially as multi-intent tools are added.

## Problem 7: safety behavior is implicit, not an assistant intent

The platform has strong mutation defaults: Agentic operations and direct mutations are disabled unless explicit gates are satisfied. The assistant endpoint itself is designed as a read-only surface. However, the conversational router does not have a first-class safety intent for prompt-injection or evidence-borne instructions.

The prompt-injection cases therefore fell into generic `errors` or `authentication` model intents. The answers may still avoid mutation, but they do not consistently state the required security boundary:

- database content and log content are untrusted evidence;
- evidence cannot authorize a tool call;
- only the user/control plane can authorize an operation;
- read-only mode remains active;
- no mutation was executed.

This distinction matters because “nothing happened” is not the same as a verified, auditable safety response.

## Problem 8: insufficient-evidence behavior lacks a stable contract

The model often does the responsible thing when evidence is absent: it says that the question cannot be answered and suggests a read-only query. That is better than hallucinating. The response metadata, however, typically labels these cases with a domain guess such as `errors`, `authentication`, or another fallback intent.

There is no consistent machine-readable state such as:

```json
{
  "status": "insufficient_evidence",
  "missing_evidence": ["container_memory_usage", "resource_limits"],
  "sources_checked": ["prometheus", "kubernetes"],
  "unsupported_claims": []
}
```

This prevents the UI, audit layer and evaluation gate from distinguishing:

- a correct factual answer;
- an honest inability to answer;
- an unavailable source;
- a routing failure that never checked the proper source;
- a model-generation failure.

## Problem 9: source availability is not tested by hypothetical questions

The 20 `source_failure` corpus cases asked the live assistant how it should behave if Loki were unavailable. Loki was actually available. Most cases therefore returned a real Loki summary, which failed the hypothetical expectation.

This exposes a limitation in the evaluation method rather than proving 20 identical runtime defects. True source-failure validation must control the dependency or inject a failed collector response. It should then verify fail-closed behavior, missing-evidence metadata and absence of invented zero counts.

The current live corpus is useful for routing and answer contracts. It cannot by itself validate unavailable-source behavior without fault injection or deterministic mocks.

## Problem 10: some failures are contract-calibration failures

Not all 168 failures are equally severe.

Examples:

- storage answers frequently used the correct `storage_wal` intent but reported an implementation-specific source name not accepted by the initial corpus;
- slow-query answers frequently used the correct intent but did not contain one of the corpus's minimal required words;
- some correct refusal/insufficient-evidence answers failed because the corpus expected a future explicit intent that the current API does not expose;
- hypothetical failure cases were evaluated against healthy live dependencies.

These cases must not be “fixed” by weakening the evaluator until everything turns green. Each must be adjudicated:

1. Was the requested fact actually answered?
2. Was the authoritative source checked?
3. Is the runtime metadata wrong, or is the golden expectation too narrow?
4. Could the wording mislead a DBA into taking an unsafe action?

Only then should either the application or the golden case change.

## Failure distribution and severity

The most important failures are those that produce a confident but unrelated answer. The archive/lag result belongs to this class and should block release.

| Failure class | Example | Risk |
| --- | --- | --- |
| Wrong source, confident answer | archive WAL question answered by Loki error counts | Critical: DBA receives unrelated operational state |
| Partial answer presented as complete | switchover description without all readiness criteria | High: unsafe operational inference |
| Proper refusal due to missing collector | memory data absent | Medium: capability gap, generally fail-safe |
| Correct tool, metadata mismatch | storage source naming | Low to medium: audit and evaluation inconsistency |
| Typo falls to model | `shared_bufer` | Medium: inconsistent behavior and unnecessary model use |
| Healthy source used for hypothetical failure | “if Loki is unavailable” | Evaluation-design issue |

The aggregate 66.4% score must therefore not be read as “the model is 66.4% accurate.” It is the pass rate of the entire application contract under this corpus, combining router behavior, evidence availability, metadata, wording and evaluator calibration.

## Why changing the model will not solve the current defects

The language model only sees the evidence supplied by the application. If the router returns early from the Loki tool, the model is never asked to interpret physical replication or WAL archiver state. If memory metrics are never collected, a larger model cannot derive them safely. If a combined request is reduced to one intent, prompt quality cannot restore the discarded intent.

A model upgrade may improve:

- typo interpretation;
- explanation quality;
- RCA narrative structure;
- recognition of missing evidence.

It will not reliably repair:

- incorrect tool selection before generation;
- missing data collectors;
- absent multi-tool composition;
- inconsistent source metadata;
- lack of runtime evidence requirements;
- untested failure-state behavior.

## Why adopting MCP, LangChain or LangGraph alone will not solve it

These technologies address different concerns:

- MCP can standardize how tools are exposed and invoked.
- LangGraph can represent multi-step, stateful workflows.
- LangChain provides integrations and orchestration abstractions.

None of them defines the application's PostgreSQL semantics, evidence contracts, safety policy, source freshness or correct intent precedence. Recreating the same generic `log` tool and single-intent logic behind MCP or LangGraph would preserve the defect in a more elaborate architecture.

The framework decision belongs to the solution-design phase. The current diagnosis establishes that any future design must preserve deterministic factual tools while adding explicit planning, composition and validation.

## Current strengths that should not be lost

The existing system already has several good foundations:

- deterministic read-only tools for many common DBA questions;
- direct Patroni and `pg_stat_replication` topology evidence;
- separation of physical HA members from logical walsenders in the live-state response;
- audit metadata on assistant turns;
- provider/fallback metadata;
- namespace-scoped OpenShift access;
- strong platform mutation defaults;
- evidence persistence and log/RAG infrastructure;
- good results for alerts, backup status, CPU, HA topology, logical replication, logs, PITR, physical replication lag, roles and switchover wording in this corpus.

The problem is not that the application lacks useful tools. It is that tool coverage and orchestration are uneven, and the fallback path is asked to compensate for gaps it cannot safely fill.

## What the evaluation proves

The run provides direct evidence for these conclusions:

1. The assistant can answer many bounded factual questions accurately and quickly through deterministic paths.
2. Routing changes the answer more than model quality does for the observed critical defect.
3. The current architecture does not support general multi-intent composition.
4. Several operational domains lack an attached deterministic collector on the assistant path.
5. The model usually avoids inventing missing measurements, but irrelevant log context is still presented in unsupported domains.
6. Runtime response metadata is not yet expressive enough for partial, unavailable or insufficient-evidence outcomes.
7. Live testing alone cannot validate dependency failures.
8. The release gate correctly identifies the archive/lag defect and other category-wide gaps.

## What the evaluation does not prove

The run does not establish:

- that every one of the 168 failures is an application defect;
- that the language model is 66.4% accurate;
- that passing these 500 cases guarantees production correctness;
- that source-outage handling is broken in all 20 hypothetical cases;
- that a specific orchestration framework is required;
- that write-capable Agentic workflows are unsafe or were exercised;
- that the current live cluster itself is unhealthy.

No mutation endpoints were called. The evaluation exercised only the read-only assistant endpoint.

## Evidence and reproducibility

The following repository artifacts support this analysis:

- `evals/assistant_500.jsonl` — committed 500-case corpus;
- `evals/run_assistant_eval.py` — HTTP evaluator and release-gate logic;
- `evals/reports/assistant_eval_20260717.json` — complete machine-readable live results;
- `evals/reports/assistant_eval_20260717.html` — human-readable report;
- `tests/test_assistant_eval.py` — corpus and grader contract tests;
- `tests/test_assistant_corpus_contract.py` — validates that all 500 cases are machine gradable;
- `app/assistant_tools.py` — deterministic router and tools;
- `app/log_ai.py` — live-state and log/model fallback orchestration;
- `app/api_ops.py` — assistant API endpoint.

Local validation after adding the evaluation assets:

```text
80 passed, 4 deprecation warnings
```

The warnings concern FastAPI `on_event` deprecation and are unrelated to the assistant evaluation.

## Diagnostic conclusion

The current assistant is a collection of useful deterministic DBA tools followed by a log-oriented generative fallback. It is not yet a general evidence planner.

Its principal failure mechanism is:

```text
ambiguous or combined question
  -> first lexical match wins
  -> one tool returns early
  -> other required sources are never queried
  -> answer is internally consistent with the wrong or incomplete evidence
```

Its secondary failure mechanism is:

```text
unsupported operational question
  -> no deterministic collector matches
  -> log/RAG fallback receives irrelevant or incomplete evidence
  -> model refuses safely or produces a generic answer
  -> requested live operational fact remains unanswered
```

The next phase should use this diagnosis to define the solution architecture and acceptance contracts. That phase should decide how intent planning, typed tools, multi-source composition, evidence validation, safety responses, fault injection and framework choices will be implemented. This document intentionally stops before selecting or implementing that solution.
