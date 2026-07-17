# AI Assistant Next Implementation Marker

Date: 2026-07-17

## Status

**ACCEPTED FOR NEXT IMPLEMENTATION**

The problem analysis in
`docs/AI_ASSISTANT_CURRENT_SETUP_PROBLEM_ANALYSIS_20260717.md` is the approved
problem statement and evidence baseline for the next AI Assistant implementation
phase.

The next phase must address both execution paths:

1. deterministic factual routing for exact current-state answers;
2. multi-source RCA orchestration for incident investigations.

## Required implementation scope

- Multi-intent detection and tool composition.
- Typed intent, tool and evidence contracts.
- Authoritative source selection and freshness validation.
- Missing collectors identified by the 500-case evaluation.
- Chronological RCA evidence correlation.
- Explicit separation of facts, hypotheses and missing evidence.
- Prompt-injection and read-only safety responses.
- Deterministic dependency-failure and partial-source tests.
- Golden-case recalibration only after evidence-based adjudication.
- A release gate requiring zero critical failures.

## Completion gate

The implementation is not complete merely when routing code changes. Completion
requires:

- the reviewed acceptance corpus passes;
- critical failures equal zero;
- controlled source-failure tests pass;
- RCA timeline and evidence-attribution tests pass;
- prompt-injection and read-only safety tests pass;
- no unsupported factual claims are produced;
- results are retained as version-controlled evidence.

## Scope boundary

This marker authorizes planning and implementation against the documented
problem. It does not authorize production mutations, PostgreSQL changes,
Patroni failovers, Kubernetes mutations or deployment without the normal
reviewed change process.
