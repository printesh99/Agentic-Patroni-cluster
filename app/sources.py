"""Live data sources for the PostgreSQL Enterprise Console backend.

Three primitives, chosen for robustness against a local kind cluster:

* ``k8s``  - shells out to ``kubectl`` for pod/topology state (read-only).
* ``sql``  - runs SQL through ``kubectl exec ... psql`` on the primary pod.
             This avoids the flaky ``kubectl port-forward`` to a single pod.
* ``prom`` - queries Prometheus over a stable HTTP port-forward.

Everything here is synchronous and cached briefly; FastAPI runs the calls in a
threadpool so the event loop is never blocked.

--------------------------------------------------------------------------
Multi-cluster note (added 2026-07-07)
--------------------------------------------------------------------------
This console addresses multiple logical clusters (uat/prod/dr/...) via a
``cluster_id`` URL path segment, but until now every setting below (NS,
CLUSTER_NAME, PROM_URL, ...) was a single module-level constant read once
from the process environment — ``cluster_id`` was accepted by route
handlers and never actually used. Selecting "prod" in the UI silently kept
showing whichever cluster this deployment's env vars pointed at (see
memory: object-monitor-cluster-id-cosmetic-bug).

Fix: settings now live in a ``ClusterConfig`` per cluster_id
(``CLUSTER_REGISTRY``), selected per-request via a ``contextvars.ContextVar``
that FastAPI route dependencies set from the URL's ``cluster_id``
(``cluster_path_dependency``, attached as a router-level dependency).
``contextvars`` specifically (not a plain global or thread-local) because
this codebase runs blocking source calls via ``anyio.to_thread.run_sync``
(see ``app/threads.py``) — anyio copies the calling task's context into the
worker thread, so a value set by a dependency during request handling is
correctly visible inside the threaded pg_*/S.* calls, without bleed-over
from concurrent requests. A plain global would leak across concurrent
requests; a thread-local would go missing (thread pool workers are reused
per-call, not per-request).

``NS``, ``CLUSTER_NAME``, ``CLUSTER_ID``, ``PROM_URL``, ``DB_CONTAINER``,
``DIRECT_SQL``, ``CTX``, and ``KUBECTL`` are kept as module attributes for
backward compatibility with the ~30 files that do ``from . import sources
as S`` and read ``S.NS`` etc. — but they are now computed dynamically per
access (via module ``__getattr__``, PEP 562) from the active
``ClusterConfig`` instead of frozen at import time. The ONE place in the
codebase that read one of these into a module-level constant at import
time (``app/api_clusters.py``'s old ``_METRIC_PROMQL`` dict) was fixed
separately to format its PromQL per-call instead.

Only the DEFAULT cluster (whatever this deployment's env vars already
described) is guaranteed reachable via the kubectl/exec path — additional
registry entries for clusters living on a DIFFERENT physical OpenShift
cluster than this pod (e.g. prod-pgcluster-uae on ocp-prod, when this pod
runs on ocp-dr) CANNOT use kubectl/oc at all (no cross-cluster kubeconfig
is provisioned) and must set ``direct_sql=True`` with a real, network
-reachable Postgres endpoint (e.g. a LoadBalancer primary-lb IP) — pods()/
patroni_cluster() will raise SourceError for those until real cross-cluster
k8s access exists, which is intentional: a loud, correct failure instead of
silently serving the wrong cluster's pod list.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import psycopg

logger = logging.getLogger(__name__)


def _env(*names: str, default: str = "") -> str:
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return default


# Kubernetes CLI (kubectl locally, oc on OpenShift). Not cluster-specific —
# the same binary talks to whichever cluster its kubeconfig/context/in-cluster
# ServiceAccount points at.
K8S_CLI = _env("PGC_K8S_CLI", "K8S_CLI", default="kubectl")


class SourceError(RuntimeError):
    """Raised when an upstream (kubectl / psql / prometheus) call fails."""


class ClusterResolutionError(RuntimeError):
    """Base error for safe, credential-free cluster resolution failures."""


class UnknownClusterError(ClusterResolutionError):
    pass


class DisabledClusterError(ClusterResolutionError):
    pass


class IncompleteClusterConfigError(ClusterResolutionError):
    pass


# --------------------------------------------------------------------------
# Per-cluster configuration
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ClusterConfig:
    cluster_id: str
    cluster_name: str
    namespace: str
    prom_url: str
    db_container: str = "database"
    direct_sql: bool = False
    ctx: str = ""  # kubeconfig context; empty = in-cluster ServiceAccount
    enabled: bool = True
    # Only used when direct_sql=True (cross-cluster / no kubectl exec path):
    pghost: str = "db"
    pgport: int = 5432
    pguser: str = "postgres"
    pgpassword: str = ""
    pgsslmode: str = "disable"
    pgdatabase: str = "postgres"
    # Loki queries use `namespace` unless overridden here.
    loki_namespace: str = ""

    def resolved_loki_namespace(self) -> str:
        return self.loki_namespace or self.namespace


def _default_config() -> ClusterConfig:
    """The cluster this deployment's environment variables describe — same
    aliases/defaults as before this refactor, so an unmodified deployment
    (no CLUSTER_REGISTRY entries touched) behaves identically to pre-refactor."""
    direct_sql = (
        os.environ.get("PGC_SQL_DIRECT", "").lower() in {"1", "true", "yes", "on"}
        or _env("PGC_CONTEXT", "K8S_CONTEXT", default="") in {"local-empty", "direct"}
    )
    return ClusterConfig(
        cluster_id=_env("PGC_CLUSTER_ID", "LOCAL_CLUSTER_ID", default="prod"),
        cluster_name=_env("PGC_CLUSTER", "CLUSTER_NAME", default="prod-pgcluster-uae"),
        namespace=_env("PGC_NAMESPACE", "K8S_NAMESPACE", "NAMESPACE", default="prod-pgcluster-uae-local"),
        prom_url=_env("PGC_PROM_URL", "PROMETHEUS_URL", "PROMETHEUS_BASE_URL", default="http://localhost:9090"),
        db_container=os.environ.get("PGC_DB_CONTAINER", "database"),
        direct_sql=direct_sql,
        ctx=_env("PGC_CONTEXT", "K8S_CONTEXT", default=""),
        pghost=_env("MONITOR_PGHOST", "PGHOST", default="db"),
        pgport=int(_env("MONITOR_PGPORT", "PGPORT", default="5432")),
        pguser=_env("MONITOR_PGUSER", "PGUSER", default="postgres"),
        pgpassword=_env("MONITOR_PGPASSWORD", "PGPASSWORD", default=""),
        pgsslmode=_env("MONITOR_PGSSLMODE", "PGSSLMODE", default="disable"),
        pgdatabase=_env("MONITOR_PGDATABASE", "PGDATABASE", default="postgres"),
    )


_DEFAULT_CONFIG = _default_config()

# Additional cluster_id -> ClusterConfig entries this console can serve.
# Populate real entries here (and flip enabled=True) once a cluster's real
# connection details are confirmed reachable from THIS pod. See the module
# docstring above for why cross-physical-cluster entries need direct_sql.
#
# "dr" values below are NOT a guess — they're the same namespace/Prometheus
# service names already used successfully in
# grafana-dashboards/deploy_all_clusters.py's CLUSTERS registry (dr entry:
# grafana_url on ocp-dr, prom_url http://dr-pgo18-prometheus.dr-pgcluster-uae.svc:9090).
# dr-pgcluster-uae lives on ocp-dr, the SAME physical cluster this pod runs
# on, so kubectl-exec (direct_sql=False) should work in principle — but
# left disabled until someone confirms (a) the `monitoring` namespace's
# ServiceAccount has RBAC to exec/get pods in dr-pgcluster-uae (cross
# -namespace kubectl exec needs a RoleBinding there, same class of gap as
# the region-build RBAC issues in other memories), and (b) no NetworkPolicy
# blocks monitoring -> dr-pgcluster-uae on 9090 (this cluster is known to
# use NetworkPolicies to restrict cross-namespace traffic — see
# object-monitor-grafana-exporter-import memory's allow-uat-pg-inspector
# -to-object-monitor policy for a precedent).
#
# NOTE: _DEFAULT_CONFIG is merged in LAST below (not first) so it always wins
# its own key. Its cluster_id defaults to "prod" (PGC_CLUSTER_ID's own code
# default, see _default_config()) whenever that env var isn't set — the SAME
# string as the "prod" placeholder key below. Inserting the default first
# would let the placeholder silently clobber it whenever PGC_CLUSTER_ID is
# unset, breaking the actually-working default cluster.
CLUSTER_REGISTRY: dict[str, ClusterConfig] = {
    "dr": ClusterConfig(
        cluster_id="dr",
        cluster_name="dr-pgcluster-uae",
        namespace="dr-pgcluster-uae",
        prom_url="http://dr-pgo18-prometheus.dr-pgcluster-uae.svc:9090",
        direct_sql=False,
        enabled=False,  # verify RBAC + NetworkPolicy reachability first, see comment above
    ),
    # prod-pgcluster-uae lives on ocp-prod — a DIFFERENT physical cluster
    # than this pod (ocp-dr) — so kubectl-exec is categorically impossible
    # here (no cross-cluster kubeconfig is provisioned). Must use
    # direct_sql with a real, externally-reachable Postgres endpoint (e.g.
    # a LoadBalancer primary-lb IP, NOT the internal
    # prod-pgo18-prometheus.prod-pgcluster-uae.svc ClusterIP DNS name that
    # deploy_all_clusters.py uses — that only resolves/routes from inside
    # ocp-prod's own pod network) and a Prometheus URL that's also
    # cross-cluster reachable (likely needs an exposed Route on ocp-prod,
    # unless/until a federated Thanos endpoint is set up).
    #
    # Auto-enables once real values are supplied via env (Secret), so
    # turning this on is an ops action (set the env vars + roll the
    # deployment), not a code change:
    #   PROD_MONITOR_PGHOST / PROD_MONITOR_PGPORT / PROD_MONITOR_PGUSER /
    #   PROD_MONITOR_PGPASSWORD / PROD_MONITOR_PGSSLMODE / PROD_PROM_URL
    "prod": ClusterConfig(
        cluster_id="prod",
        cluster_name="prod-pgcluster-uae",
        namespace="prod-pgcluster-uae",
        prom_url=_env("PROD_PROM_URL", default=""),
        direct_sql=True,
        pghost=_env("PROD_MONITOR_PGHOST", default=""),
        pgport=int(_env("PROD_MONITOR_PGPORT", default="5555")),
        pguser=_env("PROD_MONITOR_PGUSER", default="dbuser_monitor"),
        pgpassword=_env("PROD_MONITOR_PGPASSWORD", default=""),
        pgsslmode=_env("PROD_MONITOR_PGSSLMODE", default="require"),
        enabled=bool(_env("PROD_MONITOR_PGHOST", default="")),
    ),
    _DEFAULT_CONFIG.cluster_id: _DEFAULT_CONFIG,
}

_active_cluster_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_cluster_id", default=None
)


def activate_cluster(cluster_id: str) -> contextvars.Token[str | None]:
    """Set the active cluster for the remainder of this request/task context."""
    resolve_cluster_or_raise(cluster_id)
    return _active_cluster_id.set(cluster_id)


def reset_active_cluster(token: contextvars.Token[str | None]) -> None:
    _active_cluster_id.reset(token)


def _configuration_gaps(cfg: ClusterConfig) -> list[str]:
    gaps = [name for name, value in (
        ("cluster_id", cfg.cluster_id), ("cluster_name", cfg.cluster_name),
        ("namespace", cfg.namespace),
    ) if not str(value or "").strip()]
    if cfg.direct_sql:
        for name, value in (("pghost", cfg.pghost), ("pguser", cfg.pguser)):
            if not str(value or "").strip():
                gaps.append(name)
        if not (1 <= int(cfg.pgport) <= 65535):
            gaps.append("pgport")
    elif not str(cfg.db_container or "").strip():
        gaps.append("db_container")
    return gaps


def resolve_cluster_or_raise(cluster_id: str) -> ClusterConfig:
    """Resolve an explicit cluster without ever substituting another cluster."""
    cfg = CLUSTER_REGISTRY.get(cluster_id)
    if cfg is None:
        raise UnknownClusterError(f"unknown cluster_id {cluster_id!r}")
    if not cfg.enabled:
        raise DisabledClusterError(f"cluster_id {cluster_id!r} is disabled")
    gaps = _configuration_gaps(cfg)
    if gaps:
        raise IncompleteClusterConfigError(
            f"cluster_id {cluster_id!r} has incomplete configuration: {', '.join(sorted(gaps))}"
        )
    return cfg


def resolve_cluster_name(value: str | None) -> str | None:
    """Map a cluster_id (``CLUSTER_REGISTRY`` key, e.g. ``uat``) — or an
    already-canonical cluster_name — to the canonical ``cluster_name`` rows are
    persisted under. Agent recommendations/audit store ``S.CLUSTER_NAME`` (the
    full name, e.g. ``uat-pgcluster-uae``), but frontends filter by the short
    cluster_id, which would never ``==`` the stored value, silently emptying the
    panel. Unknown values pass through unchanged so an explicit cluster_name
    filter still works."""
    if not value:
        return value
    cfg = CLUSTER_REGISTRY.get(value)
    if cfg is not None:
        return cfg.cluster_name
    for cfg in CLUSTER_REGISTRY.values():
        if cfg.cluster_name == value:
            return cfg.cluster_name
    return value


async def cluster_path_dependency(cluster_id: str | None = None) -> AsyncGenerator[ClusterConfig | None, None]:
    """FastAPI dependency: activates the cluster named by the request's
    {cluster_id} path segment. Attach via
    ``APIRouter(..., dependencies=[Depends(cluster_path_dependency)])``.
    No-ops for routes without a {cluster_id} path segment (cluster_id is
    None in that case, since FastAPI then treats it as an absent optional
    query param rather than a path param)."""
    if not cluster_id:
        yield None
        return
    cfg = resolve_cluster_or_raise(cluster_id)
    token = _active_cluster_id.set(cfg.cluster_id)
    try:
        yield cfg
    finally:
        _active_cluster_id.reset(token)


def _current() -> ClusterConfig:
    cid = _active_cluster_id.get()
    if cid is None:
        return _DEFAULT_CONFIG
    return resolve_cluster_or_raise(cid)


def _kubectl_prefix(cfg: ClusterConfig) -> list[str]:
    return [K8S_CLI] + (["--context", cfg.ctx] if cfg.ctx else [])


# --------------------------------------------------------------------------
# Dynamic module attributes (PEP 562) — backward compatible with `S.NS` etc.
# --------------------------------------------------------------------------
_DYNAMIC_ATTRS = {
    "NS": lambda cfg: cfg.namespace,
    "CLUSTER_NAME": lambda cfg: cfg.cluster_name,
    "CLUSTER_ID": lambda cfg: cfg.cluster_id,
    "PROM_URL": lambda cfg: cfg.prom_url,
    "DB_CONTAINER": lambda cfg: cfg.db_container,
    "DIRECT_SQL": lambda cfg: cfg.direct_sql,
    "CTX": lambda cfg: cfg.ctx,
    "KUBECTL": lambda cfg: _kubectl_prefix(cfg),
}


def __getattr__(name: str) -> Any:
    factory = _DYNAMIC_ATTRS.get(name)
    if factory is not None:
        return factory(_current())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --------------------------------------------------------------------------
# kubectl helpers
# --------------------------------------------------------------------------
def _run(args: list[str], timeout: int = 20) -> str:
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise SourceError(f"{K8S_CLI} not found — install it and set PGC_K8S_CLI/PGC_CONTEXT: {exc}") from exc
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        raise SourceError(f"timeout running {' '.join(args[:4])}…") from exc
    if out.returncode != 0:
        raise SourceError(out.stderr.strip() or f"command failed: {args}")
    return out.stdout


def kubectl_json(args: list[str], timeout: int = 20) -> dict[str, Any]:
    raw = _run(_kubectl_prefix(_current()) + args + ["-o", "json"], timeout=timeout)
    return json.loads(raw)


# Short TTL cache so a single page render (many endpoints) hits kubectl once.
# Keyed per cluster_id — different clusters have different pod lists.
_pods_cache: dict[str, dict[str, Any]] = {}


def pods(ttl: float = 4.0) -> list[dict[str, Any]]:
    """Return the cluster's pods with their Patroni role, normalised."""
    cfg = _current()
    now = time.time()
    cached = _pods_cache.get(cfg.cluster_id)
    if cached is not None and now - cached["ts"] < ttl:
        return cached["data"]
    doc = kubectl_json(["-n", cfg.namespace, "get", "pods", "-l",
                        f"postgres-operator.crunchydata.com/cluster={cfg.cluster_name}"])
    result = []
    for item in doc.get("items", []):
        meta = item["metadata"]
        labels = meta.get("labels", {})
        status = item.get("status", {})
        cstats = status.get("containerStatuses", []) or []
        ready = sum(1 for c in cstats if c.get("ready"))
        restarts = sum(c.get("restartCount", 0) for c in cstats)
        containers = []
        for container in cstats:
            last_terminated = (container.get("lastState") or {}).get("terminated") or {}
            current_terminated = (container.get("state") or {}).get("terminated") or {}
            terminated = last_terminated or current_terminated
            containers.append({
                "name": container.get("name"),
                "ready": bool(container.get("ready")),
                "restart_count": int(container.get("restartCount") or 0),
                "last_termination": {
                    "reason": terminated.get("reason"),
                    "exit_code": terminated.get("exitCode"),
                    "started_at": terminated.get("startedAt"),
                    "finished_at": terminated.get("finishedAt"),
                } if terminated else None,
            })
        result.append({
            "name": meta["name"],
            "role": labels.get("postgres-operator.crunchydata.com/role", ""),
            "instance": labels.get("postgres-operator.crunchydata.com/instance", ""),
            "node": item["spec"].get("nodeName", ""),
            "phase": status.get("phase", ""),
            "ready": f"{ready}/{len(cstats)}" if cstats else "0/0",
            "ready_bool": bool(cstats) and ready == len(cstats),
            "restarts": restarts,
            "start_time": status.get("startTime"),
            "containers": containers,
        })
    _pods_cache[cfg.cluster_id] = {"ts": now, "data": result}
    return result


def kubernetes_events(limit: int = 30) -> list[dict[str, Any]]:
    """Return bounded, sanitized events for this PostgreSQL cluster."""
    cfg = _current()
    doc = kubectl_json(["-n", cfg.namespace, "get", "events"], timeout=20)
    rows: list[dict[str, Any]] = []
    cluster_prefix = cfg.cluster_name
    for item in doc.get("items", []):
        involved = item.get("involvedObject") or {}
        name = str(involved.get("name") or "")
        message = str(item.get("message") or "")
        if cluster_prefix not in name and cluster_prefix not in message:
            continue
        rows.append({
            "timestamp": item.get("eventTime") or item.get("lastTimestamp") or item.get("firstTimestamp"),
            "type": item.get("type"), "reason": item.get("reason"),
            "kind": involved.get("kind"), "name": name,
            "count": int(item.get("count") or 1),
            "message": message[:500],
        })
    rows.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=True)
    return rows[:min(max(int(limit), 1), 100)]


def primary_pod() -> str:
    for p in pods():
        if p["role"] == "master" and p["phase"] == "Running":
            return p["name"]
    raise SourceError("no running primary (role=master) pod found")


# --------------------------------------------------------------------------
# SQL via kubectl exec (robust; no port-forward) or direct psycopg
# --------------------------------------------------------------------------
def _direct_sql(cfg: ClusterConfig, query: str, dbname: str = "postgres", timeout: int = 25) -> list[list[str]]:
    connect_db = dbname or cfg.pgdatabase
    try:
        with psycopg.connect(host=cfg.pghost, port=cfg.pgport, dbname=connect_db, user=cfg.pguser,
                             password=cfg.pgpassword, sslmode=cfg.pgsslmode,
                             connect_timeout=min(timeout, 10)) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                if cur.description is None:
                    return []
                return [["" if v is None else str(v) for v in row] for row in cur.fetchall()]
    except Exception as exc:
        raise SourceError(f"direct postgres query failed: {exc}") from exc


def sql(query: str, dbname: str = "postgres", timeout: int = 25) -> list[list[str]]:
    """Run a query and return rows as lists of string fields."""
    cfg = _current()
    if cfg.direct_sql:
        return _direct_sql(cfg, query, dbname=dbname, timeout=timeout)
    sep = "\x1f"
    args = _kubectl_prefix(cfg) + [
        "-n", cfg.namespace, "exec", primary_pod(), "-c", cfg.db_container, "--",
        "psql", "-d", dbname, "-tAF", sep, "-c", query,
    ]
    raw = _run(args, timeout=timeout)
    rows = []
    for line in raw.splitlines():
        if line == "":
            continue
        rows.append(line.split(sep))
    return rows


def sql_one(query: str, dbname: str = "postgres") -> list[str] | None:
    rows = sql(query, dbname=dbname)
    return rows[0] if rows else None


# --------------------------------------------------------------------------
# Prometheus
# --------------------------------------------------------------------------
def prom_query(expr: str) -> list[dict[str, Any]]:
    prom_url = _current().prom_url
    try:
        r = httpx.get(f"{prom_url}/api/v1/query", params={"query": expr}, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceError(f"prometheus query failed: {exc}") from exc
    body = r.json()
    if body.get("status") != "success":
        raise SourceError(f"prometheus error: {body.get('error')}")
    return body["data"]["result"]


def prom_scalar(expr: str, default: float | None = None) -> float | None:
    res = prom_query(expr)
    if not res:
        return default
    try:
        return float(res[0]["value"][1])
    except (KeyError, IndexError, ValueError):
        return default


def prom_range(expr: str, minutes: int = 60, step: str = "60s") -> list[list[Any]]:
    """Return [[unix_ts, float], …] for a single-series range query."""
    prom_url = _current().prom_url
    end = time.time()
    start = end - minutes * 60
    try:
        r = httpx.get(f"{prom_url}/api/v1/query_range",
                      params={"query": expr, "start": start, "end": end, "step": step},
                      timeout=15)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceError(f"prometheus range failed: {exc}") from exc
    body = r.json()
    result = body.get("data", {}).get("result", [])
    if not result:
        return []
    return [[float(ts), float(v)] for ts, v in result[0]["values"]]


# Cache "is prometheus up" forever per-cluster once checked (same semantics
# as the old @lru_cache(maxsize=1), now correctly scoped per cluster_id
# instead of accidentally shared across every cluster).
_prom_up_cache: dict[str, bool] = {}


def prom_up() -> bool:
    cfg = _current()
    if cfg.cluster_id in _prom_up_cache:
        return _prom_up_cache[cfg.cluster_id]
    try:
        result = bool(prom_query("up"))
    except SourceError:
        result = False
    _prom_up_cache[cfg.cluster_id] = result
    return result


# --------------------------------------------------------------------------
# Patroni REST (via kubectl exec curl on the primary; no extra port-forward)
# --------------------------------------------------------------------------
def patroni_cluster(timeout: int = 15) -> dict[str, Any]:
    """Return Patroni's /cluster document (members, scope, …)."""
    cfg = _current()
    args = _kubectl_prefix(cfg) + [
        "-n", cfg.namespace, "exec", primary_pod(), "-c", cfg.db_container, "--",
        "bash", "-lc",
        "curl -sk https://localhost:8008/cluster || curl -s http://localhost:8008/cluster",
    ]
    raw = _run(args, timeout=timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError("could not parse Patroni /cluster response") from exc


def _patroni_document(path: str, timeout: int = 15) -> Any:
    if path not in {"history", "patroni"}:
        raise ValueError("unsupported Patroni document")
    cfg = _current()
    args = _kubectl_prefix(cfg) + [
        "-n", cfg.namespace, "exec", primary_pod(), "-c", cfg.db_container, "--",
        "bash", "-lc",
        f"curl -sk https://localhost:8008/{path} || curl -s http://localhost:8008/{path}",
    ]
    raw = _run(args, timeout=timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError(f"could not parse Patroni /{path} response") from exc


def patroni_history(timeout: int = 15) -> list[Any]:
    payload = _patroni_document("history", timeout)
    if not isinstance(payload, list):
        raise SourceError("Patroni /history response is not a list")
    return payload


def patroni_status(timeout: int = 15) -> dict[str, Any]:
    payload = _patroni_document("patroni", timeout)
    if not isinstance(payload, dict):
        raise SourceError("Patroni /patroni response is not an object")
    return payload
