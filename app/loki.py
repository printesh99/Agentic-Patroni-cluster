"""Loki LogQL client for the log pillar.

Thin synchronous HTTP wrapper around Loki's query API, mirroring the style of
``sources.py`` (sync ``httpx``; FastAPI runs these in a threadpool).

Two deployment shapes are supported by the same image:

* **Local/dev** — a plain single-tenant Loki at ``http://localhost:3100``. No
  auth, no TLS. Leave the extra env unset and this behaves exactly as before.
* **OpenShift LokiStack gateway** (production, ns ``monitoring`` -> ns
  ``openshift-logging``) — the multitenant ``logging-loki-gateway-http`` service
  on :8080. The tenant is IN THE PATH
  (``/api/logs/v1/application/loki/api/v1/...``) and every request must carry:
    - ``Authorization: Bearer <serviceaccount token>`` — the gateway runs an
      OpenShift SubjectAccessReview; SA ``monitoring:object-monitor`` is granted
      ``get`` on ``application/logs`` via ClusterRole
      ``objmon-loki-application-logs-view``.
    - TLS trust of the service-serving cert (``service-ca.crt``).
    - ``X-Scope-OrgID: application`` (harmless/ignored by single-tenant Loki).
  The SA token rotates (projected token, ~1h), so it is read fresh per request.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from .sources import SourceError

# Accept several aliases so the same image works under the local compose env
# (LOKI_URL / LOKI_BASE_URL) and the PGC_* naming. First non-empty wins.
# In production set this to the gateway tenant base, e.g.
#   https://logging-loki-gateway-http.openshift-logging.svc.cluster.local:8080/api/logs/v1/application
LOKI_URL = (
    os.environ.get("PGC_LOKI_URL")
    or os.environ.get("LOKI_URL")
    or os.environ.get("LOKI_BASE_URL")
    or "http://localhost:3100"
)

# Multitenant tenant id -> X-Scope-OrgID. Empty = send no header (legacy).
LOKI_ORG_ID = (
    os.environ.get("PGC_LOKI_ORG_ID")
    or os.environ.get("LOKI_ORG_ID")
    or os.environ.get("LOKI_TENANT_ID")
    or ""
)
_STATIC_HEADERS: dict[str, str] = {"X-Scope-OrgID": LOKI_ORG_ID} if LOKI_ORG_ID else {}

# Bearer token file. In-cluster this is the projected SA token; absent in dev.
LOKI_TOKEN_FILE = (
    os.environ.get("LOKI_BEARER_TOKEN_FILE")
    or os.environ.get("PGC_LOKI_TOKEN_FILE")
    or "/var/run/secrets/kubernetes.io/serviceaccount/token"
)

# CA bundle for verifying the gateway TLS cert. Explicit env wins; otherwise
# auto-detect the in-pod service-serving CA (what the gateway cert is signed by),
# then the kube CA, else fall back to system trust (True).
_CA_ENV = os.environ.get("LOKI_CA_FILE") or os.environ.get("PGC_LOKI_CA_FILE") or ""


def _resolve_verify() -> Any:
    if _CA_ENV:
        return _CA_ENV if os.path.exists(_CA_ENV) else True
    for p in (
        "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt",
        "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
    ):
        if os.path.exists(p):
            return p
    return True


_VERIFY: Any = _resolve_verify()

# Nanoseconds per second — Loki timestamps are unix-nanoseconds.
NS_PER_S = 1_000_000_000


def _bearer() -> dict[str, str]:
    """Fresh Authorization header from the SA token file (rotates ~hourly)."""
    try:
        with open(LOKI_TOKEN_FILE, encoding="utf-8") as fh:
            tok = fh.read().strip()
    except OSError:
        return {}
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _headers() -> dict[str, str]:
    h = dict(_STATIC_HEADERS)
    h.update(_bearer())
    return h


def _get(path: str, params: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
    try:
        r = httpx.get(
            f"{LOKI_URL}{path}",
            params=params,
            headers=_headers(),
            verify=_VERIFY,
            timeout=timeout,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceError(f"loki request failed ({path}): {exc}") from exc
    body = r.json()
    if body.get("status") != "success":
        raise SourceError(f"loki error: {body.get('error') or body}")
    return body.get("data", {})


def up() -> bool:
    """True if Loki answers an authenticated labels query.

    The LokiStack gateway exposes no ``/ready`` at the tenant path, so probe a
    cheap 1-minute ``labels`` call instead — this exercises the exact auth/TLS
    path the real queries use and works against plain Loki too.
    """
    try:
        end = now_ns()
        r = httpx.get(
            f"{LOKI_URL}/loki/api/v1/labels",
            params={"start": str(end - 60 * NS_PER_S), "end": str(end)},
            headers=_headers(),
            verify=_VERIFY,
            timeout=5,
        )
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def now_ns() -> int:
    return time.time_ns()


def query_range(
    logql: str,
    start_ns: int,
    end_ns: int,
    limit: int = 200,
    direction: str = "backward",
) -> list[dict[str, Any]]:
    """Run a LogQL log query; return Loki's ``streams`` result list.

    Each element is ``{"stream": {labels...}, "values": [[ts_ns_str, line], ...]}``.
    """
    if start_ns >= end_ns:
        return []
    data = _get("/loki/api/v1/query_range", {
        "query": logql,
        "start": str(start_ns),
        "end": str(end_ns),
        "limit": str(limit),
        "direction": direction,
    })
    if data.get("resultType") not in (None, "streams"):
        raise SourceError(f"expected log streams, got {data.get('resultType')}")
    return data.get("result", [])


def metric_range(
    logql: str,
    start_ns: int,
    end_ns: int,
    step: str = "1m",
) -> list[dict[str, Any]]:
    """Run a LogQL *metric* query (e.g. count_over_time); return ``matrix`` result.

    Each element is ``{"metric": {labels...}, "values": [[unix_s, "count"], ...]}``.
    """
    if start_ns >= end_ns:
        return []
    data = _get("/loki/api/v1/query_range", {
        "query": logql,
        "start": str(start_ns),
        "end": str(end_ns),
        "step": step,
    })
    return data.get("result", [])


def labels(start_ns: int, end_ns: int) -> list[str]:
    return _get("/loki/api/v1/labels", {"start": str(start_ns), "end": str(end_ns)}) or []  # type: ignore[return-value]


def label_values(name: str, start_ns: int, end_ns: int) -> list[str]:
    return _get(f"/loki/api/v1/label/{name}/values",
                {"start": str(start_ns), "end": str(end_ns)}) or []  # type: ignore[return-value]


def series(match: str, start_ns: int, end_ns: int) -> list[dict[str, str]]:
    """Return label-set dicts for streams matching ``match`` in the window."""
    return _get("/loki/api/v1/series",
                {"match[]": match, "start": str(start_ns), "end": str(end_ns)}) or []  # type: ignore[return-value]


def diag(namespace: str | None = None, window_h: int = 24) -> dict[str, Any]:
    """Self-diagnostic for the whole log path (mirrors objmon_loki_verify.sh):
    transport, which namespace label the gateway authorizes, and whether the
    tenant actually has data. Lets ops tell "empty tenant" from "label mismatch"
    from "auth/transport down" without shelling into the pod."""
    from . import log_parse as LP
    from . import sources as S

    ns = namespace or S.NS
    end = now_ns()
    start = end - int(window_h) * 3600 * NS_PER_S
    out: dict[str, Any] = {
        "available": True, "loki_url": LOKI_URL,
        "org_id": _STATIC_HEADERS.get("X-Scope-OrgID", ""),
        "namespace_label": LP.LBL_NAMESPACE, "test_namespace": ns,
        "window_hours": int(window_h),
    }
    try:
        out["transport_up"] = up()
    except Exception as exc:  # noqa: BLE001
        out["transport_up"] = False
        out["transport_error"] = str(exc)[:200]
    try:
        out["label_keys"] = labels(start, end)
    except SourceError as exc:
        out["label_keys"] = []
        out["labels_error"] = str(exc)[:200]
    # Which namespace label authorizes + returns data on THIS LokiStack?
    probes: dict[str, Any] = {}
    for lbl in dict.fromkeys([LP.LBL_NAMESPACE, "kubernetes_namespace_name", "namespace"]):
        try:
            res = query_range('{%s="%s"}' % (lbl, ns), start, end, limit=1)
            probes[lbl] = {"authorized": True, "streams": len(res)}
        except SourceError as exc:
            probes[lbl] = {"authorized": False, "error": str(exc)[:120]}
    out["namespace_label_probes"] = probes
    try:
        out["namespace_values_count"] = len(label_values(LP.LBL_NAMESPACE, start, end))
    except SourceError:
        out["namespace_values_count"] = 0

    configured_ok = probes.get(LP.LBL_NAMESPACE, {}).get("authorized", False)
    has_data = out["namespace_values_count"] > 0 or any(
        p.get("streams", 0) > 0 for p in probes.values())
    out["configured_label_authorized"] = configured_ok
    out["tenant_has_data"] = has_data
    if not out.get("transport_up"):
        out["verdict"] = "transport down — check LOKI_URL / TLS / bearer token"
    elif not configured_ok:
        alt = [l for l, p in probes.items() if p.get("authorized")]
        out["verdict"] = (
            f"configured label '{LP.LBL_NAMESPACE}' not authorized; "
            f"authorized alternates={alt} — set LOKI_LABEL_NAMESPACE"
        ) if alt else "no namespace label authorized — RBAC / tenant issue"
    elif not has_data:
        out["verdict"] = "authorized but tenant EMPTY — no logs forwarded (fix ClusterLogForwarder)"
    else:
        out["verdict"] = "ok — logs reachable and tenant has data"
    return out
