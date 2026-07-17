"""Derived ops data: readiness checks and threshold alerts from live signals."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import sources as S


def readiness() -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    items: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str, severity: str = "critical") -> None:
        key_map = {"PostgreSQL reachable": "database", "Patroni leader present": "patroni", "Standby streaming": "replication", "Prometheus reachable": "prometheus", "Postgres pods ready": "kubernetes", "Replication lag": "replication_lag"}
        items.append({"key": key_map.get(name, name.lower().replace(" ", "_")), "name": name, "label": name, "source": "live cluster", "status": "ok" if ok else severity,
                      "ok": ok, "detail": detail})

    # PostgreSQL reachable
    try:
        ver = S.sql_one("select current_setting('server_version')")
        check("PostgreSQL reachable", bool(ver), f"server_version={ver[0] if ver else '?'}")
    except S.SourceError as exc:
        check("PostgreSQL reachable", False, str(exc))

    # Patroni quorum / leader
    try:
        cl = S.patroni_cluster()
        members = cl.get("members", [])
        leader = next((m for m in members if m.get("role") == "leader"), None)
        check("Patroni leader present", leader is not None,
              f"leader={leader['name'] if leader else 'none'}")
        streaming = [m for m in members if m.get("state") == "streaming"]
        check("Standby streaming", bool(streaming),
              f"{len(streaming)} standby streaming", severity="warning")
    except S.SourceError as exc:
        check("Patroni leader present", False, str(exc))

    # Prometheus
    try:
        check("Prometheus reachable", S.prom_up(), "metrics endpoint responding")
    except S.SourceError as exc:
        check("Prometheus reachable", False, str(exc), "warning")

    # Kubernetes pods
    try:
        pods = S.pods()
        pg = [p for p in pods if p["role"] in ("master", "replica")]
        ready = all(p["ready_bool"] for p in pg)
        check("Postgres pods ready", ready,
              f"{sum(p['ready_bool'] for p in pg)}/{len(pg)} ready")
    except S.SourceError as exc:
        check("Postgres pods ready", False, str(exc))

    # Replication lag
    try:
        lag = S.sql_one(
            "select coalesce(max(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)),0)::bigint "
            "from pg_stat_replication")
        lag_bytes = int(lag[0]) if lag else 0
        check("Replication lag", lag_bytes < 16 * 1024 * 1024,
              f"max replay lag {lag_bytes} bytes", "warning")
    except S.SourceError as exc:
        check("Replication lag", False, str(exc), "warning")

    crit = sum(1 for i in items if not i["ok"] and i["status"] == "critical")
    warn = sum(1 for i in items if not i["ok"] and i["status"] == "warning")
    score = round(100 * sum(1 for i in items if i["ok"]) / max(1, len(items)))
    return {
        "source": "live cluster",
        "available": True,
        "generated_at": generated_at,
        "checked_at": generated_at,
        "items": items,
        "summary": {"score": score, "ok": sum(1 for i in items if i["ok"]), "critical": crit, "warnings": warn,
                    "total": len(items),
                    "status": "critical" if crit else "warning" if warn else "ok"},
    }


def alerts() -> dict[str, Any]:
    """Threshold checks derived from live signals (no Alertmanager wiring yet)."""
    out: list[dict[str, Any]] = []

    def add(name: str, severity: str, detail: str) -> None:
        out.append({"id": name, "name": name, "severity": severity,
                    "detail": detail, "state": "firing", "source": "derived"})

    try:
        conns = S.sql_one(
            "select count(*), (select setting::int from pg_settings where name='max_connections') "
            "from pg_stat_activity")
        if conns:
            used, mx = int(conns[0]), int(conns[1])
            if mx and used / mx > 0.8:
                add("connection_saturation", "warning",
                    f"{used}/{mx} connections (>80%)")
    except S.SourceError:
        pass

    try:
        idletx = S.sql_one(
            "select count(*) from pg_stat_activity where state='idle in transaction' "
            "and now()-state_change > interval '5 min'")
        if idletx and int(idletx[0]) > 0:
            add("long_idle_in_transaction", "warning",
                f"{idletx[0]} sessions idle-in-transaction >5m")
    except S.SourceError:
        pass

    try:
        lag = S.sql_one(
            "select coalesce(max(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)),0)::bigint "
            "from pg_stat_replication")
        if lag and int(lag[0]) > 16 * 1024 * 1024:
            add("replication_lag_high", "critical", f"replay lag {lag[0]} bytes")
    except S.SourceError:
        pass

    return {"source": "derived thresholds", "available": True, "alerts": out,
            "summary": {"firing": len(out),
                        "status": "ok" if not out else "firing"}}
