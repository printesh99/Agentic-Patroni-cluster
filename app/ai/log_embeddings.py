"""Incremental Loki-to-pgvector evidence index and semantic retrieval."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import text

from .. import log_parse, loki, sources as S
from ..db.session import SessionLocal
from ..services import ai_provider

EMBED_DIM = 1536
MAX_CHUNKS = 200
DEFAULT_RETENTION_DAYS = 90
DEFAULT_FRESHNESS_SECONDS = 2700


def _enabled() -> bool:
    return os.environ.get("LOG_INDEX_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _vector(value: list[float]) -> str:
    if len(value) != EMBED_DIM:
        raise ValueError(f"embedding dimension must be {EMBED_DIM}")
    return "[" + ",".join(str(float(item)) for item in value) + "]"


def _watermark(db, cluster_id: str, log_type: str) -> datetime | None:
    return db.execute(text("SELECT last_indexed_at FROM log_index_state WHERE cluster_id=:c AND log_type=:t"),
                      {"c": cluster_id, "t": log_type}).scalar_one_or_none()


def _chunks(streams: list[dict[str, Any]], limit: int = MAX_CHUNKS) -> list[dict[str, Any]]:
    records = log_parse.flatten(streams)
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: item["ts_ns"]):
        message = record["message"].strip()
        if not message:
            continue
        digest = hashlib.sha256((record["ts_ns"] + "\0" + message).encode()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        output.append({**record, "content_hash": digest, "summary": message[:1000]})
        if len(output) >= limit:
            break
    return output


def _lock(db, cluster_id: str) -> bool:
    return bool(db.execute(text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                           {"key": f"object-monitor-log-index:{cluster_id}"}).scalar())


def _unlock(db, cluster_id: str) -> None:
    db.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"),
               {"key": f"object-monitor-log-index:{cluster_id}"})


def _retention(db) -> int:
    days = _positive_int("LOG_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)
    result = db.execute(text(
        "DELETE FROM log_embeddings WHERE log_time < now() - make_interval(days => :days)"
    ), {"days": days})
    return max(0, int(result.rowcount or 0))


def index_cluster_logs(cluster_id: str,
                       log_types: tuple[str, ...] = ("database", "pgbouncer")) -> dict[str, Any]:
    if not _enabled():
        return {"available": True, "enabled": False, "status": "disabled", "chunks_indexed": 0}
    result: dict[str, Any] = {
        "available": True, "enabled": True, "status": "ok", "chunks_indexed": 0,
        "deduplicated": 0, "retention_deleted": 0, "sources": {},
    }
    end_ns = loki.now_ns()
    with SessionLocal() as db:
        if not _lock(db, cluster_id):
            return {**result, "status": "skipped", "reason": "index_lock_busy"}
        try:
            result["retention_deleted"] = _retention(db)
            db.commit()
            for log_type in log_types:
                watermark = _watermark(db, cluster_id, log_type)
                start = watermark or datetime.now(timezone.utc) - timedelta(hours=72)
                try:
                    streams = loki.query_range(
                        log_parse.build_selector([log_type]),
                        int(start.timestamp() * loki.NS_PER_S), end_ns,
                        limit=2000, direction="forward",
                    )
                    chunks = _chunks(streams)
                    vectors = ai_provider.embed([chunk["summary"] for chunk in chunks]) if chunks else []
                    inserted = 0
                    for chunk, vector in zip(chunks, vectors):
                        metadata = {
                            "content_hash": chunk["content_hash"], "component": chunk["component"],
                            "pod": chunk["pod"], "container": chunk["container"],
                            "severity": chunk["severity"], "level": chunk["level"],
                            "embed_model": "text-embedding-3-small",
                        }
                        row = db.execute(text(
                            "INSERT INTO log_embeddings "
                            "(cluster_id,log_type,log_time,raw_text,summary,embedding,metadata,created_at) "
                            "VALUES (:c,:t,:ts,:raw,:summary,CAST(:embedding AS vector),CAST(:metadata AS jsonb),now()) "
                            "ON CONFLICT DO NOTHING RETURNING id"
                        ), {
                            "c": cluster_id, "t": log_type, "ts": chunk["ts"],
                            "raw": chunk["message"], "summary": chunk["summary"],
                            "embedding": _vector(vector), "metadata": json.dumps(metadata),
                        }).first()
                        inserted += int(row is not None)
                    deduplicated = max(0, len(chunks) - inserted)
                    newest = max((datetime.fromisoformat(chunk["ts"]) for chunk in chunks), default=start)
                    db.execute(text(
                        "INSERT INTO log_index_state "
                        "(cluster_id,log_type,last_indexed_at,last_run_at,chunks_indexed,last_error) "
                        "VALUES (:c,:t,:wm,now(),:n,NULL) "
                        "ON CONFLICT (cluster_id,log_type) DO UPDATE SET "
                        "last_indexed_at=GREATEST(log_index_state.last_indexed_at,excluded.last_indexed_at),"
                        "last_run_at=now(),chunks_indexed=log_index_state.chunks_indexed+excluded.chunks_indexed,last_error=NULL"
                    ), {"c": cluster_id, "t": log_type, "wm": newest, "n": inserted})
                    db.commit()
                    result["chunks_indexed"] += inserted
                    result["deduplicated"] += deduplicated
                    result["sources"][log_type] = {
                        "status": "ok", "chunks_indexed": inserted,
                        "deduplicated": deduplicated, "last_indexed_at": newest.isoformat(),
                    }
                except Exception as exc:
                    db.rollback()
                    result["status"] = "partial"
                    result["sources"][log_type] = {"status": "error", "error": type(exc).__name__}
                    db.execute(text(
                        "INSERT INTO log_index_state "
                        "(cluster_id,log_type,last_indexed_at,last_run_at,chunks_indexed,last_error) "
                        "VALUES (:c,:t,:wm,now(),0,:error) ON CONFLICT (cluster_id,log_type) DO UPDATE SET "
                        "last_run_at=now(),last_error=excluded.last_error"
                    ), {"c": cluster_id, "t": log_type, "wm": start, "error": type(exc).__name__})
                    db.commit()
        finally:
            _unlock(db, cluster_id)
        return result


def search(query: str, cluster_id: str, start_ns: int | None = None,
           end_ns: int | None = None, log_types: Iterable[str] | None = None,
           limit: int = 12) -> dict[str, Any]:
    """Return fresh, time-scoped semantic evidence using pgvector cosine distance."""
    if not query.strip():
        return {"available": True, "status": "empty_query", "fresh": False, "entries": []}
    limit = min(max(int(limit), 1), 40)
    types = [item for item in (log_types or []) if item in {"database", "pgbouncer"}]
    params: dict[str, Any] = {"cluster": cluster_id, "limit": limit}
    where = ["cluster_id=:cluster"]
    if start_ns is not None:
        params["start"] = datetime.fromtimestamp(start_ns / loki.NS_PER_S, tz=timezone.utc)
        where.append("log_time>=:start")
    if end_ns is not None:
        params["end"] = datetime.fromtimestamp(end_ns / loki.NS_PER_S, tz=timezone.utc)
        where.append("log_time<=:end")
    if types:
        names = []
        for idx, value in enumerate(types):
            key = f"t{idx}"
            params[key] = value
            names.append(f":{key}")
        where.append(f"log_type IN ({','.join(names)})")
    try:
        params["embedding"] = _vector(ai_provider.embed([query[:2000]])[0])
        sql = (
            "SELECT log_type,log_time,summary,metadata,"
            "1-(embedding <=> CAST(:embedding AS vector)) AS score "
            "FROM log_embeddings WHERE " + " AND ".join(where) +
            " ORDER BY embedding <=> CAST(:embedding AS vector) LIMIT :limit"
        )
        with SessionLocal() as db:
            latest = db.execute(text(
                "SELECT max(last_indexed_at) FROM log_index_state WHERE cluster_id=:cluster"
            ), {"cluster": cluster_id}).scalar_one_or_none()
            # The live IVFFlat index has 100 lists. Probe all lists so selective
            # cluster/time filters cannot produce false-empty nearest-neighbor
            # results after the approximate index scan.
            db.execute(text("SET LOCAL ivfflat.probes = 100"))
            rows = db.execute(text(sql), params).mappings().all()
        now = datetime.now(timezone.utc)
        lag_seconds = int((now - latest).total_seconds()) if latest else None
        fresh = lag_seconds is not None and lag_seconds <= _positive_int(
            "LOG_INDEX_FRESHNESS_SECONDS", DEFAULT_FRESHNESS_SECONDS)
        entries = []
        for row in rows:
            metadata = row["metadata"] or {}
            ts = row["log_time"]
            entries.append({
                "ts": ts.isoformat() if ts else None,
                "ts_ns": str(int(ts.timestamp() * loki.NS_PER_S)) if ts else "0",
                "level": metadata.get("level") or "INFO",
                "severity": metadata.get("severity") or "info",
                "component": metadata.get("component") or row["log_type"],
                "pod": metadata.get("pod") or "", "container": metadata.get("container") or "",
                "namespace": "", "node": "", "message": row["summary"],
                "semantic_score": round(float(row["score"] or 0), 6), "evidence_source": "store",
            })
        return {
            "available": True, "status": "ok" if entries else "empty", "fresh": fresh,
            "lag_seconds": lag_seconds, "last_indexed_at": latest.isoformat() if latest else None,
            "entries": entries,
        }
    except Exception as exc:
        return {"available": False, "status": "error", "fresh": False,
                "error": type(exc).__name__, "entries": []}


def health() -> dict[str, Any]:
    base = {
        "available": True, "enabled": _enabled(), "embed_dimension": EMBED_DIM,
        "embedding_deployment_configured": bool(os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT")),
        "retention_days": _positive_int("LOG_RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
        "freshness_seconds": _positive_int("LOG_INDEX_FRESHNESS_SECONDS", DEFAULT_FRESHNESS_SECONDS),
    }
    try:
        with SessionLocal() as db:
            counts = db.execute(text(
                "SELECT (SELECT count(*) FROM log_embeddings),"
                "(SELECT count(*) FROM log_index_state),"
                "(SELECT count(*) FROM ai_evidence_items),"
                "(SELECT count(*) FROM ai_assistant_sessions)"
            )).one()
            rows = db.execute(text(
                "SELECT cluster_id,log_type,last_indexed_at,last_run_at,chunks_indexed,last_error "
                "FROM log_index_state WHERE cluster_id=:cluster ORDER BY cluster_id,log_type"
            ), {"cluster": S.CLUSTER_NAME}).mappings().all()
        now = datetime.now(timezone.utc)
        stale_after = base["freshness_seconds"]
        sources = []
        for row in rows:
            lag = int((now - row["last_indexed_at"]).total_seconds()) if row["last_indexed_at"] else None
            status = "error" if row["last_error"] else ("fresh" if lag is not None and lag <= stale_after else "stale")
            sources.append({
                "cluster_id": row["cluster_id"], "log_type": row["log_type"], "status": status,
                "lag_seconds": lag,
                "last_indexed_at": row["last_indexed_at"].isoformat() if row["last_indexed_at"] else None,
                "last_run_at": row["last_run_at"].isoformat() if row["last_run_at"] else None,
                "chunks_indexed": row["chunks_indexed"], "last_error": row["last_error"],
            })
        overall = "empty" if not counts[0] else (
            "stale" if any(row["status"] in {"stale", "error"} for row in sources) else "ok")
        latest = max((row["last_indexed_at"] for row in rows if row["last_indexed_at"]), default=None)
        return {
            **base, "status": overall, "log_embeddings": counts[0], "index_sources": counts[1],
            "evidence_items": counts[2], "assistant_sessions": counts[3],
            "last_indexed_at": latest.isoformat() if latest else None, "sources": sources,
        }
    except Exception as exc:
        return {**base, "available": False, "status": "unavailable", "error": type(exc).__name__}
