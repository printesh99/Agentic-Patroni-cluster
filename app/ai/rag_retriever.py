"""RAG retriever for Phase 6.

Semantic (pgvector cosine) retrieval when a Postgres metadata DB + local
embedding model are available; deterministic keyword (ILIKE) retrieval as the
fallback so the pipeline still works on the SQLite dev box or without the model.
Exact ``runbook_id`` lookups always take priority over similarity search.
"""
from __future__ import annotations

from sqlalchemy import or_, select, text

from ..db.models import AiKnowledgeBase
from ..db.session import SessionLocal, engine
from . import embeddings
from .runbooks import RUNBOOKS

_IS_PG = engine.dialect.name == "postgresql"


def semantic_enabled() -> bool:
    return _IS_PG and embeddings.available()


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _doc(row: AiKnowledgeBase, method: str = "exact", score: float | None = None) -> dict:
    return {
        "id": row.id,
        "runbook_id": row.runbook_id,
        "title": row.title,
        "content": row.content,
        "tags": row.tags,
        "source_file": row.source_file,
        "method": method,
        "score": score,
    }


def _embedding_column_ready(db) -> bool:
    if not _IS_PG:
        return False
    try:
        return bool(db.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'ai_knowledge_base' AND column_name = 'embedding' "
            "LIMIT 1"
        )).scalar())
    except Exception:
        return False


def _backfill_embeddings(db, limit: int | None = None) -> int:
    """Embed any KB rows missing an embedding (Postgres + model only)."""
    if not semantic_enabled() or not _embedding_column_ready(db):
        return 0
    sql = "SELECT id, title, content FROM ai_knowledge_base WHERE embedding IS NULL ORDER BY id"
    params = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = max(1, int(limit))
    rows = db.execute(text(sql), params).all()
    done = 0
    for rid, title, content in rows:
        vec = embeddings.embed(f"{title}\n{content}")
        if not vec:
            continue
        db.execute(
            text("UPDATE ai_knowledge_base SET embedding = CAST(:v AS vector) WHERE id = :id"),
            {"v": _vec_literal(vec), "id": rid},
        )
        done += 1
    if done:
        db.commit()
    return done


def backfill_missing_embeddings(limit: int | None = None) -> dict:
    """Public endpoint helper for newly-ingested KB rows.

    This is intentionally tolerant: keyword RAG still works when pgvector, the
    embedding column, or the local model are unavailable.
    """
    with SessionLocal() as db:
        return {
            "available": True,
            "semantic": semantic_enabled(),
            "embedding_column": _embedding_column_ready(db),
            "embedded": _backfill_embeddings(db, limit=limit),
        }


def seed() -> dict:
    with SessionLocal() as db:
        existing = {r[0] for r in db.execute(select(AiKnowledgeBase.runbook_id)).all()}
        count = 0
        for runbook_id, title, content in RUNBOOKS:
            if runbook_id in existing:
                continue
            db.add(AiKnowledgeBase(
                doc_type="runbook",
                region="uae",
                title=title,
                content=content,
                tags=[runbook_id, "dba", "safe-action"],
                source_file="built-in",
                runbook_id=runbook_id,
            ))
            count += 1
        db.commit()
        embedded = _backfill_embeddings(db)
        return {"available": True, "seeded": count, "embedded": embedded, "semantic": semantic_enabled()}


def _semantic_search(db, query: str, limit: int) -> list[dict]:
    if not _embedding_column_ready(db):
        return []
    qv = embeddings.embed(query)
    if not qv:
        return []
    sql = text(
        "SELECT id, runbook_id, title, content, tags, source_file, "
        "(embedding <=> CAST(:v AS vector)) AS dist "
        "FROM ai_knowledge_base WHERE embedding IS NOT NULL "
        "ORDER BY embedding <=> CAST(:v AS vector) LIMIT :k"
    )
    try:
        res = db.execute(sql, {"v": _vec_literal(qv), "k": limit}).mappings().all()
    except Exception:
        return []
    return [{
        "id": r["id"],
        "runbook_id": r["runbook_id"],
        "title": r["title"],
        "content": r["content"],
        "tags": r["tags"],
        "source_file": r["source_file"],
        "method": "semantic",
        # cosine distance -> similarity for readability
        "score": round(1.0 - float(r["dist"]), 4),
    } for r in res]


def _keyword_search(db, query: str, limit: int) -> list[dict]:
    like = f"%{query.lower()}%"
    rows = db.execute(
        select(AiKnowledgeBase)
        .where(or_(AiKnowledgeBase.title.ilike(like), AiKnowledgeBase.content.ilike(like)))
        .limit(limit)
    ).scalars().all()
    return [_doc(r, method="keyword") for r in rows]


def retrieve(runbook_id: str | None = None, query: str | None = None, limit: int = 5) -> list[dict]:
    hits = _retrieve(runbook_id=runbook_id, query=query, limit=limit)
    try:
        from .. import metrics
        method = hits[0]["method"] if hits else ("semantic" if semantic_enabled() else "keyword")
        metrics.RAG_RETRIEVALS.labels(method=method).inc()
        metrics.RAG_HITS.observe(len(hits))
    except Exception:
        pass
    return hits


def _retrieve(runbook_id: str | None = None, query: str | None = None, limit: int = 5) -> list[dict]:
    seed()
    with SessionLocal() as db:
        # 1. Exact runbook id wins.
        if runbook_id:
            rows = db.execute(
                select(AiKnowledgeBase).where(AiKnowledgeBase.runbook_id == runbook_id).limit(limit)
            ).scalars().all()
            if rows:
                return [_doc(r, method="exact") for r in rows]

        # 2. Query -> semantic, then keyword fallback.
        if query:
            if semantic_enabled():
                hits = _semantic_search(db, query, limit)
                if hits:
                    return hits
            return _keyword_search(db, query, limit)

        # 3. No selector: return a sample.
        rows = db.execute(select(AiKnowledgeBase).limit(limit)).scalars().all()
        return [_doc(r, method="sample") for r in rows]
