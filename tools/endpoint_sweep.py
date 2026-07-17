import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"

GETS = [
    "/livez",
    "/api/v1/health",
    "/api/v1/clusters",
    "/api/v1/ui/overview/uat?range=1h",
    "/api/v1/clusters/uat/databases",
    "/api/v1/clusters/uat/metrics/catalog",
    "/api/v1/clusters/uat/metrics/series?metric=connections&range=1h",
    "/api/v1/clusters/uat/appmon/filters",
    "/api/v1/clusters/uat/appmon/overview",
    "/api/v1/clusters/uat/appmon/overview?domain=TPS",
    "/api/v1/clusters/uat/appmon/top-sessions?limit=10",
    "/api/v1/clusters/uat/appmon/trend?range=1h",
    "/api/v1/clusters/uat/appmon/domain/tps?limit=5",
    "/api/v1/clusters/uat/appmon/domain/tps_warehouse?limit=5",
    "/api/v1/clusters/uat/appmon/domain/service?limit=5",
    "/api/v1/clusters/uat/appmon/domain/api_gateway?limit=5",
    "/api/v1/clusters/uat/appmon/domain/charge?limit=5",
    "/api/v1/clusters/uat/appmon/domain/locker?limit=5",
    "/api/v1/clusters/uat/appmon/domain/mobile?limit=5",
    "/api/v1/clusters/uat/appmon/domain/document?limit=5",
    "/api/v1/clusters/uat/appmon/replication",
    "/api/v1/clusters/uat/appmon/dba-evidence?limit=5",
    "/api/v1/clusters/uat/bizmon/dashboards",
    "/api/v1/clusters/uat/bizmon/panel/business_customers",
    "/api/v1/clusters/uat/bizmon/panel/business_accounts",
    "/api/v1/clusters/uat/bizmon/panel/business_postings",
    "/api/v1/clusters/uat/bizmon/panel/business_revenue",
    "/api/v1/clusters/uat/bizmon/panel/business_channel_mix",
    "/api/v1/clusters/uat/bizmon/panel/business_txn_mix",
    "/api/v1/clusters/uat/bizmon/panel/business_event_trend",
    "/api/v1/clusters/uat/bizmon/panel/management_sessions",
    "/api/v1/clusters/uat/bizmon/panel/management_db_size",
    "/api/v1/clusters/uat/bizmon/panel/management_risk",
    "/api/v1/clusters/uat/bizmon/panel/management_channel_adoption",
    "/api/v1/clusters/uat/bizmon/panel/management_table_churn",
    "/api/v1/clusters/uat/bizmon/panel/management_locks",
    "/api/v1/clusters/uat/perf/bloat?limit=3",
    "/api/v1/clusters/uat/perf/index-advisor?limit=3",
    "/api/v1/clusters/uat/recommendations?limit=5",
    "/api/v1/assistant/status",
]

POSTS = [
    ("/api/v1/assistant/ask", {"question": "Show top PostgreSQL risk and tuning recommendations for this local test cluster."}),
]


def fetch(path, method="GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode()
            payload = json.loads(text) if text else {}
            return resp.status, payload, None
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        return exc.code, None, text[:240]
    except Exception as exc:
        return 0, None, str(exc)


def summarize(payload):
    if payload is None:
        return "no payload"
    if not isinstance(payload, dict):
        return f"type={type(payload).__name__}"
    keys = []
    for key in (
        "rows", "databases", "regions", "series", "points", "metrics", "top_by_size",
        "top_by_rows", "dead_tuples", "dml_churn", "sessions", "slots",
        "subscriptions", "workers", "locks", "mod_since_analyze", "seq_scans",
        "indexes", "dashboards", "clusters", "items", "recommendations",
    ):
        val = payload.get(key)
        if isinstance(val, list):
            keys.append(f"{key}={len(val)}")
    for key in ("total", "active", "idle", "coverage", "enabled", "available", "llm_connected"):
        if key in payload:
            keys.append(f"{key}={payload[key]}")
    if "answer" in payload:
        keys.append(f"answer_chars={len(str(payload['answer']))}")
    return ", ".join(keys) or "ok"


failures = []
for path in GETS:
    status, payload, error = fetch(path)
    ok = 200 <= status < 300
    if not ok:
        failures.append((path, status, error))
    print(f"{'PASS' if ok else 'FAIL'} {status:>3} GET  {path} :: {summarize(payload) if ok else error}")

for path, body in POSTS:
    status, payload, error = fetch(path, method="POST", body=body)
    ok = 200 <= status < 300
    if not ok:
        failures.append((path, status, error))
    print(f"{'PASS' if ok else 'FAIL'} {status:>3} POST {path} :: {summarize(payload) if ok else error}")

print(f"SUMMARY total={len(GETS) + len(POSTS)} failures={len(failures)}")
if failures:
    raise SystemExit(1)
