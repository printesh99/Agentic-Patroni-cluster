#!/usr/bin/env bash
# =============================================================================
# objmon_panel_data_extractor.sh   — run on the bastion (ns monitoring access)
# -----------------------------------------------------------------------------
# PURPOSE
#   Deep, READ-ONLY audit of EVERY console panel's backing API endpoint, so we
#   see exactly which modules/panels return DATA vs EMPTY vs UNAVAIL vs ERROR.
#   It execs into the object-monitor pod and probes each GET endpoint in-process
#   over http://localhost:PORT using python stdlib (no curl / no credentials —
#   the API has no auth guard). It auto-discovers cluster id/name and drills one
#   instance for id-templated routes (session pid, incident id, signature, ...).
#
#   Output = ONE text file you upload back:
#     /tmp/objmon_panel_report_<ts>.txt
#   It starts with a SUMMARY of every non-DATA panel grouped by module, then a
#   full per-endpoint breakdown (http, state, shape/counts, reason).
#
# USAGE (bastion, logged in to api.ocp-dr; mohsinali is fine for ns monitoring):
#   ./objmon_panel_data_extractor.sh
#   gsutil cp /tmp/objmon_panel_report_*.txt gs://postgres_patroni/logs/
#
# ENV: CNS (console ns, default monitoring), PORT (default 8080),
#      APP (deployment/pod name prefix, default object-monitor).
# =============================================================================
set -uo pipefail

CNS="${CNS:-monitoring}"
PORT="${PORT:-8080}"
APP="${APP:-object-monitor}"
TS="$(date -u +%Y%m%d_%H%M%S)"
OUT="${OUT:-/tmp/objmon_panel_report_${TS}.txt}"

command -v oc >/dev/null 2>&1 || { echo "FATAL: 'oc' not in PATH"; exit 2; }

POD=$(oc -n "$CNS" get pods --no-headers 2>/dev/null \
  | grep -E "^${APP}-" | grep -vE 'build|-db-|deploy' | awk '$3=="Running"{print $1; exit}')
if [ -z "$POD" ]; then
  echo "FATAL: no Running ${APP}-* pod in ns $CNS (check: oc -n $CNS get pods)"; exit 2
fi

{
  echo "############################################################"
  echo "# object-monitor PANEL DATA extractor (READ-ONLY)"
  echo "#   cluster : $(oc whoami --show-server 2>/dev/null)  as $(oc whoami 2>/dev/null)"
  echo "#   time    : $(date -u +%FT%TZ)"
  echo "#   pod     : $CNS/$POD   base: http://localhost:$PORT"
  echo "############################################################"
} | tee "$OUT"

oc -n "$CNS" exec -i "$POD" -- env BASE="http://localhost:${PORT}" python3 - <<'PYEOF' 2>&1 | tee -a "$OUT"
import json, os, sys, time
import urllib.request, urllib.error

BASE = os.environ.get("BASE", "http://localhost:8080")
TIMEOUT = 20

def get(path):
    """GET path -> (http_status, parsed_json_or_text, err)."""
    url = BASE + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
            code = r.getcode()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        code = e.code
    except Exception as e:
        return (0, None, "%s: %s" % (type(e).__name__, str(e)[:180]))
    try:
        return (code, json.loads(body), None)
    except Exception:
        return (code, body[:400], None)

# ---- data-presence classification -----------------------------------------
IGNORE = {"available", "source", "reason", "ok", "cluster_id", "id", "generated_at",
          "timestamp", "ts", "status", "name", "hypopg_available", "safety"}

SEED_LITERALS = [
    "uat_core_logical_slot", "uat_docs_archive_slot", "uat_gateway_physical_slot",
    "sub_core_to_reporting", "sub_docs_to_archive", "sub_gateway_to_fraud",
    "pub_api_events", "pub_documents",
    "core-apply-worker", "docs-archive-sender", "gateway-fraud-apply",
    "uat_core_banking", "uat_customer", "uat_etl", "uat_gateway",
    "uat_mobile", "uat_locker", "uat_documents", "uat_payments", "uat_cards",
    "REC-2041", "Autovacuum starvation", "local-llama-70b",
]
SUSPECT_SOURCE_SUBSTRINGS = [
    "seed", "mock", "sample", "demo", "static", "hardcoded", "placeholder",
    "local-empty", "fallback", "fixture", "stub", "dummy", "test data",
]
AI_FRONTEND_BLOCKING_PHRASES = [
    "representative sample", "fall back to sample", "falls back to sample",
    "fallback to sample", "mock data", "sample data", "dummy data",
    "fake data", "local mock", "data: sample",
]

def scan(obj):
    """return (total_list_items, nonempty_lists, scalar_data, shape_str)."""
    items = 0; nonempty = 0; scalars = 0; shape = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list):
                items += len(v); nonempty += (1 if v else 0)
                shape.append("%s[%d]" % (k, len(v)))
            elif isinstance(v, dict):
                si, sn, ss, _ = scan(v)
                items += si; nonempty += sn; scalars += ss
                shape.append("%s{%d}" % (k, len(v)))
            else:
                if k not in IGNORE and v not in (None, "", 0):
                    scalars += 1
        return items, nonempty, scalars, " ".join(shape[:12])
    if isinstance(obj, list):
        return len(obj), (1 if obj else 0), 0, "list[%d]" % len(obj)
    return 0, 0, (1 if obj not in (None, "", 0) else 0), "scalar"

def classify(code, body):
    if code == 0:
        return "ERROR", "conn: %s" % body
    if code == 401 or code == 403:
        return "AUTH", "http %d" % code
    if code == 404:
        return "NOTFOUND", "http 404 (route/instance missing)"
    if code == 422:
        return "PARAMS", "http 422 (needs query params)"
    if code == 501:
        return "UNAVAIL", "http 501 not implemented"
    if code >= 500:
        d = ""
        if isinstance(body, dict):
            d = str(body.get("detail") or body.get("error") or "")[:160]
        elif isinstance(body, str):
            d = body[:160]
        return "ERROR", "http %d %s" % (code, d)
    # 2xx
    if isinstance(body, (dict, list)):
        raw = json.dumps(body, default=str)
        seed_hits = [lit for lit in SEED_LITERALS if lit in raw]
        if seed_hits:
            return "SEED_SUSPECT", "seed literals: %s" % ", ".join(seed_hits[:8])
        src_hits = []
        def collect_sources(node):
            if isinstance(node, dict):
                val = node.get("source")
                if isinstance(val, str):
                    low = val.lower()
                    # Word boundaries prevent the legitimate source label
                    # "pg_stat_activity sampler" from matching "sample".
                    if any(__import__("re").search(r"(?<![a-z0-9])%s(?![a-z0-9])" % __import__("re").escape(s), low)
                           for s in SUSPECT_SOURCE_SUBSTRINGS):
                        src_hits.append(val)
                for vv in node.values():
                    collect_sources(vv)
            elif isinstance(node, list):
                for item in node:
                    collect_sources(item)
        collect_sources(body)
        if src_hits:
            return "SEED_SUSPECT", "suspect source: %s" % ", ".join(sorted(set(src_hits))[:5])
    if isinstance(body, dict):
        if body.get("available") is False:
            return "UNAVAIL", "available=false: %s" % str(body.get("reason", ""))[:140]
        items, nonempty, scalars, shape = scan(body)
        if items > 0:
            return "DATA", shape
        if scalars > 0:
            return "DATA", shape or "scalars"
        return "EMPTY", shape or "no rows"
    if isinstance(body, list):
        return ("DATA" if body else "EMPTY"), "list[%d]" % len(body)
    if body in (None, "", "null"):
        return "EMPTY", "empty body"
    return "DATA", "text"

def pick(obj, keys):
    """find first id-ish value in a list/dict response."""
    lst = None
    if isinstance(obj, dict):
        for k in ("sessions","incidents","models","anomalies","signatures","jobs",
                  "runbooks","items","rows","data","dashboards","forecasts"):
            if isinstance(obj.get(k), list) and obj[k]:
                lst = obj[k]; break
    elif isinstance(obj, list):
        lst = obj
    if not lst:
        return None
    first = lst[0]
    if isinstance(first, dict):
        for k in keys:
            if first.get(k) not in (None, ""):
                return first[k]
    elif isinstance(first, (str, int)):
        return first
    return None

# ---- discover cluster id / name -------------------------------------------
code, cl, _ = get("/api/v1/clusters")
CID, CNAME = None, None
if isinstance(cl, (list, dict)):
    c = cl[0] if isinstance(cl, list) and cl else (
        (cl.get("clusters") or cl.get("items") or [None])[0] if isinstance(cl, dict) else None)
    if isinstance(c, dict):
        CID = c.get("id") or c.get("cluster_id") or c.get("name")
        CNAME = c.get("name") or c.get("cluster_name") or CID
CID = CID or os.environ.get("CID", "uae")
CNAME = CNAME or CID
print("\ndiscovered cluster_id=%r  cluster_name=%r\n" % (CID, CNAME))

def prefetch_id(path, keys):
    c, b, _ = get(path)
    return pick(b, keys) if c == 200 else None

PID   = prefetch_id("/api/v1/clusters/%s/perf/sessions?limit=50" % CID, ["pid"])
INC   = prefetch_id("/api/v1/ai/incidents", ["id","incident_id"])
SIG   = prefetch_id("/api/v1/clusters/%s/log-analytics/signatures" % CID, ["id","sid","signature_id"])
MODEL = prefetch_id("/api/v1/ml/models", ["id","model_id"])
SNAP  = prefetch_id("/api/v1/ml/anomalies", ["snapshot_id","id"])
JOB   = prefetch_id("/api/v1/jobs", ["id","job_id"])
RB    = prefetch_id("/api/v1/help/runbooks", ["id","rb_id"])
c,bz,_ = get("/api/v1/clusters/%s/bizmon/dashboards" % CID)
PANEL = None
if c == 200:
    PANEL = pick(bz, ["id","panel","key","name"])
    if PANEL is None and isinstance(bz, dict):
        for v in bz.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                PANEL = v[0].get("id") or v[0].get("key"); break

# ---- endpoint catalogue: (module, path, needs) ----------------------------
CX = lambda p: "/api/v1/clusters/%s%s" % (CID, p)
E = [
 # module, path, skip_reason(None=probe)
 ("Overview", "/api/v1/ui/overview/%s" % CID, None),
 ("Overview", "/api/v1/ui/cluster/%s" % CID, None),
 ("Overview", "/api/v1/clusters/%s" % CID, None),
 ("Overview", "/api/overview", None),
 ("Overview", "/api/v1/readiness", None),
 ("Overview", "/api/v1/readiness/score", None),
 ("Overview", "/api/v1/health-check/latest", None),
 ("Cluster",  CX("/pods"), None),
 ("Cluster",  CX("/databases"), None),
 ("Cluster",  CX("/config/patroni"), None),
 ("Performance", CX("/perf/sessions?limit=25"), None),
 ("Performance", CX("/perf/waits"), None),
 ("Performance", CX("/perf/slow"), None),
 ("Performance", CX("/perf/vacuum"), None),
 ("Performance", CX("/perf/bloat"), None),
 ("Performance", CX("/perf/topsql?limit=25"), None),
 ("Performance", CX("/perf/topsql/history"), None),
 ("Performance", CX("/perf/index-advisor"), None),
 ("Performance", CX("/perf/application-activity"), None),
 ("Performance", CX("/perf/locks"), None),
 ("Performance", CX("/perf/session/%s/insight" % PID) if PID else None,
      None if PID else "no active session pid"),
 ("Metrics", CX("/metrics/catalog"), None),
 ("Metrics", CX("/metrics/entities"), None),
 ("Metrics", CX("/metrics/series?metric=connections&range=24h"), None),
 ("Metrics", CX("/metrics/series?metric=storage_bytes&range=24h"), None),
 ("Metrics", CX("/metrics/series?metric=tps&range=24h"), None),
 ("Metrics", CX("/metrics/forecast?metric=connections"), None),
 # v31 live-only chart overlays. available=false is honest UNAVAIL, never DATA.
 ("LiveCharts", "/api/v1/charts/advisor?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/wal?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/backups?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/dr?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/logs?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/objects?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/perf?view=waits&cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/perf?view=topsql&cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/perf?view=indexes&cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/perf?view=bloat&cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/perf?view=vacuum&cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/perf?view=activity&cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/cluster?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/replication?view=physical&cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/replication?view=logical&cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/anomalies?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/heatmap?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/capacity?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/collector?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/charts/upgrades?cluster_id=%s" % CID, None),
 ("LiveCharts", "/api/v1/perf/db-load?window=24h&dim=wait_class&cluster_id=%s" % CID, None),
 ("Replication", CX("/replication/topology"), None),
 ("Replication", CX("/replication/logical"), None),
 ("Replication", CX("/replication/sync"), None),
 ("Replication", CX("/replication/history"), None),
 ("Replication", CX("/replication/fdw"), None),
 ("Backups", CX("/backups"), None),
 ("Backups", CX("/backups/schedules"), None),
 ("Backups", CX("/pitr/preview"), None),
 ("Security", CX("/roles"), None),
 ("Security", CX("/hba"), None),
 ("Security", CX("/tls"), None),
 ("Security", CX("/privileges"), None),
 ("Security", CX("/sensitive-data"), None),
 ("Security", CX("/pgaudit"), None),
 ("Security", CX("/auth"), None),
 ("Configuration", CX("/config/parameters"), None),
 ("Configuration", CX("/config/database-settings"), None),
 ("Configuration", CX("/config/role-settings"), None),
 ("Configuration", CX("/config/maintenance"), None),
 ("AppMon", CX("/appmon/overview"), None),
 ("AppMon", CX("/appmon/trend?range=24h"), None),
 ("AppMon", CX("/appmon/replication"), None),
 ("AppMon", CX("/appmon/top-sessions"), None),
 ("AppMon", CX("/appmon/filters"), None),
 ("AppMon", CX("/appmon/dba-evidence"), None),
 ("BizMon", CX("/bizmon/dashboards"), None),
 ("BizMon", CX("/bizmon/panel/%s" % PANEL) if PANEL else None,
      None if PANEL else "no bizmon panel id"),
 ("Logs", CX("/logs/labels"), None),
 ("Logs", CX("/logs/search?limit=5"), None),
 ("Logs", CX("/logs/histogram?range=1h"), None),
 ("Logs", CX("/logs/diag"), None),
 ("LogAnalytics", CX("/log-analytics/summary"), None),
 ("LogAnalytics", CX("/log-analytics/categories"), None),
 ("LogAnalytics", CX("/log-analytics/findings"), None),
 ("LogAnalytics", CX("/log-analytics/signatures"), None),
 ("LogAnalytics", CX("/log-analytics/signatures/%s" % SIG) if SIG else None,
      None if SIG else "no signature id"),
 ("Objects", "/api/databases", None),
 ("Objects", "/api/tables", None),
 ("Objects", "/api/indexes", None),
 ("Objects", "/api/slots", None),
 ("Objects", "/api/pubsub", None),
 ("Objects", "/api/regions", None),
 ("Objects", "/api/snapshots/latest", None),
 ("ML/AI", "/api/v1/ml/models", None),
 ("ML/AI", "/api/v1/ml/models/%s" % MODEL if MODEL else None,
      None if MODEL else "no model id"),
 ("ML/AI", "/api/v1/ml/anomalies", None),
 ("ML/AI", "/api/v1/ml/anomalies/%s" % SNAP if SNAP else None,
      None if SNAP else "no snapshot id"),
 ("ML/AI", "/api/v1/ml/forecasts", None),
 ("ML/AI", "/api/v1/ai/incidents", None),
 ("ML/AI", "/api/v1/ai/incidents/%s" % INC if INC else None,
      None if INC else "no incident id"),
 ("Assistant", "/api/v1/assistant/status", None),
 ("Assistant", "/api/v1/assistant/anomalies", None),
 ("Alerts", "/api/v1/alerts", None),
 ("Alerts", "/api/v1/alerts/notifications", None),
 ("Alerts", "/api/v1/alert-rules", None),
 ("Alerts", "/api/v1/rules/evaluate/latest", None),
 ("Alerts", "/api/v1/notifications/channels", None),
 ("Ops", "/api/v1/cutover/config", None),
 ("Ops", "/api/v1/cutover/modes", None),
 ("Ops", "/api/v1/cutover/runs", None),
 ("Ops", "/api/v1/lifecycle/provision", None),
 ("Ops", "/api/v1/lifecycle/provision/defaults", None),
 ("Ops", "/api/v1/live-connections", None),
 ("Ops", "/api/v1/live-connections/defaults", None),
 ("Ops", "/api/v1/compliance/operational", None),
 ("Ops", "/api/v1/collector/runs", None),
 ("Ops", "/api/v1/collector/alert-bundle-requests", None),
 ("Ops", "/api/v1/scheduler/status", None),
 ("Jobs", "/api/v1/jobs", None),
 ("Jobs", "/api/v1/jobs/%s" % JOB if JOB else None, None if JOB else "no job id"),
 ("Meta", "/api/v1/me", None),
 ("Meta", "/api/v1/tenants", None),
 ("Meta", "/api/v1/tokens", None),
 ("Meta", "/api/v1/regions", None),
 ("Meta", "/api/v1/audit", None),
 ("Meta", "/api/v1/actions/audit", None),
 ("Meta", "/api/v1/search-index", None),
 ("Meta", "/api/v1/help/runbooks", None),
 ("Health", "/api/v1/health", None),
]

# ---- probe -----------------------------------------------------------------
results = []  # (module, path, http, state, detail)
for row in E:
    if row is None:
        continue
    module, path, skip = row
    if path is None:
        results.append((module, "(id-templated)", "-", "SKIP", skip or "no id"))
        continue
    if skip:
        results.append((module, path, "-", "SKIP", skip))
        continue
    code, body, err = get(path)
    if err:
        state, detail = "ERROR", err
    else:
        state, detail = classify(code, body)
    results.append((module, path, code, state, detail))
    time.sleep(0.03)

# ---- frontend AI bundle scan ----------------------------------------------
def frontend_seed_results():
    roots = ["/app/static", "/opt/app-root/src/static", "./static"]
    files = [
        "dist/ai_platform.js", "dist/ai_platform2.js", "dist/ai_ops.js",
        "dist/ai_agent.js", "dist/viz_core.js", "dist/live_charts.js",
        "dist/appmon_charts.js", "dist/overview_charts.js", "ai-ui.js", "logs-ui.js",
    ]
    out = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for rel in files:
            path = os.path.join(root, rel)
            if path in seen or not os.path.isfile(path):
                continue
            seen.add(path)
            try:
                text = open(path, "r", encoding="utf-8", errors="replace").read().lower()
            except Exception as exc:
                out.append(("Frontend", rel, "-", "ERROR", "cannot read static file: %s" % exc))
                continue
            hits = [p for p in AI_FRONTEND_BLOCKING_PHRASES if p in text]
            literal_hits = [lit for lit in SEED_LITERALS if lit.lower() in text]
            if hits or literal_hits:
                out.append(("Frontend", rel, "-", "SEED_SUSPECT",
                            "frontend fallback markers: %s" % ", ".join((hits + literal_hits)[:10])))
            else:
                out.append(("Frontend", rel, "-", "DATA", "static bundle has no AI fallback markers"))
    if not seen:
        out.append(("Frontend", "(static scan)", "-", "SKIP", "static directory not found in pod"))
    return out

results.extend(frontend_seed_results())

# ---- report ----------------------------------------------------------------
order = {"ERROR":0,"AUTH":1,"SEED_SUSPECT":2,"UNAVAIL":3,"EMPTY":4,"NOTFOUND":5,"PARAMS":6,"SKIP":7,"DATA":8}
def C(s): return sum(1 for r in results if r[3]==s)

print("="*70)
print(" SUMMARY  (%d endpoints probed)" % len(results))
print("   DATA=%d  SEED_SUSPECT=%d  EMPTY=%d  UNAVAIL=%d  ERROR=%d  NOTFOUND=%d  PARAMS=%d  AUTH=%d  SKIP=%d"
      % (C("DATA"),C("SEED_SUSPECT"),C("EMPTY"),C("UNAVAIL"),C("ERROR"),C("NOTFOUND"),C("PARAMS"),C("AUTH"),C("SKIP")))
print("="*70)
print("\n>>> PANELS NOT SHOWING DATA (fix these) — grouped by module:\n")
bad = [r for r in results if r[3] not in ("DATA","SKIP")]
if not bad:
    print("  (none — every probed panel returned data)")
else:
    lastm = None
    for module, path, code, state, detail in sorted(bad, key=lambda r:(r[0], order[r[3]])):
        if module != lastm:
            print("  [%s]" % module); lastm = module
        print("    %-9s http=%-3s  %s" % (state, code, path))
        if detail: print("              %s" % detail[:150])

print("\n" + "="*70)
print(" FULL BREAKDOWN (all endpoints, incl. DATA with shape/counts)")
print("="*70)
lastm = None
for module, path, code, state, detail in sorted(results, key=lambda r:(r[0], order[r[3]], r[1])):
    if module != lastm:
        print("\n[%s]" % module); lastm = module
    print("  %-9s http=%-3s %s" % (state, code, path))
    if detail:
        print("            %s" % detail[:150])

print("\nDONE. Upload this file.")
PYEOF

echo | tee -a "$OUT"
echo "report written: $OUT" | tee -a "$OUT"
echo "upload with:  gsutil cp $OUT gs://postgres_patroni/logs/"
