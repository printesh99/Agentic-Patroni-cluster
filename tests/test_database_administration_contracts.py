import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text()


def test_python_sources_parse():
    for path in ("app/pg_admin.py", "app/api_admin.py", "app/api_compat.py", "app/api_actions.py"):
        ast.parse(text(path), filename=path)


def test_inventory_contracts_have_freshness_and_availability():
    admin = text("app/pg_admin.py")
    compat = text("app/api_compat.py")
    assert 'def build_databases(include_objects: bool = False)' in admin
    assert '"available": True' in admin and '"generated_at": _generated_at()' in admin
    assert 'def build_privileges(database: str = "postgres", role: str | None = None' in admin
    assert 'predicates.append(f"grantee = ' in admin
    assert 'predicates.append(f"table_schema = ' in admin
    assert '"table_type": r[3]' in compat
    assert '"estimated_rows": int(r[4])' in compat
    assert '"index_name": r[2]' in compat and '"table_name": r[1]' in compat
    assert '"comment": r[4] or None' in compat


def test_live_query_is_bounded_single_statement_and_read_only():
    actions = text("app/api_actions.py")
    assert 'if ";" in q or _WRITE_RE.search(q)' in actions
    assert 'limit = max(1, min(int(payload.get("row_limit", 200)), 1000))' in actions
    assert 'set default_transaction_read_only=on;' in actions
    assert '"read_only": True' in actions


def test_frontend_uses_direct_contract_without_credentials_or_sessions():
    source = text("static/admin.jsx")
    live = source[source.index("function LiveDatabaseConnectScreen("):]
    assert 'body: { database: database, query: query, row_limit: Number(maxRows) }' in live
    for forbidden in ("password", "writeMode", "/query", 'method: "DELETE"'):
        assert forbidden not in live
    assert 'usersRolesRequest("/api/users-roles/users")' not in source
    assert 'usersRolesRequest("/api/users-roles/roles")' not in source
    assert 'var tabState = React.useState("manage");' in source
    assert 'Console identity/password API not configured' in source


def test_compiled_admin_bundle_matches_fixed_contract():
    bundle = text("static/dist/admin.js")
    assert "/api/v1/live-connections" in bundle
    assert "Read-only enforced" in bundle
    assert "Console identity/password API not configured" in bundle
    assert "writeMode" not in bundle
