#!/usr/bin/env bash
set -euo pipefail

: "${ASSISTANT_BASE_URL:?Set ASSISTANT_BASE_URL to the console origin}"
python3 evals/run_assistant_eval.py \
  --base-url "${ASSISTANT_BASE_URL}" \
  --workers "${ASSISTANT_EVAL_WORKERS:-4}" \
  --min-pass-rate "${ASSISTANT_MIN_PASS_RATE:-95}" \
  --allow-critical-failures "${ASSISTANT_ALLOWED_CRITICAL_FAILURES:-0}"
