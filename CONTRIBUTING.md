# Contributing — Agentic Patroni Cluster / Object Monitor

Thank you for contributing. This document describes the shortest, safest path to run and develop the project locally, run the evaluation suite, render the architecture diagrams, and produce a distributable overview PDF.

## Local development (quickstart)

1. Clone and create a venv

```bash
git clone https://github.com/printesh99/Agentic-Patroni-cluster.git
cd Agentic-Patroni-cluster
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

2. Static checks / compile & tests

```bash
# catch syntax errors
python3 -m compileall app
# run the test suite (offline unit/async tests)
pytest -q
```

3. Run the app locally (development)

Notes: many live sources (oc, Prometheus, Loki, monitored PostgreSQL) will fail unless you configure connections or run against a test cluster. The metadata database must be reachable for full startup.

```bash
# recommended development envs (examples)
export PGHOST=localhost
export PGPORT=5432
export PGUSER=object_monitor
export PGPASSWORD=secret
export PGDATABASE=object_monitor
# safety flags (defaults prevent destructive actions)
export AGENTIC_WORKFLOW_ENABLED=false
export AGENTIC_MODE=SHADOW
export PGC_ALLOW_MUTATIONS=0

# run uvicorn
uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
```

4. Running in Docker for local testing

```bash
docker build -t object-monitor:dev .
docker run --rm -p 8080:8080 \
  -e PGHOST=... -e PGUSER=... -e PGPASSWORD=... -e PGDATABASE=... \
  -e AGENTIC_MODE=SHADOW object-monitor:dev
```

5. OpenShift build / deployment

- The repository is intended for OpenShift binary builds. Use your OpenShift tools (oc) to create a BuildConfig and perform a binary build, or push an image to your registry and create Deployment/Service/Route objects as described in docs/CURRENT_END_TO_END_ARCHITECTURE.md.

## Running the assistant evaluation suite (evals)

The evals directory contains a suite that hits the read-only assistant endpoint and grades routing, evidence attribution and other properties. These are opt-in and may query UAT-like production sources.

Run a small smoke test first (examples from evals/README.md):

```bash
python3 evals/run_assistant_eval.py \
  --base-url https://OBJECT-MONITOR-ROUTE \
  --categories archive_and_lag,replication_lag,wal_archive \
  --workers 1
```

For a full release gate (opt-in):

```bash
ASSISTANT_EVAL_TOKEN="<bearer token>" python3 evals/run_assistant_eval.py \
  --base-url https://OBJECT-MONITOR-ROUTE --workers 4 --min-pass-rate 95 --allow-critical-failures 0
```

Notes:
- The runner only calls the assistant read endpoint. It never executes actions, SQL writes, Patroni or Kubernetes mutations.
- Provide ASSISTANT_EVAL_TOKEN in the environment if the route requires authentication.

## Safety: essential runtime flags

The application is deliberately conservative by default. Ensure these remain set for non-production testing unless you explicitly intend to enable operations:

- AGENTIC_WORKFLOW_ENABLED=false
- MCP_DIAGNOSTICS_ENABLED=false
- MCP_OPERATIONS_ENABLED=false
- AI_ACTION_EXECUTION_ENABLED=false
- EMERGENCY_FAILOVER_ENABLED=false
- AGENTIC_MODE=SHADOW
- PGC_ALLOW_MUTATIONS=0
- TRUSTED_IDENTITY_HEADERS=false

## Rendering mermaid diagrams and creating a PDF overview

The docs/COMPLETE_PROJECT_OVERVIEW.md and docs/CURRENT_END_TO_END_ARCHITECTURE.md contain mermaid diagram blocks. A few options to render and produce a PDF:

Option A — Quick manual preview
- Use mermaid.live (https://mermaid.live/) or a VS Code plugin (Mermaid Preview) to paste/preview the mermaid blocks, then export PNG/SVG.

Option B — mermaid-cli (generate PNG/SVG from .mmd files)

1. Install mermaid-cli (requires Node.js):

```bash
npm install -g @mermaid-js/mermaid-cli
```

2. Extract a mermaid block into a file, e.g. pipeline.mmd (wrap the flowchart in a single file) and render:

```bash
mmdc -i pipeline.mmd -o pipeline.png
mmdc -i pipeline.mmd -o pipeline.svg
```

Option C — Produce a PDF of the markdown with rendered diagrams locally

One reliable path is to render mermaid diagrams to images (Option B) and then include those images in a single markdown file before producing a PDF with pandoc/wkhtmltopdf:

```bash
# render diagrams to images (mmdc)
# then convert markdown -> PDF with pandoc (requires wkhtmltopdf or a TeX engine)
pandoc docs/COMPLETE_PROJECT_OVERVIEW.md -o docs/Agentic-Patroni-Complete-Overview.pdf --pdf-engine=wkhtmltopdf
```

If you prefer an automated workflow, we can add a GitHub Actions workflow that:
- extracts mermaid blocks, renders them with mermaid-cli, embeds images into a markdown-to-pdf pipeline and commits a PDF to docs/ or uploads it as a release artifact. Tell me if you want that and I will add the workflow.

## How to contribute code

- Fork the repo, create a topic branch per change (feature/fix/infra). Keep PRs small and focused.
- Add tests for new features or bug fixes. Run pytest locally.
- When editing public docs with diagrams, add the mermaid source and a rendered image under docs/assets/ for readers without mermaid renderers.

## Developer checklist for PRs (recommended)

- [ ] All new code is covered by unit tests or integration tests where appropriate
- [ ] No secrets or credentials are committed; use Kubernetes Secrets for deployment
- [ ] Run python3 -m compileall app and pytest -q locally
- [ ] Update docs/CURRENT_END_TO_END_ARCHITECTURE.md or docs/COMPLETE_PROJECT_OVERVIEW.md when architecture changes

## Request automated outputs

If you want, I can:
- Add a GitHub Actions workflow that renders mermaid diagrams and commits docs/Agentic-Patroni-Complete-Overview.pdf to the repository on merge to main.
- Generate PNG/SVG exports of the included mermaid diagrams and commit them to docs/assets/.

Tell me which of the two automation tasks you'd like and I'll add it.
