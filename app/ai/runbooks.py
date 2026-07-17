"""Built-in runbook seed set for Phase 6."""
from __future__ import annotations

RUNBOOKS = [
    ("runbook_pgbackrest_archive_failure", "pgBackRest archive failure",
     "Check pgBackRest stanza status, archive-push errors, repo reachability, S3 endpoint health, credentials, and WAL backlog. Safe checks: pgbackrest info, archive status, recent PostgreSQL logs. Approval required before deleting WAL, changing archive_command, restarting pods, or restoring."),
    ("runbook_replication_lag", "Replication lag triage",
     "Check pg_stat_replication, replica state, WAL receiver logs, network errors, checkpoint pressure, and slot retention. Safe checks are read-only. Approval required before switchover, failover, restart, pg_rewind, or slot drop."),
    ("runbook_wal_disk_full", "WAL disk pressure",
     "Inspect WAL PVC usage, archive failures, replication slots, and WAL generation rate. Safe checks: pg_replication_slots and pg_stat_archiver. Approval required before dropping slots or deleting files."),
    ("runbook_connection_exhaustion", "Connection exhaustion",
     "Check pg_stat_activity by state, PgBouncer pools, waiters, and application bursts. Approval required before killing sessions or changing max_connections."),
    ("runbook_pgbouncer_exhaustion", "PgBouncer pool exhaustion",
     "Check pool active/waiting clients, server connections, app names, and backend reachability. Approval required before reload/restart or pool config changes."),
    ("runbook_lock_contention", "Lock contention and deadlocks",
     "Check pg_locks, blocking PIDs, long transactions, and application owners. Approval required before terminating sessions."),
    ("runbook_patroni_failover", "Patroni failover/switchover",
     "Check Patroni leader, DCS health, sync replica, timeline, replication lag, and backups. Switchover/failover is L5 and requires multi-level approval."),
    ("runbook_logical_replication", "Logical replication troubleshooting",
     "Check subscriptions, slot activity, confirmed_flush_lsn, retained WAL, apply worker errors, and subscriber reachability. Slot drop requires approval."),
    ("runbook_general_dba_triage", "General DBA triage",
     "Start with readiness, recent critical logs, rule findings, ML score, and forecast risk. Prefer read-only diagnostics and escalate destructive actions."),
]
