"""RAG Embeddings — convenience facade over ``app.rag.embedder``.

Maintains backwards compatibility with existing consumers (engine.py,
ann_index.py) while delegating to the new protocol-based embedder
architecture.

Public API (unchanged):
    - ``EMBEDDING_DIM`` — current primary dimension constant
    - ``get_embedding(text)`` — single-text async embedding
    - ``get_embeddings(texts)`` — batch async embedding (supports 100+)
    - ``current_backend()`` — human-readable backend name
    - ``RAGUnavailableError`` — error when no backend is reachable
"""

from __future__ import annotations

import os
from typing import Final

from app.config import settings
from app.core.logging import get_logger
from app.rag.embedder import (
    BGE_M3_Embedder,
    CachedEmbedder,
    EmbedderProtocol,
    FallbackEmbedder,
    OllamaEmbedder,
    get_default_embedder,
    lookup_dim,
    normalize_dim,
)

log = get_logger(__name__)

# ── Public constants ────────────────────────────────────────────────────
EMBEDDING_DIM: Final = lookup_dim(settings.embedder_primary)

# Re-export _resize for legacy code (rmi-rag/legacy_supabase.py)
_resize = normalize_dim


class RAGUnavailableError(Exception):
    """Raised when no embedding backend is reachable. The RAG endpoint
    translates this to HTTP 503 + an explicit empty-result body.
    See T04 in MINIMAX_M3_TASKS.md."""


# ── Backwards-compatible async API ──────────────────────────────────────

_EMBEDDER: EmbedderProtocol | None = None


def _get_embedder() -> EmbedderProtocol:
    """Lazy singleton embedder for the convenience functions below."""
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = get_default_embedder()
    return _EMBEDDER


async def get_embedding(text: str) -> list[float]:
    """Async embedding API. Returns ``EMBEDDING_DIM``-dimensional float list.

    Uses the configured embedder chain (default: bge-m3 -> nomic-embed-text + cache).
    Raises ``RAGUnavailableError`` on total failure.

    Per T04: NO hash fallback. If all neural backends fail, the error
    propagates so the caller can return HTTP 503 explicitly.
    """
    if not text or not text.strip():
        raise RAGUnavailableError("empty text input")

    try:
        return await _get_embedder().embed_one(text)
    except RuntimeError as exc:
        raise RAGUnavailableError(
            f"no embedding backend available: {exc}"
        ) from exc


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Batch embedding. Handles 100+ texts efficiently.

    Delegates to the embedder chain which supports native batching
    (bge-m3, Ollama batch mode) with automatic fallback to sequential
    processing if the backend doesn't support batching.
    """
    if not texts:
        return []

    try:
        return await _get_embedder().embed(texts)
    except RuntimeError as exc:
        raise RAGUnavailableError(
            f"batch embedding failed: {exc}"
        ) from exc


# ── Health / observability ──────────────────────────────────────────────

def current_backend() -> str:
    """Return a human-readable string describing the active embedder."""
    try:
        e = _get_embedder()
        return e.name
    except Exception:
        return "unavailable"


# ── Embedder factory (convenience for callers who want explicit control) ─

def create_embedder(
    primary: str | None = None,
    fallback: str | None = None,
    enable_cache: bool = True,
) -> EmbedderProtocol:
    """Build a fully-wired embedder chain (convenience wrapper).

    Args:
        primary: ``"bge-m3"`` or ``"ollama"`` (default from settings).
        fallback: Ollama model name for fallback (default from settings).
        enable_cache: Wrap in ``CachedEmbedder``.

    Returns:
        An ``EmbedderProtocol`` instance ready for ``await embed(texts)``.
    """
    from app.rag.embedder import get_embedder as _build

    return _build(primary=primary, fallback=fallback, enable_cache=enable_cache)


__all__ = [
    "EMBEDDING_DIM",
    "RAGUnavailableError",
    "create_embedder",
    "current_backend",
    "get_embedding",
    "get_embeddings",
    "_resize",
]
