from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "ai_ops.jsx").read_text()
BUNDLE = (ROOT / "static" / "dist" / "ai_ops.js").read_text()
CSS = (ROOT / "static" / "styles.css").read_text()


def test_live_summary_schema_is_supported():
    assert "recs.PENDING" in SOURCE
    assert '["CRITICAL", "HIGH", "MEDIUM", "LOW"]' in SOURCE
    assert "severityRows" in SOURCE
    assert "categoryRows" in SOURCE


def test_every_ai_route_has_structured_visual_recovery():
    for marker in (
        "Visual result",
        "Rows by database",
        "Retrieval methods",
        "Knowledge sources",
        "Run outcomes",
        "Trigger distribution",
        "Agent run timeline",
        "Replication inventory",
        "Standby synchronization",
    ):
        assert marker in SOURCE
        assert marker in BUNDLE


def test_partial_failures_and_loading_keep_layout():
    assert "Promise.allSettled" in SOURCE
    assert "AioPartial" in SOURCE
    assert "AioLoading" in SOURCE
    assert ".aio-skeleton-card" in CSS
    assert "prefers-reduced-motion" in CSS
    assert ".aio-partial" in CSS
