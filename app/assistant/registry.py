from __future__ import annotations

from types import MappingProxyType

from .contracts import SourceContract

REGISTRY_VERSION = "assistant-source-registry/v1"

_CONTRACTS = {
    "pg_stat_replication": SourceContract(
        name="pg_stat_replication",
        intent="replication_physical",
        transport="postgresql_read_only",
        evidence_contract="PhysicalReplicationEvidence/v1",
        required_fields=("primary_member", "patroni_ok", "standbys",
                         "logical_walsenders", "collected_at"),
        freshness_ttl_seconds=10,
        answer_obligations=("identify physical standbys", "report replay lag",
                            "exclude logical walsenders"),
    ),
    "pg_stat_archiver": SourceContract(
        name="pg_stat_archiver",
        intent="wal_archiver",
        transport="postgresql_read_only",
        evidence_contract="WalArchiverEvidence/v1",
        required_fields=("current_wal_segment", "current_wal_lsn",
                         "archived_count", "failed_count", "collected_at"),
        optional_fields=("last_archived_wal", "last_archived_time",
                         "last_failed_wal", "last_failed_time"),
        freshness_ttl_seconds=30,
        answer_obligations=("distinguish current WAL from archived WAL",),
    ),
}

SOURCE_REGISTRY = MappingProxyType(_CONTRACTS)


def get_source(name: str) -> SourceContract:
    try:
        return SOURCE_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"unregistered assistant source: {name}") from exc


def sources_for_intent(intent: str) -> tuple[SourceContract, ...]:
    return tuple(c for c in SOURCE_REGISTRY.values() if c.intent == intent)
