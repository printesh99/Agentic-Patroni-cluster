from app import pg_security


def test_pg_bool_accepts_driver_and_psql_representations():
    for value in (True, "true", "TRUE", "t", "1", "on", "yes"):
        assert pg_security._pg_bool(value) is True
    for value in (False, "false", "FALSE", "f", "0", "off", "no", "", None):
        assert pg_security._pg_bool(value) is False


def test_auth_parses_direct_psycopg_boolean_text(monkeypatch):
    def fake_sql(query):
        if "from pg_roles" in query:
            return [["app", "true", "false", "false", "false", "false", ""]]
        if "from pg_hba_file_rules" in query:
            return []
        return [["password_encryption", "scram-sha-256"], ["ssl", "on"]]

    monkeypatch.setattr(pg_security.S, "sql", fake_sql)
    payload = pg_security.build_auth()

    assert payload["roles"][0]["rolcanlogin"] is True
    assert payload["summary"]["login_roles"] == 1


def test_tls_parses_direct_psycopg_boolean_text(monkeypatch):
    def fake_sql(query):
        if "from pg_stat_ssl" in query:
            return [
                ["1", "app", "driver", "true", "TLSv1.3", "cipher", "256", ""],
                ["2", "app", "driver", "false", "", "", "0", ""],
            ]
        return [["ssl", "on"], ["ssl_min_protocol_version", "TLSv1.2"]]

    monkeypatch.setattr(pg_security.S, "sql", fake_sql)
    payload = pg_security.build_tls()

    assert payload["summary"]["ssl_sessions"] == 1
    assert payload["summary"]["non_ssl_sessions"] == 1
    assert payload["summary"]["protocols"] == ["TLSv1.3"]
