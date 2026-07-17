#!/usr/bin/env python3
"""AI Ops module gap diagnostic — pinpoints exactly why the new v17 AI Ops
screens (ai_platform.js / ai_platform2.js / ai-ui.js) show only
"representative sample" placeholders on OpenShift, before any redeploy.

live_vs_seeded_endpoint_audit.py already proved (via HTTP + static-file scan)
that the frontend calls /api/v1/ai/agents, /api/v1/ai/audit,
/api/v1/ai/evidence-packs, /api/v1/ai/model-gateway/status,
/api/v1/ai/overview, /api/v1/ai/rag/kb, /api/v1/ai/recommendations, and none
of those exist as backend routes. What that tool CAN'T see from outside is
whether the backend already has the underlying capability under a different
name. Reading app/services/ai_agent_service.py, app/services/ai_provider.py,
app/ai/rag_retriever.py, and app/db/models.py shows it almost certainly does:

    frontend wants                     backend already has
    ---------------------------------  --------------------------------------
    /api/v1/ai/agents                  /api/ai-agent/runs (ai_agent_service.list_runs)
    /api/v1/ai/agents/run-now          /api/ai-agent/run  (ai_agent_service.run_agent)
    /api/v1/ai/recommendations         /api/ai-agent/recommendations (list_recommendations)
    /api/v1/ai/recommendations/{id}/reject  /api/ai-agent/recommendations/{id}/reject
    /api/v1/ai/audit                   AiActionAudit table + ai_agent_service._audit_api
    /api/v1/ai/evidence-packs/{id}     ai_dba_recommendation_evidence table
    /api/v1/ai/rag/kb                  ai_knowledge_base table + app/ai/rag_retriever.py
    /api/v1/ai/model-gateway/status    app/services/ai_provider.provider_status()
    /api/v1/ai/overview                (no direct equivalent — likely a new
                                         thin aggregator over the above)

This script runs INSIDE the live pod (via `oc exec`, same pattern as
tools/check_object_monitor_bizmon.py) to CONFIRM that against the actual
deployed code + database, rather than trusting a local source read:
  - the real registered FastAPI routes (from the running app object itself)
  - the real frontend /api/... references (read off disk at UI_ROOT)
  - the real gap between them (segment-aware path matching)
  - for each gap path: candidate near-matches among registered routes,
    SQLAlchemy table names, and public functions in the AI service modules
  - row counts for every ai_*/ml_* table (does data exist to serve?)
  - AI provider/config status (ANTHROPIC key present, agent status, seed
    fallback) via the live service functions, not just env var greps

Usage (run on the bastion, needs oc + a working `oc login`):
    python3 tools/ai_module_gap_diag.py --namespace monitoring --deployment object-monitor \
        --json-out ~/UAT_PATRONI/ai_module_gap_diag_$(date +%Y%m%d_%H%M%S).json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REMOTE_CHECK = r'''
import inspect
import json
import os
import re


def short_error(exc):
    return str(exc).splitlines()[0][:300]


def path_segments(path):
    return [s for s in path.strip("/").split("/") if s]


def normalize_shape(path):
    return re.sub(r"\{[^/}]+\}", "*", path)


def is_segment_prefix(a, b):
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer[: len(shorter)] == shorter


# ---- 1. real registered routes -- via the LIVE server's own /openapi.json --
# A first real run re-imported app.main fresh in this scratch process and got
# only 38 routes total (14 GET) when the live server clearly has 200 (proven
# separately by live_vs_seeded_endpoint_audit.py AND by curling
# http://127.0.0.1:8080/openapi.json from inside this same pod). Root cause:
# uvicorn's long-running worker builds app.routes ONCE at cold start with its
# own sys.modules cache; a brand-new `python3 -c` process here re-runs all 23
# `app.include_router(...)` calls from scratch and can observe some router
# modules mid-registration (their APIRoute objects fully populated a moment
# later, but app.include_router() had already copied an incomplete snapshot
# by the time it ran) -- an import-order artifact of re-importing app.main
# outside of its normal single cold-start context, not a real routing bug.
# Fix: ask the ALREADY-RUNNING server for its own route table over its own
# loopback HTTP port instead of re-triggering that import path here.
routes_error = None
registered = []
registered_methods = {}
app_main_file = None
app_package_file = None
router_module_route_counts = {}
all_routes_raw_count = None
UI_ROOT = None
try:
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:8080/openapi.json", timeout=15) as resp:
        _spec = json.loads(resp.read().decode())
    for _path, _methods in (_spec.get("paths") or {}).items():
        registered.append(_path)
        registered_methods[_path] = sorted(m.upper() for m in _methods.keys())
    registered = sorted(set(registered))
    all_routes_raw_count = len(_spec.get("paths") or {})
except Exception as exc:
    routes_error = short_error(exc)

# UI_ROOT is a pure `Path(__file__).parent.parent / "static"` computation in
# app/main.py -- unrelated to the router-registration artifact above, so a
# fresh import is fine for this specific value even though it isn't safe to
# trust for app.routes.
try:
    import app as _app_pkg
    app_package_file = getattr(_app_pkg, "__file__", None)
    import app.main as _main_mod
    app_main_file = getattr(_main_mod, "__file__", None)
    UI_ROOT = _main_mod.UI_ROOT
    for name in (
        "api_meta", "api_clusters", "api_perf", "api_backups", "api_security",
        "api_replication", "api_admin", "api_metrics", "api_ops", "api_actions",
        "api_logs", "api_log_analytics", "api_health_check", "api_rules",
        "api_ml", "api_forecast", "api_ai_incidents", "api_scheduler",
        "api_objects", "api_ai_actions", "api_ai_agent", "api_compat",
        "api_recommendations",
    ):
        try:
            mod = getattr(_main_mod, name)
            router_module_route_counts[name] = len(mod.router.routes)
        except Exception as exc:
            router_module_route_counts[name] = "error: %s" % short_error(exc)
except Exception as exc:
    # UI_ROOT import failed independently of the HTTP route lookup above;
    # frontend file scanning below will just report nothing found.
    pass

# ---- 2. real frontend /api/... references, read off disk -------------------
frontend_error = None
frontend_paths = set()
scanned_files = []
if UI_ROOT is not None:
    candidates = [
        UI_ROOT / "dist" / "ai_platform.js",
        UI_ROOT / "dist" / "ai_platform2.js",
        UI_ROOT / "dist" / "assistant.js",
        UI_ROOT / "dist" / "ai_ops.js",
        UI_ROOT / "ai-ui.js",
        UI_ROOT / "live-identity.js",
    ]
    pattern = re.compile(r"""["'`](/api/[a-zA-Z0-9_\-./{}]*)""")
    for p in candidates:
        if not p.is_file():
            continue
        scanned_files.append(str(p))
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            frontend_error = short_error(exc)
            continue
        for m in pattern.finditer(text):
            raw = m.group(1).split("?")[0].rstrip("/")
            if raw and raw != "/api":
                frontend_paths.add(raw)

# ---- 3. the actual gap, segment-aware ---------------------------------------
backend_segs = [path_segments(normalize_shape(p)) for p in registered]
gaps = []
for fp in sorted(frontend_paths):
    fseg = path_segments(normalize_shape(fp))
    matched = any(is_segment_prefix(fseg, bseg) for bseg in backend_segs)
    if not matched:
        gaps.append(fp)

# ---- 4. near-match candidates for each gap ----------------------------------
service_modules = {}
for modname in ("app.services.ai_agent_service", "app.services.ai_provider", "app.ai.rag_retriever"):
    try:
        mod = __import__(modname, fromlist=["_"])
        funcs = sorted(n for n, v in vars(mod).items() if inspect.isfunction(v) and not n.startswith("_"))
        service_modules[modname] = funcs
    except Exception as exc:
        service_modules[modname] = {"error": short_error(exc)}

table_names = []
tables_error = None
try:
    from app.db.models import Base
    table_names = sorted(Base.metadata.tables.keys())
except Exception as exc:
    tables_error = short_error(exc)

def norm_tokens(s):
    # token-overlap, not substring: "agents" must match "ai-agent" (hyphen +
    # singular/plural differ), "evidence-packs" must match
    # "ai_dba_recommendation_evidence" (only shares one of two words).
    # Naive `keyword in candidate` substring checks miss both of those.
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.lower())
    toks = set(t for t in s.split("_") if t)
    expanded = set()
    for t in toks:
        expanded.add(t)
        expanded.add(t[:-1] if (t.endswith("s") and len(t) > 3) else t + "s")
    return expanded


def token_overlap(keyword, candidate):
    return bool(norm_tokens(keyword) & norm_tokens(candidate))


near_matches = {}
for gap in gaps:
    keywords = [seg for seg in path_segments(gap) if seg not in ("api", "v1", "ai")]
    route_hits = [r for r in registered if any(token_overlap(k, r) for k in keywords)]
    table_hits = [t for t in table_names if any(token_overlap(k, t) for k in keywords)]
    func_hits = {}
    for modname, funcs in service_modules.items():
        if isinstance(funcs, dict):
            continue
        hits = [f for f in funcs if any(token_overlap(k, f) for k in keywords)]
        if hits:
            func_hits[modname] = hits
    near_matches[gap] = {"routes": route_hits, "tables": table_hits, "functions": func_hits}

# ---- 5. row counts for every ai_*/ml_* table --------------------------------
row_counts = {}
row_counts_error = None
try:
    from app.db.session import SessionLocal
    from sqlalchemy import text as sa_text
    ai_ml_tables = [t for t in table_names if t.startswith("ai_") or t.startswith("ml_")]
    with SessionLocal() as db:
        for t in ai_ml_tables:
            try:
                n = db.execute(sa_text(f'select count(*) from "{t}"')).scalar()
                row_counts[t] = n
            except Exception as exc:
                row_counts[t] = "error: %s" % short_error(exc)
except Exception as exc:
    row_counts_error = short_error(exc)

# ---- 6. live AI provider / agent / seed-fallback status ---------------------
provider_status = None
provider_status_error = None
try:
    from app.services import ai_provider
    provider_status = ai_provider.provider_status()
except Exception as exc:
    provider_status_error = short_error(exc)

agent_status = None
agent_status_error = None
try:
    from app.services import ai_agent_service
    agent_status = ai_agent_service.status()
except Exception as exc:
    agent_status_error = short_error(exc)

env_flags = {
    "PGC_LOCAL_SEED_FALLBACK": os.environ.get("PGC_LOCAL_SEED_FALLBACK"),
    "AI_PROVIDER": os.environ.get("AI_PROVIDER"),
    "AI_MODEL": os.environ.get("AI_MODEL"),
    "ANTHROPIC_API_KEY_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    "AI_SCHEDULER_ENABLED": os.environ.get("AI_SCHEDULER_ENABLED"),
    "AI_SCHEDULER_INTERVAL_SECONDS": os.environ.get("AI_SCHEDULER_INTERVAL_SECONDS"),
}

payload = {
    "routes_error": routes_error,
    "app_package_file": app_package_file,
    "app_main_file": app_main_file,
    "all_routes_raw_count": all_routes_raw_count,
    "router_module_route_counts": router_module_route_counts,
    "registered_route_count": len(registered),
    "registered_methods": registered_methods,
    "ai_related_registered_routes": sorted(r for r in registered if "ai" in r.lower() or "agent" in r.lower()),
    "frontend_error": frontend_error,
    "scanned_frontend_files": scanned_files,
    "frontend_ai_paths_referenced": sorted(p for p in frontend_paths if "/ai/" in p or p.endswith("/ai")),
    "gaps_frontend_only": gaps,
    "near_match_candidates": near_matches,
    "service_module_public_functions": service_modules,
    "tables_error": tables_error,
    "ai_ml_table_names": [t for t in table_names if t.startswith("ai_") or t.startswith("ml_")],
    "row_counts_error": row_counts_error,
    "ai_ml_table_row_counts": row_counts,
    "provider_status": provider_status,
    "provider_status_error": provider_status_error,
    "agent_status": agent_status,
    "agent_status_error": agent_status_error,
    "env_flags": env_flags,
}
print(json.dumps(payload, indent=2, sort_keys=True, default=str))
'''


def run(cmd: list[str], *, stdin: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)


def load_json_output(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if proc.returncode != 0:
        return {"error": "command failed", "returncode": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {"error": "invalid json from remote check: %s" % exc, "stdout": proc.stdout[-6000:], "stderr": proc.stderr[-4000:]}


def print_report(payload: dict[str, Any]) -> None:
    print("# AI Ops module gap diagnostic")
    print("collected_at=%s" % datetime.now(timezone.utc).isoformat())

    if payload.get("error"):
        print("\nERROR collecting diagnostic:", payload["error"])
        print(payload.get("stderr", ""))
        return

    # Surface every *_error field up front. These were being collected but
    # silently dropped by this report before -- if route/frontend/table
    # collection partially failed inside the pod, this is the only place
    # that shows why instead of just printing a suspiciously low count.
    error_fields = [
        ("routes_error", "collecting registered routes"),
        ("frontend_error", "reading frontend static files"),
        ("tables_error", "listing DB tables"),
        ("row_counts_error", "counting DB table rows"),
        ("provider_status_error", "calling ai_provider.provider_status()"),
        ("agent_status_error", "calling ai_agent_service.status()"),
    ]
    any_error = False
    for key, desc in error_fields:
        if payload.get(key):
            any_error = True
            print(f"\n!! ERROR {desc}: {payload[key]}")
    if any_error:
        print()

    print("\n## Import sanity")
    print("app package file:", payload.get("app_package_file"))
    print("app.main file:   ", payload.get("app_main_file"))
    print("total paths in live /openapi.json (ground truth, from the running server):", payload.get("all_routes_raw_count"))
    print("per-router-module route counts (router.routes on a FRESH re-import of app.main --")
    print("informational only, NOT used for gap detection: re-importing app.main in a scratch")
    print("process can under-count app.routes due to an import-order artifact -- see the big")
    print("comment above 'real registered routes' in this script's source for why):")
    for name, count in sorted((payload.get("router_module_route_counts") or {}).items()):
        print(f"  {name}: {count}")

    print("\n## Registered backend routes (from live /openapi.json, not a re-import)")
    print("total registered route paths:", payload.get("registered_route_count"))
    print("AI/agent-related routes already registered:")
    for r in payload.get("ai_related_registered_routes") or []:
        print("  -", r)

    print("\n## Frontend AI references (read from the live pod's static/)")
    print("scanned files:", payload.get("scanned_frontend_files"))
    print("paths referenced:")
    for p in payload.get("frontend_ai_paths_referenced") or []:
        print("  -", p)

    print("\n## Confirmed gap (frontend calls, no backend route matches)")
    gaps = payload.get("gaps_frontend_only") or []
    if not gaps:
        print("  none — every referenced AI path has a matching backend route.")
    for gap in gaps:
        info = (payload.get("near_match_candidates") or {}).get(gap, {})
        print(f"\n  {gap}")
        if info.get("routes"):
            print("    near-match registered routes:", info["routes"])
        if info.get("tables"):
            print("    near-match DB tables:", info["tables"])
        if info.get("functions"):
            for mod, funcs in info["functions"].items():
                print(f"    near-match functions in {mod}: {funcs}")
        if not (info.get("routes") or info.get("tables") or info.get("functions")):
            print("    no near-match found by keyword matching (which misses abbreviations like")
            print("    'kb' vs 'knowledge_base' or 'rag' vs 'retriever') — cross-check the raw")
            print("    ai_ml_table_row_counts and service_module_public_functions lists below by")
            print("    hand before concluding this one needs new backend logic.")

    print("\n## ai_*/ml_* table row counts (does data already exist?)")
    if payload.get("tables_error"):
        print("  tables_error:", payload["tables_error"])
    if payload.get("row_counts_error"):
        print("  row_counts_error:", payload["row_counts_error"])
    for t, n in sorted((payload.get("ai_ml_table_row_counts") or {}).items()):
        print(f"  {t}: {n}")

    print("\n## AI provider / agent status (live)")
    print("provider_status:", payload.get("provider_status") or payload.get("provider_status_error"))
    print("agent_status:", payload.get("agent_status") or payload.get("agent_status_error"))
    print("env_flags:", payload.get("env_flags"))

    print("\n## Verdict")
    if gaps:
        with_candidates = [
            g for g in gaps
            if (payload.get("near_match_candidates") or {}).get(g, {}).get("functions")
            or (payload.get("near_match_candidates") or {}).get(g, {}).get("tables")
        ]
        without_candidates = [g for g in gaps if g not in with_candidates]
        if with_candidates:
            print(f"  {len(with_candidates)}/{len(gaps)} gap path(s) have an existing service-layer")
            print("  function and/or DB table behind them already. Those look like a missing router")
            print("  registration under /api/v1/ai/*, not missing business logic — a thin new router")
            print("  calling the existing functions listed above would likely be enough:")
            for g in with_candidates:
                print(f"    - {g}")
        if without_candidates:
            print(f"  {len(without_candidates)}/{len(gaps)} gap path(s) found no automatic keyword match")
            print("  (this heuristic misses abbreviations, e.g. 'rag'/'kb' vs 'rag_retriever'/")
            print("  'knowledge_base'). Read the raw table/function lists by hand for these before")
            print("  assuming they need new logic:")
            for g in without_candidates:
                print(f"    - {g}")
    else:
        print("  No gap found on this live instance.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--namespace", "-n", default="monitoring")
    p.add_argument("--deployment", "-d", default="object-monitor")
    p.add_argument("--oc", default="oc")
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--json-out", help="Write the full collected JSON to this path.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    proc = run([args.oc, "exec", "-i", "deploy/%s" % args.deployment, "-n", args.namespace, "--", "python3", "-"], stdin=REMOTE_CHECK, timeout=args.timeout)
    payload = load_json_output(proc)

    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print_report(payload)
    return 1 if payload.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
