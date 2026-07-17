import json

from app import log_parse


def test_viaq_envelope_decoded_and_classified():
    body = "2026-07-11 11:00:48 UTC [42] postgres@postgres/ LOG: database system is ready"
    line = json.dumps({"message": body, "kubernetes": {"container_name": "database"}})
    message, envelope = log_parse._decode_body(line)
    assert message == body
    assert envelope["message"] == body
    record = log_parse.normalize_entry({"k8s_container_name": "database"}, "1783767648000000000", line)
    assert record["component"] == "postgres"
    assert record["level"] == "LOG"
    assert record["message"] == body
    assert record["raw"] == line


def test_body_parser_components():
    assert log_parse.parse_body("2026-07-11 LOG: ready", "database") == ("postgres", "LOG")
    assert log_parse.parse_body("2026-07-11 INFO: Lock owner: node-a", "database") == ("patroni", "INFO")
    assert log_parse.parse_body("client ERROR: login failed", "pgbouncer") == ("pgbouncer", "ERROR")


def test_selector_never_uses_nonexistent_level_label():
    query = log_parse.build_query(components=["database"], levels=["ERROR"], line_regex="(?i)starting")
    assert 'k8s_container_name=~"database"' in query
    assert "level=~" not in query
    assert '|~ "(?i)starting"' in query


def test_normalized_fields_support_body_aggregation():
    record = log_parse.normalize_entry(
        {"k8s_container_name": "database"}, "1783767648000000000",
        "2026-07-16 ERROR: connection failed",
    )
    assert record["level"] == "ERROR"
    assert record["severity"] == "error"
    assert record["component"] == "postgres"
