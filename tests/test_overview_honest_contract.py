import ast
from pathlib import Path
from unittest.mock import patch
import sys
import types

for dependency in ("httpx", "psycopg"):
    sys.modules.setdefault(dependency, types.ModuleType(dependency))

from app import cluster_model
from app import pg_overview

ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_overview_sources_parse() -> None:
    for path in ("app/cluster_model.py", "app/pg_metrics.py", "app/pg_overview.py"):
        ast.parse(_text(path), filename=path)


def test_capacity_classifies_primary_replica_wal_and_repository() -> None:
    pvc_doc = {"items": [
        {"metadata": {"name": "db-primary"}, "spec": {"storageClassName": "ceph"}, "status": {"capacity": {"storage": "100Gi"}}},
        {"metadata": {"name": "db-replica"}, "spec": {"storageClassName": "ceph"}, "status": {"capacity": {"storage": "100Gi"}}},
        {"metadata": {"name": "db-wal"}, "spec": {"storageClassName": "ceph"}, "status": {"capacity": {"storage": "20Gi"}}},
        {"metadata": {"name": "db-repo"}, "spec": {"storageClassName": "ceph"}, "status": {"capacity": {"storage": "50Gi"}}},
    ]}
    pod_doc = {"spec": {"volumes": [
        {"persistentVolumeClaim": {"claimName": "db-primary"}},
        {"persistentVolumeClaim": {"claimName": "db-wal"}},
    ]}}
    with patch.object(pg_overview.S, "primary_pod", return_value="primary"), patch.object(
        pg_overview.S, "kubectl_json", side_effect=[pvc_doc, pod_doc]
    ):
        result = pg_overview._capacity()
    assert result["available"] is True
    assert result["primary_data_available"] is True
    assert result["primary_data_gib"] == 100.0
    assert result["replicated_data_gib"] == 200.0
    assert result["wal_gib"] == 20.0
    assert result["repository_gib"] == 50.0


def test_capacity_reports_rbac_or_provider_failure_honestly() -> None:
    with patch.object(pg_overview.S, "kubectl_json", side_effect=pg_overview.S.SourceError("forbidden")):
        result = pg_overview._capacity()
    assert result["available"] is False
    assert result["volumes"] == []
    assert "forbidden" in result["error"]


def test_backup_provider_failure_is_returned_not_raised() -> None:
    with patch.object(pg_overview.pg_backups, "build_schedules", side_effect=pg_overview.S.SourceError("provider unavailable")):
        result = pg_overview._backup_metadata()
    assert result["schedules_available"] is False
    assert result["repository"]["available"] is False
    assert "provider unavailable" in result["schedules_error"]


def test_direct_sql_summary_has_no_docker_or_capacity_defaults() -> None:
    with patch.object(cluster_model.S, "sql_one", return_value=None), patch.object(
        cluster_model, "_pg_version", return_value="PostgreSQL"
    ):
        result = cluster_model._direct_sql_summary()
    assert result["leader"] is None
    assert result["cores"] is None
    assert result["ram_gib"] is None
    assert result["total_storage_gib"] is None
    assert "docker" not in str(result).lower()


def test_source_and_bundle_have_no_overview_seeded_defaults() -> None:
    forbidden = ("UAT · OpenShift", "Every 6 hours", "s3://pgbackrest-uae-uat", "total_storage_gib: 2048", "cores: 16", "ram_gib: 64")
    for path in ("static/overview.jsx", "static/dist/overview.js"):
        text = _text(path)
        for marker in forbidden:
            assert marker not in text
