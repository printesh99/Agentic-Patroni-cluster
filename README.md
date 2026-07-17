# Agentic Patroni Cluster / Object Monitor

An OpenShift-native PostgreSQL 18 monitoring, historical performance, AI/RAG, immutable evidence, incident/RCA and guarded Agentic operations console for Crunchy PGO and Patroni clusters.

The current UAT rollout stage is **SHADOW**. Monitoring and analysis are live; Agentic PostgreSQL, Patroni, shell, `oc` and Kubernetes mutations are disabled.

## Architecture

See [Current End-to-End Architecture](docs/CURRENT_END_TO_END_ARCHITECTURE.md) for the complete image, frontend, backend, metadata database, collector, ML, RAG, pg_profile, evidence, security and deployment pipelines.

## Validation

```bash
python3 -m compileall app
pytest -q
```

Current suite: 73 tests.

## Container

The Dockerfile builds a UBI 9 / Python 3.12 image containing:

- FastAPI backend and static React/ECharts frontend;
- OpenShift `oc` client for namespace-scoped live reads;
- PostgreSQL, Patroni, Prometheus and Loki collectors;
- SQLAlchemy metadata models and Alembic migrations;
- ML anomaly/forecasting and evidence-cited RCA services;
- an offline FastEmbed model cache for air-gapped RAG.

## Safety defaults

```text
AGENTIC_WORKFLOW_ENABLED=false
MCP_DIAGNOSTICS_ENABLED=false
MCP_OPERATIONS_ENABLED=false
AI_ACTION_EXECUTION_ENABLED=false
EMERGENCY_FAILOVER_ENABLED=false
AGENTIC_MODE=SHADOW
PGC_ALLOW_MUTATIONS=0
TRUSTED_IDENTITY_HEADERS=false
```

Do not enable mutation or trusted-header flags without completing the security, approval, identity and recovery-readiness gates documented under `docs/`.
