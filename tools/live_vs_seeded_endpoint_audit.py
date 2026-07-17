#!/usr/bin/env python3
"""Audit every registered API endpoint and classify it as LIVE, SEED_SUSPECT,
NO_DATA, LIVE_UNCONFIRMED, SKIPPED, or ERROR.

Unlike tools/endpoint_sweep.py (a fixed 41-path smoke test), this discovers
the *complete* route table from the running instance's own OpenAPI schema
(GET /openapi.json), so it stays correct as routers/endpoints are added or
removed. It then classifies each GET endpoint's response by:

  - "source" field(s) found anywhere in the JSON body (this app already
    tags most live responses, e.g. "pg_stat_activity", "pg_database",
    "Patroni + pg_stat_replication", "prometheus", "loki", "pgbackrest").
  - literal fixture values baked into tools/local_appmon_bizmon_seed.sql
    (e.g. "uat_core_logical_slot", "sub_gateway_to_fraud"). A hit here is
    a hard signal that PGC_LOCAL_SEED_FALLBACK data leaked into a response
    that should be live.
  - whether the payload actually carries any rows/series/etc, or is an
    honest "available": false / empty-list response (NO_DATA is not a bug;
    SEED_SUSPECT and LIVE_UNCONFIRMED are what need a human look).

Usage:
    python tools/live_vs_seeded_endpoint_audit.py --base-url http://127.0.0.1:8080
    python tools/live_vs_seeded_endpoint_audit.py --base-url https://<route> \
        --namespace uat-pgcluster-uae --deployment uat-pg-object-monitor

If --namespace/--deployment are given and `oc` is on PATH, the script also
prints whether PGC_LOCAL_SEED_FALLBACK is set on the live Deployment - the
single kill-switch that, if "true" on a target namespace, means every
seed-gated panel is intentionally allowed to fall back to fixture data.

Frontend-only checks (--static-dir <path to static/>):

The backend sweep above can only see servers that exist. It is blind to a
different failure mode found in the v17 "web UI only" bundle: client-side
JS that calls a path the backend has never registered, catches the failure,
and silently renders a hardcoded sample with no clear error - e.g.
static/dist/ai_platform.js's `useApi(path, sample)` hook falls back to a
baked-in `sample` object whenever the fetch fails, marking it only with a
small "representative sample" pill. Because the backend never receives a
request that "fails" in a way the HTTP sweep flags, that gap is invisible
to endpoint classification alone. --static-dir adds two checks instead:

  - FRONTEND_ONLY: an `/api/...` path referenced in static JS/JSX that has
    no matching route in the live /openapi.json at all (the exact ai_platform
    v17 situation: /api/v1/ai/* is called by the UI but was never added to
    any FastAPI router).
  - frontend seed/mock keyword hits: lines matching phrases like "representative
    sample", "mock data", "shipped uat mock", "seeded entry" etc, each a sign
    that static JS ships its own fallback fixture independent of the backend.
"""
from __future__ import annotations

import argparse
import json
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Set by main() when --insecure is passed, for OpenShift routes on a
# self-signed/internal-CA edge cert (same case curl -sk covers elsewhere
# in this repo's redeploy scripts).
_SSL_CONTEXT: ssl.SSLContext | None = None

# Distinctive literal values that only exist in tools/local_appmon_bizmon_seed.sql.
# A response containing any of these on a real target is proof the seed
# fallback fired, regardless of what its "source" field claims.
SEED_LITERALS = [
    "uat_core_logical_slot", "uat_docs_archive_slot", "uat_gateway_physical_slot",
    "sub_core_to_reporting", "sub_docs_to_archive", "sub_gateway_to_fraud",
    "pub_api_events", "pub_documents",
    "core-apply-worker", "docs-archive-sender", "gateway-fraud-apply",
    "uat_core_banking", "uat_customer", "uat_etl", "uat_gateway",
    "uat_mobile", "uat_locker", "uat_documents", "uat_payments", "uat_cards",
]

# Substrings in a "source" value that mean "not really live PostgreSQL/Patroni/
# Prometheus/Loki/pgbackrest/kubernetes data", even if no seed literal matched.
SUSPECT_SOURCE_SUBSTRINGS = [
    "seed", "mock", "sample", "demo", "static", "hardcoded", "placeholder",
    "local-empty", "fallback", "fixture", "stub", "dummy", "test data",
]

# Path-parameter name -> value used to fill in templated OpenAPI paths.
# Overridden at runtime for cluster_id/database once real values are discovered.
DEFAULT_PATH_PARAMS: dict[str, str] = {
    "cluster_id": "uat", "cluster_name": "uat",
    "database": "postgres", "schema": "public",
    "domain": "tps", "panel": "business_customers",
    "action": "status", "framework": "operational",
    "config_id": "1", "job_id": "1", "incident_id": "1", "model_id": "1",
    "snapshot_id": "1", "recommendation_id": "1", "queryid": "1", "sid": "1",
    "rb_id": "1", "ts_ns": "1", "pid": "1",
}

# Query-parameter name -> value, applied whenever the discovered route accepts it.
DEFAULT_QUERY_PARAMS: dict[str, str] = {
    "range": "1h", "limit": "5", "metric": "connections", "domain": "tps",
    "step": "60s",
}

SKIP_PATH_PREFIXES = ("/openapi.json", "/docs", "/redoc", "/static")

# Phrases in static JS/JSX that indicate a client-side fixture/fallback
# independent of anything the backend returns. Multi-word phrases first to
# keep false positives down ("placeholder" alone is a common HTML attribute).
FRONTEND_SEED_PHRASES = [
    "representative sample", "fall back to sample", "falls back to sample",
    "fallback to sample", "shipped uat mock", "mock shell", "mock data",
    "sample data", "seeded entry", "dummy data", "fake data", "local mock",
    "override the mock", "seed the real",
]
FRONTEND_SEED_WORDS = ["mock", "seed"]
FRONTEND_SCAN_EXTENSIONS = (".js", ".jsx")
FRONTEND_SKIP_DIR_MARKERS = ("/vendor/", "/node_modules/", "/assets/")
AI_FRONTEND_BLOCKING_FILES = ("ai_platform", "ai_ops", "ai_agent", "ai-ui")
AI_FRONTEND_BLOCKING_PHRASES = {
    "representative sample", "fall back to sample", "falls back to sample",
    "fallback to sample", "mock data", "sample data", "dummy data",
    "fake data", "local mock",
}


def scan_frontend_seed_hints(static_dir: Path, max_hits_per_file: int = 8) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in sorted(static_dir.rglob("*")):
        if not path.is_file() or path.suffix not in FRONTEND_SCAN_EXTENSIONS:
            continue
        posix = path.as_posix()
        if any(marker in posix + "/" for marker in FRONTEND_SKIP_DIR_MARKERS):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        file_hits = 0
        for lineno, line in enumerate(lines, start=1):
            low = line.lower()
            matched = next((p for p in FRONTEND_SEED_PHRASES if p in low), None)
            if not matched:
                matched = next((w for w in FRONTEND_SEED_WORDS if w in low.split()), None)
                if not matched:
                    import re as _re
                    matched = next((w for w in FRONTEND_SEED_WORDS if _re.search(r"\b%s\b" % w, low)), None)
            if matched and file_hits < max_hits_per_file:
                hits.append({
                    "file": str(path.relative_to(static_dir.parent)) if static_dir.parent in path.parents else posix,
                    "line": lineno,
                    "matched": matched,
                    "text": line.strip()[:160],
                })
                file_hits += 1
    return hits


def blocking_ai_frontend_seed_hints(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return frontend fixture hints that should fail an AI-module deploy gate.

    The broader hint list intentionally reports legacy chart helpers too, but
    v22's outage class was AI-facing code rendering client-side data when a
    live endpoint failed. Keep this gate scoped to AI module files and strong
    fallback phrases so unrelated comments do not fail a release.
    """
    blocked: list[dict[str, Any]] = []
    for hit in hits:
        file_name = str(hit.get("file") or "").lower()
        matched = str(hit.get("matched") or "").lower()
        if any(marker in file_name for marker in AI_FRONTEND_BLOCKING_FILES) and matched in AI_FRONTEND_BLOCKING_PHRASES:
            blocked.append(hit)
    return blocked


def scan_frontend_api_paths(static_dir: Path) -> set[str]:
    import re as _re
    pattern = _re.compile(r"""["'`](/api/[a-zA-Z0-9_\-./{}]*)""")
    found: set[str] = set()
    for path in sorted(static_dir.rglob("*")):
        if not path.is_file() or path.suffix not in FRONTEND_SCAN_EXTENSIONS:
            continue
        posix = path.as_posix()
        if any(marker in posix + "/" for marker in FRONTEND_SKIP_DIR_MARKERS):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in pattern.finditer(text):
            raw = m.group(1).split("?")[0].rstrip("/")
            if raw and raw != "/api":
                found.add(raw)
    return found


def find_orphaned_scripts(static_dir: Path) -> list[str]:
    """Files under static/**.js that exist on disk but have no <script src=...>
    reference in index.html, so they never execute in the browser at all.

    This is a real, distinct failure mode from anything else in this file:
    the backend can be 100% correct and the file can be byte-present in the
    deployed image, and the screen it implements will still render as a
    silent blank (no error, no seed fallback, nothing) because the component
    it defines was simply never loaded. Caught live on 2026-07-06: static/dist/
    ai_ops.js and static/dist/health_grid.js existed in the v17 bundle but
    had no <script> tag in index.html, so "Tuning Advisor" and "Cluster
    Health Grid" rendered as an empty page with zero console errors.
    """
    import re as _re
    index_path = static_dir / "index.html"
    if not index_path.is_file():
        return []
    html = index_path.read_text(encoding="utf-8", errors="replace")
    referenced = set()
    for m in _re.finditer(r"""<script[^>]+src=["']([^"']+)["']""", html):
        src = m.group(1).split("?")[0].lstrip("./").lstrip("/")  # strip cache-bust ?v=...
        referenced.add(src)
        referenced.add(Path(src).name)  # tolerate path-prefix differences

    orphaned = []
    for path in sorted(static_dir.rglob("*.js")):
        if any(marker in path.as_posix() + "/" for marker in FRONTEND_SKIP_DIR_MARKERS):
            continue
        rel = path.relative_to(static_dir).as_posix()
        if rel not in referenced and path.name not in referenced:
            orphaned.append(rel)
    return orphaned


def normalize_shape(path: str) -> str:
    import re as _re
    return _re.sub(r"\{[^/}]+\}", "*", path)


def path_segments(path: str) -> list[str]:
    return [s for s in path.strip("/").split("/") if s]


def is_segment_prefix(a: list[str], b: list[str]) -> bool:
    """True if the shorter of a/b is a non-empty, segment-wise prefix of the
    longer one. Segment-wise (not raw string prefix) so a short backend path
    like "/" or "/api" can't swallow every longer frontend path just because
    every path starts with "/" — see the bug this replaced: a bare "/" route
    (main.py's index handler) made str.startswith() match ALL frontend paths."""
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer[: len(shorter)] == shorter


def frontend_backend_gaps(frontend_paths: set[str], backend_paths: list[str]) -> list[str]:
    backend_segs = [path_segments(normalize_shape(p)) for p in backend_paths]
    gaps = []
    for fp in sorted(frontend_paths):
        fseg = path_segments(normalize_shape(fp))
        matched = any(is_segment_prefix(fseg, bseg) for bseg in backend_segs)
        if not matched:
            gaps.append(fp)
    return gaps


def fetch(base_url: str, path: str, timeout: int) -> tuple[int, Any, str | None]:
    req = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
            text = resp.read().decode(errors="replace")
            try:
                return resp.status, json.loads(text), None
            except json.JSONDecodeError:
                return resp.status, None, "non-JSON body: %s" % text[:200]
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        return exc.code, None, text[:300]
    except Exception as exc:
        return 0, None, str(exc)


def collect_sources(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        val = node.get("source")
        if isinstance(val, str) and val.strip():
            found.add(val)
        for v in node.values():
            collect_sources(v, found)
    elif isinstance(node, list):
        for item in node:
            collect_sources(item, found)


def has_available_false(node: Any) -> bool:
    if isinstance(node, dict):
        if node.get("available") is False:
            return True
        return any(has_available_false(v) for v in node.values())
    if isinstance(node, list):
        return any(has_available_false(item) for item in node)
    return False


def has_data(node: Any) -> bool:
    if isinstance(node, dict):
        for v in node.values():
            if has_data(v):
                return True
        return False
    if isinstance(node, list):
        if len(node) > 0:
            return True
        return False
    if isinstance(node, bool):
        return False
    if isinstance(node, (int, float)):
        return node != 0
    if isinstance(node, str):
        return False
    return False


def matched_seed_literals(raw_text: str) -> list[str]:
    return [lit for lit in SEED_LITERALS if lit in raw_text]


def suspect_sources(sources: set[str]) -> list[str]:
    hits = []
    for s in sources:
        low = s.lower()
        if any(sub in low for sub in SUSPECT_SOURCE_SUBSTRINGS):
            hits.append(s)
    return hits


def resolve_path(template: str, params: dict[str, str]) -> tuple[str, bool]:
    """Fill {param} placeholders. Returns (path, used_synthetic_id)."""
    synthetic = False
    out = template
    for name in extract_param_names(template):
        value = params.get(name, DEFAULT_PATH_PARAMS.get(name, "1"))
        if name not in params:
            synthetic = True
        out = out.replace("{%s}" % name, str(value))
    return out, synthetic


def extract_param_names(template: str) -> list[str]:
    names = []
    buf = ""
    in_brace = False
    for ch in template:
        if ch == "{":
            in_brace = True
            buf = ""
        elif ch == "}":
            in_brace = False
            names.append(buf)
        elif in_brace:
            buf += ch
    return names


def discover_openapi(base_url: str, timeout: int) -> dict[str, Any]:
    status, payload, error = fetch(base_url, "/openapi.json", timeout)
    if status != 200 or not isinstance(payload, dict):
        raise SystemExit("could not load /openapi.json from %s: status=%s error=%s" % (base_url, status, error))
    return payload


def build_query_string(operation: dict[str, Any]) -> str:
    parts = []
    for p in operation.get("parameters", []):
        if p.get("in") != "query":
            continue
        name = p.get("name")
        if name in DEFAULT_QUERY_PARAMS:
            parts.append("%s=%s" % (name, DEFAULT_QUERY_PARAMS[name]))
    return ("?" + "&".join(parts)) if parts else ""


def discover_defaults(base_url: str, timeout: int, cluster_id_override: str | None = None) -> dict[str, str]:
    """Best-effort: use a live cluster id / database name instead of guesses.

    Without --cluster-id, this always resolves to whatever /api/v1/clusters
    calls "default" (or its first entry) — on a multi-cluster console (e.g.
    uat/prod/dr sharing one deployment) that silently audits only ONE
    cluster and gives no signal the others were skipped. Pass --cluster-id
    explicitly to audit a specific one (e.g. "prod").
    """
    params = dict(DEFAULT_PATH_PARAMS)
    status, payload, _ = fetch(base_url, "/api/v1/clusters", timeout)
    cluster_id = cluster_id_override
    if not cluster_id and status == 200 and isinstance(payload, dict):
        cluster_id = payload.get("default")
        if not cluster_id and payload.get("clusters"):
            cluster_id = payload["clusters"][0].get("id")
    if status == 200 and isinstance(payload, dict) and isinstance(payload.get("clusters"), list):
        available = [c.get("id") for c in payload["clusters"] if isinstance(c, dict)]
        if cluster_id_override and cluster_id_override not in available:
            print("WARNING: --cluster-id %r not in /api/v1/clusters list %r — check spelling" %
                  (cluster_id_override, available), file=sys.stderr)
        elif len(available) > 1:
            print("NOTE: %d clusters available (%s); auditing cluster_id=%r only" %
                  (len(available), ", ".join(available), cluster_id), file=sys.stderr)
    if cluster_id:
        params["cluster_id"] = cluster_id
        params["cluster_name"] = cluster_id
        status, payload, _ = fetch(base_url, "/api/v1/clusters/%s/databases" % cluster_id, timeout)
        if status == 200 and isinstance(payload, dict) and payload.get("databases"):
            first = payload["databases"][0]
            if isinstance(first, dict):
                params["database"] = first.get("name") or first.get("datname") or params["database"]
            elif isinstance(first, str):
                params["database"] = first
    return params


def classify(status: int, error: str | None, payload: Any, synthetic_id: bool) -> tuple[str, dict[str, Any]]:
    detail: dict[str, Any] = {}
    if not (200 <= status < 300):
        if synthetic_id and status == 404:
            return "SKIPPED", {"reason": "404 on a synthetic path id; endpoint reachable, not evaluated"}
        detail["error"] = error
        return "ERROR", detail

    raw = json.dumps(payload) if payload is not None else ""
    sources: set[str] = set()
    if isinstance(payload, (dict, list)):
        collect_sources(payload, sources)
    detail["sources"] = sorted(sources)

    seed_hits = matched_seed_literals(raw)
    if seed_hits:
        detail["seed_literal_matches"] = seed_hits
        return "SEED_SUSPECT", detail

    bad_sources = suspect_sources(sources)
    if bad_sources:
        detail["suspect_sources"] = bad_sources
        return "SEED_SUSPECT", detail

    unavailable = has_available_false(payload) if isinstance(payload, (dict, list)) else False
    data_present = has_data(payload) if isinstance(payload, (dict, list)) else False

    if unavailable and not data_present:
        return "NO_DATA", detail
    if not data_present:
        return "NO_DATA", detail
    if sources:
        return "LIVE", detail
    return "LIVE_UNCONFIRMED", detail


def check_seed_fallback_env(oc: str, namespace: str, deployment: str, timeout: int) -> str:
    if not shutil.which(oc):
        return "oc binary not found; check manually with: oc set env deploy/%s -n %s --list" % (deployment, namespace)
    try:
        proc = subprocess.run(
            [oc, "get", "deploy", deployment, "-n", namespace, "-o", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout,
        )
    except Exception as exc:
        return "oc get deploy failed: %s" % exc
    if proc.returncode != 0:
        return "oc get deploy failed: %s" % proc.stderr.strip()[:300]
    try:
        spec = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return "oc get deploy returned non-JSON output"
    containers = spec.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    for c in containers:
        for env in c.get("env", []) or []:
            if env.get("name") == "PGC_LOCAL_SEED_FALLBACK":
                return "PGC_LOCAL_SEED_FALLBACK=%r on container %s" % (env.get("value"), c.get("name"))
    return "PGC_LOCAL_SEED_FALLBACK not set (seed fallback disabled by default)"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://127.0.0.1:8080")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification (self-signed/internal-CA OpenShift routes, like curl -sk).")
    p.add_argument("--namespace", help="If set with --deployment and oc is available, checks PGC_LOCAL_SEED_FALLBACK on the live Deployment.")
    p.add_argument("--deployment")
    p.add_argument("--oc", default="oc")
    p.add_argument("--json-out", help="Write the full per-endpoint report to this path.")
    p.add_argument("--include-mutating", action="store_true", help="Also call POST/PUT/PATCH/DELETE routes (off by default: they can change state).")
    p.add_argument("--static-dir", help="Path to the static/ frontend directory. Enables FRONTEND_ONLY route-gap and mock/seed keyword scans.")
    p.add_argument("--cluster-id", help="Audit this cluster_id specifically (e.g. 'prod') instead of auto-picking whatever /api/v1/clusters calls 'default'.")
    return p.parse_args()


def main() -> int:
    global _SSL_CONTEXT
    args = parse_args()
    if args.insecure:
        _SSL_CONTEXT = ssl._create_unverified_context()
    spec = discover_openapi(args.base_url, args.timeout)
    params = discover_defaults(args.base_url, args.timeout, cluster_id_override=args.cluster_id)

    methods_allowed = {"get"} | ({"post", "put", "patch", "delete"} if args.include_mutating else set())

    results: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for template, operations in sorted(spec.get("paths", {}).items()):
        if template.startswith(SKIP_PATH_PREFIXES):
            continue
        for method, operation in operations.items():
            if method.lower() not in methods_allowed:
                continue
            if method.lower() != "get":
                # Mutating routes are out of scope for a "does it show real data" audit.
                continue
            path, synthetic_id = resolve_path(template, params)
            path += build_query_string(operation)
            status, payload, error = fetch(args.base_url, path, args.timeout)
            verdict, detail = classify(status, error, payload, synthetic_id)
            counts[verdict] = counts.get(verdict, 0) + 1
            results.append({
                "path": path,
                "template": template,
                "status": status,
                "verdict": verdict,
                **detail,
            })
            marker = {
                "LIVE": "LIVE ", "SEED_SUSPECT": "SEED!", "NO_DATA": "empty",
                "LIVE_UNCONFIRMED": "?src ", "SKIPPED": "skip ", "ERROR": "ERR  ",
            }.get(verdict, verdict)
            extra = ""
            if verdict == "SEED_SUSPECT":
                extra = " matches=%s sources=%s" % (detail.get("seed_literal_matches") or detail.get("suspect_sources"), detail.get("sources"))
            elif verdict == "LIVE":
                extra = " sources=%s" % detail.get("sources")
            elif verdict == "ERROR":
                extra = " error=%s" % detail.get("error")
            print("[%s] %3s %s%s" % (marker, status, path, extra))

    print("\n=== SUMMARY (%s) ===" % args.base_url)
    for verdict in ("LIVE", "NO_DATA", "LIVE_UNCONFIRMED", "SEED_SUSPECT", "SKIPPED", "ERROR"):
        if verdict in counts:
            print("%-16s %d" % (verdict, counts[verdict]))
    print("total endpoints checked: %d" % len(results))

    if args.namespace and args.deployment:
        print("\n=== PGC_LOCAL_SEED_FALLBACK on %s/%s ===" % (args.namespace, args.deployment))
        print(check_seed_fallback_env(args.oc, args.namespace, args.deployment, args.timeout))

    if any(r["verdict"] == "SEED_SUSPECT" for r in results):
        print("\n%d endpoint(s) show SEED_SUSPECT — they returned seed-fixture literals or a suspect \"source\" tag instead of live data. See lines marked [SEED!] above." % counts.get("SEED_SUSPECT", 0))
    if any(r["verdict"] == "LIVE_UNCONFIRMED" for r in results):
        print("%d endpoint(s) returned data with no \"source\" tag at all (LIVE_UNCONFIRMED) — worth a manual read of the handler." % counts.get("LIVE_UNCONFIRMED", 0))

    frontend_gaps: list[str] = []
    frontend_hints: list[dict[str, Any]] = []
    blocking_frontend_hints: list[dict[str, Any]] = []
    orphaned_scripts: list[str] = []
    if args.static_dir:
        static_dir = Path(args.static_dir)
        backend_paths = list(spec.get("paths", {}).keys())
        frontend_paths = scan_frontend_api_paths(static_dir)
        frontend_gaps = frontend_backend_gaps(frontend_paths, backend_paths)
        frontend_hints = scan_frontend_seed_hints(static_dir)
        blocking_frontend_hints = blocking_ai_frontend_seed_hints(frontend_hints)
        orphaned_scripts = find_orphaned_scripts(static_dir)

        print("\n=== FRONTEND ROUTE GAPS (%s) ===" % static_dir)
        if frontend_gaps:
            for gap in frontend_gaps:
                print("[FRONTEND_ONLY] %s  (no matching route in /openapi.json)" % gap)
            print("%d frontend-referenced path(s) have no backend route at all — those UI panels can only ever show a client-side fallback." % len(frontend_gaps))
        else:
            print("none — every /api/... path referenced in static JS/JSX matches a live backend route.")

        print("\n=== ORPHANED SCRIPTS (exist on disk, no <script> tag in index.html) ===")
        if orphaned_scripts:
            for f in orphaned_scripts:
                print("[ORPHANED] %s  (never loaded by the browser -- any screen it implements renders as a silent blank)" % f)
        else:
            print("none — every .js file under static/ is referenced by index.html.")

        print("\n=== FRONTEND MOCK/SEED KEYWORD HITS (%s) ===" % static_dir)
        if frontend_hints:
            for h in frontend_hints:
                print("%s:%d [%s] %s" % (h["file"], h["line"], h["matched"], h["text"]))
            print("%d line(s) in static JS/JSX reference mock/seed/sample fallbacks — grep the surrounding code to confirm whether they're reachable in production." % len(frontend_hints))
        else:
            print("none")

        print("\n=== BLOCKING AI FRONTEND FALLBACKS (%s) ===" % static_dir)
        if blocking_frontend_hints:
            for h in blocking_frontend_hints:
                print("[AI_FRONTEND_SEED] %s:%d [%s] %s" % (h["file"], h["line"], h["matched"], h["text"]))
            print("%d AI-facing frontend fallback(s) found; this release must not be deployed until they are removed." % len(blocking_frontend_hints))
        else:
            print("none")

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "base_url": args.base_url,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "path_params_used": params,
            "counts": counts,
            "results": results,
            "frontend_route_gaps": frontend_gaps,
            "frontend_seed_hints": frontend_hints,
            "blocking_ai_frontend_seed_hints": blocking_frontend_hints,
            "orphaned_scripts": orphaned_scripts,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("\nfull report written to %s" % out_path)

    return 1 if counts.get("SEED_SUSPECT") or frontend_gaps or orphaned_scripts or blocking_frontend_hints else 0


if __name__ == "__main__":
    raise SystemExit(main())
