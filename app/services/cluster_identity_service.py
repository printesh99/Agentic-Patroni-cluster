"""Read-only verified cluster identity evidence collector."""
from __future__ import annotations

from datetime import datetime, timezone

from .. import sources as S
from . import evidence_service, inventory_service


def collect() -> dict:
    cfg = S.resolve_cluster_or_raise(S.CLUSTER_ID)
    cr = S.kubectl_json(["-n", cfg.namespace, "get", "postgrescluster", cfg.cluster_name])
    patroni = S.patroni_cluster()
    sql = S.sql_one("select system_identifier, timeline_id, current_setting('server_version') from pg_control_system(), pg_control_checkpoint()")
    members = patroni.get("members") or []
    leader = next((m.get("name") for m in members if str(m.get("role", "")).lower() in {"leader", "master", "primary"}), None)
    meta, status = cr.get("metadata") or {}, cr.get("status") or {}
    return {
        "configured_cluster_id": cfg.cluster_id, "configured_cluster_name": cfg.cluster_name,
        "namespace": cfg.namespace, "pgo_uid": meta.get("uid"),
        "pgo_resource_version": meta.get("resourceVersion"), "pgo_generation": meta.get("generation"),
        "pgo_observed_generation": status.get("observedGeneration"), "patroni_scope": patroni.get("scope"),
        "patroni_leader": leader, "system_identifier": sql[0] if sql else None,
        "timeline": int(sql[1]) if sql and sql[1] else None, "postgres_version": sql[2] if sql else None,
        "current_primary": leader, "collected_at": datetime.now(timezone.utc),
    }


def persist(db):
    inv = inventory_service.resolve(db)
    payload = collect(); payload["inventory_id"] = inv.id
    bundle = evidence_service.create_bundle(db)
    item = evidence_service.append_item(db, bundle, source_type="cluster_identity",
        source_name="pgo+patroni+postgres", collector_name="verified-cluster-identity",
        collector_version="v1", payload=payload, source_timestamp=payload["collected_at"])
    values = {payload.get("configured_cluster_name"), payload.get("patroni_scope")}
    values.discard(None)
    if len(values) > 1 and payload.get("patroni_scope") not in {payload.get("configured_cluster_name"), payload.get("configured_cluster_name") + "-ha"}:
        evidence_service.mark_contradictory(bundle, "configured cluster and Patroni scope conflict")
    db.flush()
    return bundle, item
