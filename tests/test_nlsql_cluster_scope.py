from types import SimpleNamespace
from app import api_ai_v1


def test_nlsql_fans_out_to_application_databases(monkeypatch):
    calls = []
    def fake_sql(query, dbname="postgres", timeout=25):
        calls.append((dbname, query))
        if "from pg_database" in query: return [["app_a"], ["app_b"]]
        if "information_schema.columns" in query: return [["public.orders", "id, amount"]]
        return [['{"total": 2}']]
    monkeypatch.setattr(api_ai_v1.S, "sql", fake_sql)
    monkeypatch.setattr(api_ai_v1.ai_provider, "generate_rca", lambda *a, **k: SimpleNamespace(provider="test", model="test", available=True, content="select count(*) as total from public.orders", error=None))
    result = api_ai_v1._nlsql("count orders", 100)
    assert result["executed"] is True
    assert result["database_scope"] == "all-application-databases"
    assert result["columns"] == ["database", "total"]
    assert result["rows"] == [["app_a", 2], ["app_b", 2]]
    assert {db for db, query in calls if "row_to_json" in query} == {"app_a", "app_b"}


def test_nlsql_rejects_postgres_database(monkeypatch):
    monkeypatch.setattr(api_ai_v1.S, "sql", lambda *a, **k: [["app_a"]])
    result = api_ai_v1._nlsql("show tables", 100, "postgres")
    assert result["available"] is False
    assert "not a searchable" in result["error"]
