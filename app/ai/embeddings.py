"""Local sentence-embedding backend for semantic RAG (Phase 6).

Uses fastembed (ONNX runtime) to serve ``all-MiniLM-L6-v2`` locally — no API
key, no egress, ~90MB model. The model is baked into the container image under
``vendor/fastembed_cache`` (see Dockerfile), so semantic RAG works in an
air-gapped pod with no first-use download. Everything degrades to ``None`` when
the dependency or model is unavailable, so the keyword retriever keeps working
on a box without the model.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

# all-MiniLM-L6-v2 output dimensionality.
EMBED_DIM = 384
MODEL_NAME = os.environ.get("AI_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Baked model cache, vendored into the image. Repo layout:
# <repo>/vendor/fastembed_cache, with app/ai/embeddings.py three levels down.
_BAKED_CACHE = Path(__file__).resolve().parent.parent.parent / "vendor" / "fastembed_cache"

_model = None
_lock = threading.Lock()
_unavailable = False


def _cache_dir() -> str | None:
    """Resolve the fastembed cache directory.

    Explicit env wins (deployment sets ``AI_EMBED_CACHE_DIR`` to the baked
    path); otherwise fall back to the vendored cache if it shipped with the
    image; otherwise ``None`` so fastembed uses its own default (dev box with
    network).
    """
    for var in ("AI_EMBED_CACHE_DIR", "FASTEMBED_CACHE_PATH"):
        v = os.environ.get(var)
        if v:
            return v
    if _BAKED_CACHE.is_dir():
        return str(_BAKED_CACHE)
    return None


def available() -> bool:
    """True when an embedding model can be loaded."""
    return _get_model() is not None


def _get_model():
    global _model, _unavailable
    if _model is not None or _unavailable:
        return _model
    with _lock:
        if _model is not None or _unavailable:
            return _model
        try:
            cache = _cache_dir()
            # With a baked cache present, never reach for the network: a slow or
            # blocked HF call in an air-gapped pod would otherwise hang model
            # init. Set before importing fastembed (which pulls in
            # huggingface_hub, which reads these at import time). Opt out with
            # AI_EMBED_ALLOW_DOWNLOAD=1 for a dev box that wants to refresh.
            if cache and not os.environ.get("AI_EMBED_ALLOW_DOWNLOAD"):
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            from fastembed import TextEmbedding
            _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=cache)
        except Exception:
            _unavailable = True
            _model = None
        return _model


def embed(text: str) -> list[float] | None:
    """Return a 384-dim embedding for ``text`` or None if unavailable."""
    model = _get_model()
    if model is None or not (text or "").strip():
        return None
    try:
        vecs = list(model.embed([text]))
        return [float(x) for x in vecs[0]]
    except Exception:
        return None


def embed_many(texts: list[str]) -> list[list[float] | None]:
    model = _get_model()
    if model is None:
        return [None for _ in texts]
    try:
        out = list(model.embed(texts))
        return [[float(x) for x in v] for v in out]
    except Exception:
        return [None for _ in texts]
