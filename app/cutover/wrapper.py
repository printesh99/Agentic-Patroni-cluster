"""Render the per-region wrapper script for the vendored cutover engine.

This is the ONLY adapted artifact in a run workspace (the engine and
orchestrator are byte-identical vendored copies). It mirrors the UK wrapper
(`prod_dr_cutover_uk.sh`) structure exactly: inject region flags for
context-check/dry-run/precheck/generate, and for execute refuse a manifest
whose prod_cluster does not match this region — fail closed, never silently
operate on another region.

The output filename must stay `prod_dr_cutover_uk.sh` because the vendored
orchestrator hardcodes that name next to itself.
"""
from __future__ import annotations

import shlex
from typing import Any

from app.cutover.config import CONFIG_FLAG_MAP

WRAPPER_HEADER = """#!/usr/bin/env bash
#
# prod_dr_cutover_uk.sh — rendered by the monitoring console cutover module.
# REGION = {region_id} (console_cutover_configs/{region_id})
#
# Mirrors the live-tested UK wrapper: injects region overrides so a run can
# never silently target another region, and refuses to `execute` a manifest
# that was not generated for this region. Do not edit; re-rendered per run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PY_TOOL="${{SCRIPT_DIR}}/prod_dr_cutover.py"

if [[ ! -f "${{PY_TOOL}}" ]]; then
  echo "ERROR: cannot find prod_dr_cutover.py next to this wrapper (${{PY_TOOL}})" >&2
  exit 2
fi

REGION_PROD_CLUSTER={prod_cluster_quoted}

REGION_FLAGS=(
{flag_lines}
)
"""

WRAPPER_BODY = """
if [[ $# -lt 1 ]]; then
  echo "Usage: $(basename "$0") <context-check|dry-run|precheck|generate|execute> [args...]" >&2
  exit 2
fi

SUBCMD="$1"; shift

echo "############################################################" >&2
echo "# console cutover wrapper  ->  REGION = {region_id}"           >&2
echo "#   prod cluster: ${{REGION_PROD_CLUSTER}}"                     >&2
echo "#   subcommand : ${{SUBCMD}}"                                   >&2
echo "############################################################" >&2

case "${{SUBCMD}}" in
  execute)
    # The execute subparser does NOT accept config flags; region config lives
    # in the manifest. Guard against executing a foreign-region manifest.
    manifest=""
    args=("$@")
    for ((i=0; i<${{#args[@]}}; i++)); do
      if [[ "${{args[$i]}}" == "--manifest" ]]; then
        manifest="${{args[$((i+1))]:-}}"
        break
      elif [[ "${{args[$i]}}" == --manifest=* ]]; then
        manifest="${{args[$i]#--manifest=}}"
        break
      fi
    done
    if [[ -z "${{manifest}}" ]]; then
      echo "ERROR: execute requires --manifest <path>." >&2
      exit 2
    fi
    if [[ ! -f "${{manifest}}" ]]; then
      echo "ERROR: manifest not found: ${{manifest}}" >&2
      exit 2
    fi
    if ! grep -q "\\"prod_cluster\\": \\"${{REGION_PROD_CLUSTER}}\\"" "${{manifest}}"; then
      echo "ERROR: ${{manifest}} is not a {region_id} manifest (prod_cluster != ${{REGION_PROD_CLUSTER}})." >&2
      echo "       Refusing to execute. Generate a {region_id} manifest with this wrapper first." >&2
      exit 2
    fi
    exec python3 "${{PY_TOOL}}" execute "$@"
    ;;
  context-check|dry-run|precheck|generate)
    exec python3 "${{PY_TOOL}}" "${{SUBCMD}}" "${{REGION_FLAGS[@]}}" "$@"
    ;;
  *)
    echo "ERROR: unknown subcommand '${{SUBCMD}}'." >&2
    echo "       Expected one of: context-check dry-run precheck generate execute" >&2
    exit 2
    ;;
esac
"""


def render_wrapper(region_id: str, config: dict[str, Any]) -> str:
    """Render the region wrapper script from a console_cutover_configs row."""
    prod_cluster = str(config.get("prod_cluster") or "").strip()
    if not prod_cluster:
        raise ValueError("region config has no prod_cluster; cannot render wrapper")

    flag_lines: list[str] = []
    for key, flag in CONFIG_FLAG_MAP.items():
        value = config.get(key)
        if value is None:
            continue
        value = str(value)
        # Engine flags accept empty strings (the UK wrapper passes empty
        # contexts in single-context mode), so only skip absent keys.
        flag_lines.append(f"  {flag} {shlex.quote(value)}")
    if config.get("single_context_projects"):
        flag_lines.insert(0, "  --single-context-projects")

    header = WRAPPER_HEADER.format(
        region_id=region_id,
        prod_cluster_quoted=shlex.quote(prod_cluster),
        flag_lines="\n".join(flag_lines),
    )
    body = WRAPPER_BODY.format(region_id=region_id)
    return header + body
