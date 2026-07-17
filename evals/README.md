# DBA Assistant 500-case evaluation

This suite calls the real read-only `POST /api/v1/assistant/ask` pipeline and
grades routing, source attribution, required/forbidden claims, evidence,
auditing, mutation safety, fallback metadata, and latency.

Run a small smoke test first:

```bash
python3 evals/run_assistant_eval.py \
  --base-url https://OBJECT-MONITOR-ROUTE \
  --categories archive_and_lag,replication_lag,wal_archive \
  --workers 1
```

Run the release gate:

```bash
python3 evals/run_assistant_eval.py \
  --base-url https://OBJECT-MONITOR-ROUTE \
  --workers 4 --min-pass-rate 95 --allow-critical-failures 0
```

If authentication is required, place the bearer token in
`ASSISTANT_EVAL_TOKEN`; it is read from the environment and never written to
the report. Reports are created under `evals/reports/` as JSON and HTML. A
non-zero exit means the configured release gate failed.

The committed corpus is regenerated deterministically with:

```bash
python3 evals/generate_assistant_corpus.py
```

Live runs are deliberately opt-in because 500 cases query production-like UAT
evidence sources and may invoke the configured model. The runner only calls the
assistant read endpoint; it never calls action, approval, SQL-write, Patroni,
or Kubernetes mutation endpoints.
