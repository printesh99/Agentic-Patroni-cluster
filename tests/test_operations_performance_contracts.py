import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_modified_python_sources_parse():
    for path in ("app/pg_ops.py", "app/pg_backups.py", "app/pg_perf.py", "app/api_perf.py", "app/api_compat.py", "app/api_actions.py"):
        ast.parse(text(path), filename=path)


def test_readiness_contract_has_identity_freshness_and_summary():
    source = text("app/pg_ops.py")
    for marker in ('"generated_at": generated_at', '"checked_at": generated_at', '"key": key_map.get', '"label": name', '"ok": sum('):
        assert marker in source
    ui = text("static/readiness.jsx")
    assert "data.items || data.checks || []" in ui
    assert "(data.summary || {}).status" in ui


def test_operations_use_live_contracts():
    remaining = text("static/remaining_phases.jsx")
    gaps = text("static/cloud_console_gaps.jsx")
    parity = text("static/cloud_console_parity.jsx")
    assert "data.bundles || state.data.bundles.data.requests" in remaining
    assert "readiness.items || readiness.checks" in remaining
    assert "summary || {}).total" in parity
    assert "topsql.top_sql || topsql.statements" in gaps
    assert "logs/search" in gaps and "logEntries" in gaps
    assert "a.ts || a.created_at" in gaps


def test_backup_ui_has_no_seeded_operational_schedule():
    backend = text("app/pg_backups.py")
    ui = text("static/backups.jsx")
    assert '"schedules": schedule_doc.get("schedules", [])' in backend
    assert "var scheduleState = React.useState([]);" in ui
    for forbidden in ('0 */6 * * *', 's3.openshift-storage.svc', 'pgbackrest_total_backups'):
        assert forbidden not in ui


def test_pod_log_preview_is_bounded_validated_and_redacted():
    source = text("app/api_compat.py")
    assert 'pods/{pod}/logs/preview' in source
    assert "max(20, min(int(tail), 500))" in source
    assert "container not found in selected pod" in source
    assert "_safe_text(line, 2000)" in source


def test_performance_contracts_are_current_and_honest():
    api = text("app/api_perf.py")
    backend = text("app/pg_perf.py")
    ui = text("static/performance.jsx")
    assert '"generated_at"' in api
    assert '"history_available": False' in api
    assert '"dead_tuple_percent"' in backend
    assert '"wait_event_type"' in backend and '"queryid"' in backend
    assert "data.source_breakdown || []" in ui
    assert "row.size_bytes" in ui
    assert "History store unavailable" in ui
    assert "Repeated captures create" not in ui


def test_compiled_bundles_are_expected_to_match_source_contracts():
    expected = {
        "readiness.js": "Healthy sources",
        "remaining_phases.js": ".data.bundles||",
        "cloud_console_gaps.js": "logs/search",
        "cloud_console_parity.js": "summary||{}).total",
        "performance.js": "History store unavailable",
        "backups.js": "Backup Schedules",
        "ops.js": "Execution evidence",
        "administration.js": "/logs/preview",
    }
    for filename, marker in expected.items():
        assert marker in text("static/dist/" + filename)


def test_classic_screen_bundles_export_global_routes_and_cache_is_busted():
    exports = {
        "static/dist/ops.js": ("window.RunHistoryScreen", "window.AuditLogScreen", "window.AlertsScreen", "window.v1Json", "window.Phase1Toolbar", "window.phase1Pill", "window.phase1Date"),
        "static/dist/administration.js": ("window.PodLogsScreen", "window.AdministrationScreen"),
        "static/dist/backups.js": ("window.BackupRecoveryScreen",),
    }
    for path, markers in exports.items():
        bundle = text(path)
        for marker in markers:
            assert marker in bundle
    index = text("static/index.html")
    assert "20260715T103000Z" in index
    assert "20260714T120000Z" not in index
