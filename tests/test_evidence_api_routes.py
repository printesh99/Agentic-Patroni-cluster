from app.main import app


def test_required_evidence_routes_exist():
    paths = {route.path for route in app.routes}
    assert "/api/v1/assistant/health" in paths
    assert "/api/v1/ai/health" in paths
    assert "/api/v1/log-analytics/summary" in paths
