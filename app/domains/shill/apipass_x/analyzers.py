"""Shill-signal analysis: semantic dedup + relevance reranking (fail-open)."""

from __future__ import annotations

import math
import os
import time
from collections import deque
from typing import Any

from app.domains.shill.apipass_x.helpers import (
    DEDUP_COSINE_THRESHOLD,
    DEDUP_MAX_VECTORS,
    logger,
)

_recent_vectors: deque[tuple[str, list[float]]] = deque(maxlen=DEDUP_MAX_VECTORS)
_embed_available: bool | None = None
_embed_retry_after: float = 0.0
_EMBED_RETRY_SECONDS: float = float(os.getenv("APIPASS_EMBED_RETRY_SECONDS", "300"))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _remember_vector(text: str, vector: list[float]) -> None:
    global _embed_available
    _recent_vectors.append((text, vector))
    _embed_available = True


def reset_embed_availability() -> None:
    """Force re-probe of the embedder (tests / after an outage)."""
    global _embed_available, _embed_retry_after
    _embed_available = None
    _embed_retry_after = 0.0


def _semantic_is_duplicate(text: str, vector: list[float]) -> bool:
    for stored_text, stored_vector in _recent_vectors:
        if stored_text == text:
            return True
        if _cosine(vector, stored_vector) >= DEDUP_COSINE_THRESHOLD:
            return True
    return False


async def _maybe_embed(text: str) -> list[float] | None:
    """Embed *text* via the backend embedder; None when unavailable.

    Lazy import so the ApiPass client stays import-safe even when the
    embedding stack is down. The bot copy embedded via ``qwen3-4B``; here we
    route through ``app.embedding_router.embed_single`` (the backend's
    canonical embedder), which degrades to None on any failure — the caller
    then falls back to identity dedup.
    """
    try:
        from app.embedding_router import embed_single

        _vec, _meta = await embed_single(text)
        if isinstance(_vec, list) and _vec and isinstance(_vec[0], (int, float)):
            return [float(x) for x in _vec]
    except Exception as exc:  # fail-open by contract
        logger.debug("apipass_x:embed_failed err=%s", str(exc)[:120])
    return None


async def semantic_dedup(text: str, *, key: str = "") -> bool:
    """True when *text* is a semantic near-duplicate of a recent item.

    Degrades to identity (URL key) dedup when the embedder is unavailable —
    dedup degrades, never blocks. Re-probed on a cooldown after an outage.
    """
    global _embed_available, _embed_retry_after
    _ = key  # caller may pass a URL key for the identity fallback
    if _embed_available is False and time.monotonic() < _embed_retry_after:
        return False
    vector = await _maybe_embed(text)
    if not vector:
        if _embed_available is not False:
            logger.warning(
                "apipass_x:embed_unavailable semantic_dedup_disabled retry_seconds=%s",
                _EMBED_RETRY_SECONDS,
            )
        _embed_available = False
        _embed_retry_after = time.monotonic() + _EMBED_RETRY_SECONDS
        return False
    _embed_available = True
    if _semantic_is_duplicate(text, vector):
        return True
    _remember_vector(text, vector)
    return False


async def rerank_tweets(tweets: list[dict[str, Any]], query: str, top_n: int = 5) -> list[dict[str, Any]]:
    """Rank *tweets* by relevance to *query* via the backend reranker.

    Fail-open: reranker unavailable/empty → input order unchanged. Returns up
    to ``top_n`` tweets.
    """
    if not tweets:
        return []
    documents = [str(t.get("text") or "")[:500] for t in tweets]
    try:
        from app.rag.rerank import rerank_results

        ranked = await rerank_results(query, documents, top_n=min(top_n, len(documents)), tier="live")
    except Exception as exc:  # fail-open by contract
        logger.debug("apipass_x:rerank_failed err=%s", str(exc)[:120])
        return tweets[:top_n]
    if not ranked:
        return tweets[:top_n]
    ordered: list[dict[str, Any]] = []
    for item in sorted(ranked, key=lambda r: float(r.get("relevance_score") or 0.0), reverse=True):
        idx = int(item.get("index") or 0)
        if 0 <= idx < len(tweets):
            ordered.append(tweets[idx])
    return ordered[:top_n] or tweets[:top_n]


__all__ = [
    "rerank_tweets",
    "reset_embed_availability",
    "semantic_dedup",
]
