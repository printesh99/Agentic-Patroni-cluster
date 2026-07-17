#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_uat_evidence_collection.sh
#
# Ready-to-run wrapper for uat_ai_evidence_extractor.py
# Pre-filled with UAT UAE cluster defaults.
#
# Run this on any machine that:
#   • Has Python 3.8+
#   • Has `oc` CLI installed and is logged in to the UAT OpenShift cluster
#   • Can reach the UAT cluster API (even read-only access is enough)
#
# Usage:
#   bash run_uat_evidence_collection.sh
#
#   Override oc context if needed:
#   OC_CONTEXT="uat-pgcluster-uae/api-ocp-uat-habibbank-local:6443/mohsinali" \
#     bash run_uat_evidence_collection.sh
#
#   Skip Prometheus section (if not reachable from operator box):
#   SKIP_SECTIONS=11 bash run_uat_evidence_collection.sh
#
#   Dry-run (print commands only, no actual execution):
#   DRY_RUN=1 bash run_uat_evidence_collection.sh
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail

# ── UAT Cluster Settings (pre-filled) ────────────────────────────────────────
NAMESPACE="${NAMESPACE:-uat-pgcluster-uae}"
CLUSTER="${CLUSTER:-uat-pgcluster-uae}"
CLUSTER_ID="${CLUSTER_ID:-uat}"
PATRONI_CLUSTER="${PATRONI_CLUSTER:-uat-pgcluster-uae-ha}"
ENVIRONMENT="${ENVIRONMENT:-uat}"
PG_PORT="${PG_PORT:-5555}"
PGBOUNCER_PORT="${PGBOUNCER_PORT:-5432}"
PGBACKREST_STANZA="${PGBACKREST_STANZA:-db}"
LOG_LINES="${LOG_LINES:-300}"

# ── OC Context ───────────────────────────────────────────────────────────────
# Leave blank to use whichever context `oc` is currently logged in to.
# If you need to switch:  oc login https://api-ocp-uat-habibbank-local:6443
# Then run this script.
OC_CONTEXT="${OC_CONTEXT:-}"

# ── Prometheus URL (optional) ─────────────────────────────────────────────────
# If Prometheus is not reachable from your machine, leave blank — section 11
# will be skipped gracefully. You can still access it via the Grafana UI.
# Typical UAT internal URL (only works from inside the cluster or via oc proxy):
#   http://uat-pgo18-prometheus.uat-pgcluster-uae.svc:9090
# If you're running `oc port-forward` to reach it, set this to localhost:
#   PROMETHEUS_URL="http://localhost:9090" bash run_uat_evidence_collection.sh
PROMETHEUS_URL="${PROMETHEUS_URL:-}"

# ── Output dir ────────────────────────────────────────────────────────────────
OUT_DIR="${OUT_DIR:-${HOME}/uat_evidence_$(date -u +%Y%m%dT%H%M%SZ)}"

# ── Optional section skip ─────────────────────────────────────────────────────
SKIP_SECTIONS="${SKIP_SECTIONS:-}"    # e.g. "11" to skip Prometheus, "11,12" for both

# ── Dry run ───────────────────────────────────────────────────────────────────
DRY_RUN="${DRY_RUN:-0}"

# ── Script path ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTRACTOR="${SCRIPT_DIR}/uat_ai_evidence_extractor.py"

# ─────────────────────────────────────────────────────────────────────────────
log() { printf '\n[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }

log "UAT Evidence Collection — starting"
log "Namespace : ${NAMESPACE}"
log "Cluster   : ${CLUSTER}"
log "PG port   : ${PG_PORT}"
log "Output    : ${OUT_DIR}"
echo ""

# ── Pre-flight checks ────────────────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install Python 3.8+ and retry." >&2
  exit 1
fi

if ! command -v oc &>/dev/null; then
  echo "ERROR: oc CLI not found. Install OpenShift CLI and retry." >&2
  exit 1
fi

if [[ ! -f "${EXTRACTOR}" ]]; then
  echo "ERROR: Extractor not found at ${EXTRACTOR}" >&2
  exit 1
fi

# Check oc is logged in
if [[ -n "${OC_CONTEXT}" ]]; then
  oc config use-context "${OC_CONTEXT}" 2>/dev/null || {
    echo "WARNING: Could not switch to context '${OC_CONTEXT}'" >&2
    echo "         Proceeding with current context." >&2
  }
fi

CURRENT_CTX=$(oc config current-context 2>/dev/null || echo "unknown")
log "oc context : ${CURRENT_CTX}"

# Quick connectivity check
log "Checking cluster connectivity..."
if oc get namespace "${NAMESPACE}" &>/dev/null; then
  log "Namespace ${NAMESPACE} — reachable"
else
  echo ""
  echo "WARNING: Cannot reach namespace '${NAMESPACE}'."
  echo "         Make sure you are logged in:  oc login <api-url>"
  echo "         And the namespace exists:      oc get namespace ${NAMESPACE}"
  echo ""
  echo "Proceeding anyway (extractor handles errors per-section)..."
fi

# ── Build command ─────────────────────────────────────────────────────────────
CMD=(
  python3 "${EXTRACTOR}"
  --namespace    "${NAMESPACE}"
  --cluster      "${CLUSTER}"
  --cluster-id   "${CLUSTER_ID}"
  --patroni-cluster "${PATRONI_CLUSTER}"
  --environment  "${ENVIRONMENT}"
  --pg-port      "${PG_PORT}"
  --pgbouncer-port "${PGBOUNCER_PORT}"
  --pgbackrest-stanza "${PGBACKREST_STANZA}"
  --log-lines    "${LOG_LINES}"
  --out-dir      "${OUT_DIR}"
)

[[ -n "${OC_CONTEXT}" ]]     && CMD+=(--context "${OC_CONTEXT}")
[[ -n "${PROMETHEUS_URL}" ]] && CMD+=(--prometheus-url "${PROMETHEUS_URL}")
[[ -n "${SKIP_SECTIONS}" ]]  && CMD+=(--skip-sections "${SKIP_SECTIONS}")
[[ "${DRY_RUN}" == "1" ]]    && CMD+=(--dry-run)

# ── Show exact command being run ─────────────────────────────────────────────
log "Exact command:"
echo ""
echo "  ${CMD[*]}"
echo ""

# ── Execute ───────────────────────────────────────────────────────────────────
log "Starting collection (this takes 2-5 minutes)..."
"${CMD[@]}"
EXIT_CODE=$?

echo ""
if [[ ${EXIT_CODE} -eq 0 ]]; then
  ARCHIVE="${OUT_DIR}.tar.gz"
  BUNDLE="${OUT_DIR}/evidence_bundle.json"
  SIZE=$(du -sh "${OUT_DIR}" 2>/dev/null | cut -f1 || echo "?")

  log "Collection COMPLETE"
  echo ""
  echo "  Output folder : ${OUT_DIR}  (${SIZE})"
  echo "  Archive       : ${ARCHIVE}"
  echo "  Bundle JSON   : ${BUNDLE}"
  echo ""
  echo "─────────────────────────────────────────────────────────────"
  echo "  NEXT STEPS"
  echo "─────────────────────────────────────────────────────────────"
  echo ""
  echo "  1. Transfer the archive to the AI console machine:"
  echo "     scp ${ARCHIVE} <console-host>:~/"
  echo ""
  echo "  2. Share the bundle JSON file with the team or upload to"
  echo "     the AI console for evidence-based analysis."
  echo ""
  echo "  3. Send the bundle file to Printesh for AI assistant"
  echo "     integration (evidence_bundle.json is the key file)."
  echo ""
else
  echo "ERROR: Extractor exited with code ${EXIT_CODE}" >&2
  echo "       Check the output above for details." >&2
  exit ${EXIT_CODE}
fi
