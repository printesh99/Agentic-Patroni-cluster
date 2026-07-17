from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_active_v31_chart_layer_is_live_only() -> None:
    index = _text("static/index.html")
    assert "/dist/viz_core.js" in index
    assert "/dist/live_charts.js" in index
    for legacy in ("module_charts.js", "module_charts2.js", "module_charts3.js",
                   "module_charts4.js", "sql_insight2.js", "drilldowns.js"):
        assert "/dist/" + legacy not in index


def test_live_chart_bundle_uses_only_api_attributed_payloads() -> None:
    source = _text("static/live_charts.js")
    bundle = _text("static/dist/live_charts.js")
    for text in (source, bundle):
        assert "/api/v1/charts/" in text
        assert "available === true" in text
        assert "representative sample" not in text
        assert "seeded(" not in text
        assert "Math.random" not in text
    for module in ("advisor", "wal", "backups", "dr", "logs", "objects",
                   "perf", "cluster", "replication", "capacity", "anomalies",
                   "heatmap", "collector", "upgrades"):
        assert f'chartUrl("{module}"' in source


def test_overview_and_appmon_do_not_generate_fallback_values() -> None:
    for path in ("static/overview_charts.js", "static/appmon_charts.js",
                 "static/dist/overview_charts.js", "static/dist/appmon_charts.js"):
        text = _text(path)
        assert "representative sample" not in text
        assert "seeded(" not in text
        assert "hump(" not in text


def test_chart_backend_contract_is_live_or_unavailable() -> None:
    api = _text("app/api_charts.py")
    backend = _text("app/pg_charts.py")
    assert "never sample data" in api
    assert '_EMPTY = {"available": False}' in backend
    assert "seeded(" not in backend
    assert "Math.random" not in backend
    assert '"source"' in backend
    assert 'data.get("bloat") or data.get("tables")' in backend


def test_v31_keeps_llm_attribution_bundle() -> None:
    assistant = _text("static/dist/assistant.js")
    assert "Azure " in assistant
    assert "Heuristic fallback" in assistant
    assert "provider_http_status" in assistant
    assert "provider_request_id" in assistant
