"""
RugBrain HTTP client — replaces direct ``from app.rag import ...`` calls.

Usage::

    from app.integrations.rugbrain_client import rugbrain_search, rugbrain_ingest

    results = await rugbrain_search("flash loan", collections=["defi_exploits"])
    ok = await rugbrain_ingest(content="...", collection="defi_exploits")
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from app.databus.vault import resolve_key

logger = logging.getLogger(__name__)

_RUGBRAIN_API_URL: str = os.getenv("RAG_API_URL", "http://rugbrain-api:8012/api/v1").rstrip("/")
_RUGBRAIN_API_KEY: str = resolve_key("RMI_RAG_API_KEY")
_HTTP_TIMEOUT: int = int(os.getenv("RAG_HTTP_TIMEOUT", "300"))

# ── Key rotation + direct-Qdrant fallback (2026-08-30) ────────────────────
# The RAG key may be provisioned as a ``new,old`` rotation (active key first).
# Auth validates an exact match, so send ONLY the active segment — never the
# raw ``new,old`` blob. The rag-api HTTP hop is also unreachable from the
# backend container when rag-api binds host-loopback (127.0.0.1:8012); when it
# is, we fall back to querying Qdrant directly (rmi-qdrant-v2:6333) with a
# locally-computed DeepInfra Qwen3-Embedding-4B vector (same 2560-dim space).


def _split_key_rotation(key: str) -> str:
    """Return the ACTIVE key from a ``new,old`` rotation (first non-empty part)."""
    if not key:
        return ""
    parts = [k.strip() for k in key.split(",") if k.strip()]
    return parts[0] if parts else ""


def _qdrant_base() -> str:
    return os.getenv("QDRANT_URL", os.getenv("QDRANT_FALLBACK", "http://rmi-qdrant-v2:6333")).rstrip("/")


async def _deepinfra_embed(query: str) -> list[float] | None:
    """Embed ``query`` via DeepInfra Qwen3-Embedding-4B (2560-dim, matches Qdrant)."""
    url = os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1/embeddings")
    model = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")
    key = _split_key_rotation(resolve_key("DEEPINFRA_API_KEY"))
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                url,
                json={"model": model, "input": query},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
        if r.status_code != 200:
            logger.warning("deepinfra_embed_non_200 status=%s", r.status_code)
            return None
        data = r.json()
        vec = data["data"][0]["embedding"]
        return [float(x) for x in vec] if vec else None
    except Exception as e:
        logger.warning("deepinfra_embed_error: %s", e)
        return None


async def _qdrant_direct_search(query: str, collections: list[str], limit: int) -> list[dict[str, Any]]:
    """Direct Qdrant vector search (bypasses the unreachable/hung rugbrain API).

    Embeds ``query`` once, then vector-searches every ``collection`` in
    ``rmi-qdrant-v2`` concurrently. Returns hits in the rugbrain-compatible
    shape (``content`` / ``rrf_score`` / ``metadata`` / ``doc_id`` +
    ``collection`` / ``similarity``). Returns ``[]`` on failure — never raises.
    """
    vector = await _deepinfra_embed(query)
    if not vector:
        return []
    base = _qdrant_base()

    async def _search_one(col: str) -> list[dict[str, Any]]:
        url = f"{base}/collections/{col}/points/search"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    url,
                    json={"vector": vector, "limit": limit, "with_payload": True},
                    headers={"Content-Type": "application/json"},
                )
            if r.status_code != 200:
                return []
            raw = r.json()
            result = raw.get("result") if isinstance(raw, dict) else None
            points = result if isinstance(result, list) else []
        except Exception as e:
            logger.warning("qdrant_direct_search collection=%s error=%s", col, e)
            return []
        out: list[dict[str, Any]] = []
        for p_ in points:
            if not isinstance(p_, dict):
                continue
            pl = p_.get("payload") or {}
            content = (pl.get("content") or pl.get("text") or "").strip()
            if not content:
                continue
            meta = pl.get("metadata") or {}
            score = float(p_.get("score") or 0.0)
            out.append(
                {
                    "content": content,
                    "rrf_score": score,
                    "score": score,
                    "similarity": score,
                    "metadata": meta if isinstance(meta, dict) else {},
                    "doc_id": pl.get("doc_id") or str(p_.get("id") or ""),
                    "collection": pl.get("collection") or col,
                }
            )
        return out

    try:
        tasks = [_search_one(c) for c in collections]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.warning("qdrant_direct_search gather error=%s", e)
        return []
    out: list[dict[str, Any]] = []
    for res in results:
        if isinstance(res, list):
            out.extend(res)
    return out


async def _qdrant_scroll(
    collection: str,
    filters: list[dict[str, Any]] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Scroll raw Qdrant points (``id`` + ``payload``) for a rugbrain collection.

    This is the thin-client-owned read lane for rugbrain collections. It
    mirrors ``_qdrant_direct_search``: same Qdrant base, same fail-open
    (returns ``[]`` on any error). ``filters`` is a list of Qdrant ``must``
    conditions (e.g. ``[{"key": "metadata.category", "match": {"value": x}}]``).
    """
    base = _qdrant_base()
    body: dict[str, Any] = {"limit": min(limit, 1000), "with_payload": True}
    if filters:
        body["filter"] = {"must": filters}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{base}/collections/{collection}/points/scroll",
                json=body,
                headers={"Content-Type": "application/json"},
            )
        if r.status_code != 200:
            logger.warning(
                "qdrant_scroll collection=%s status=%s body=%s",
                collection,
                r.status_code,
                r.text[:200],
            )
            return []
        result = r.json().get("result", {})
        points = result.get("points", []) if isinstance(result, dict) else []
        return points if isinstance(points, list) else []
    except Exception as e:
        logger.warning("qdrant_scroll collection=%s error=%s", collection, e)
        return []


async def rugbrain_news_scroll(
    filters: list[dict[str, Any]] | None = None,
    limit: int = 200,
    collection: str = "news_v2",
) -> list[dict[str, Any]]:
    """Scroll rugbrain-owned news points (read-only) — the only sanctioned lane.

    The Ground News-style article endpoints need a full collection listing
    with optional Qdrant metadata filters, not a vector search. rugbrain
    exposes no listing endpoint, so this thin client owns the Qdrant scroll
    directly (ARCHITECTURE.md §2: rugbrain via HTTP API / thin client only).
    Returns raw Qdrant point dicts (``{id, payload}``); ``[]`` on failure.
    """
    return await _qdrant_scroll(
        collection=collection,
        filters=filters,
        limit=limit,
    )


async def rugbrain_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    rerank: bool = False,
) -> list[dict[str, Any]]:
    """Multi-collection search — rugbrain HTTP API, falling back to Qdrant DIRECT.

    Primary: ``POST /api/v1/search/multi`` (RRF across collections). When the
    rugbrain API is unreachable (backend container can't reach the host's
    loopback-bound rag-api) or errors, we fall back to querying Qdrant directly
    (``rmi-qdrant-v2:6333``) with a DeepInfra Qwen3-Embedding-4B vector — same
    grounded intel, no broken HTTP hop. ``rerank`` is honored on the HTTP lane;
    the direct lane skips the cross-encoder (down as of 2026-08-30). The key is
    rotation-split so a ``new,old`` rotation sends only the active segment.
    """
    if not query:
        return []

    payload: dict[str, Any] = {
        "query": query,
        "collections": collections or ["threat_intel_v2"],
        "limit": limit,
        "rerank": rerank,
    }
    # ── 1. Qdrant DIRECT (primary) — reachable + fast from the backend container.
    # The rugbrain HTTP API binds host-loopback (127.0.0.1:8012) and is NOT
    # reachable from an rmi-backend Docker container, so Qdrant direct is the
    # reliable lane. DeepInfra Qwen3-Embedding-4B (2560-dim) matches the corpus.
    try:
        direct = await _qdrant_direct_search(query, collections or ["threat_intel_v2"], limit)
        if direct:
            logger.info("rugbrain_search_qdrant_direct hits=%d", len(direct))
            return direct
    except Exception as e:
        logger.warning("rugbrain_search_qdrant_direct_error: %s", e)

    # ── 2. rugbrain HTTP API (secondary — RRF fusion, short timeout) ──
    api_key = _split_key_rotation(_RUGBRAIN_API_KEY)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=2.0)) as client:
            r = await client.post(
                f"{_RUGBRAIN_API_URL}/search/multi",
                json=payload,
                headers={"X-API-Key": api_key} if api_key else {},
            )
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [])
                if results:
                    return results
                logger.warning("rugbrain_search_empty")
            else:
                logger.warning(
                    "rugbrain_search_failed status=%s body=%s",
                    r.status_code,
                    r.text[:200],
                )
    except Exception as e:
        logger.warning("rugbrain_search_error: %s", e)
    return []


async def _deepinfra_news_rerank(
    query: str,
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]] | None:
    """Rerank news candidates via DeepInfra Qwen3-Reranker-4B (fallback lane).

    Used when the rugbrain HTTP rerank endpoint is unreachable from the
    backend container (rag-api binds host-loopback). Same model family as
    rugbrain's local :8003 lane — consistent provider, zero CPU cost.
    Returns rugbrain-compatible ``items`` (score + reranked flags set), or
    ``None`` on any failure so the caller degrades to recency ranking.
    """
    url = os.getenv("DEEPINFRA_RERANK_URL", "https://api.deepinfra.com/v1/inference")
    model = os.getenv("DEEPINFRA_RERANK_MODEL", "Qwen/Qwen3-Reranker-4B")
    key = _split_key_rotation(resolve_key("DEEPINFRA_API_KEY"))
    if not key or not candidates:
        return None
    docs = [str((c.get("metadata") or {}).get("title") or c.get("content") or "")[:8000] for c in candidates]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{url}/{model}",
                json={"queries": [query], "documents": docs},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
        if r.status_code != 200:
            logger.warning("deepinfra_news_rerank status=%s", r.status_code)
            return None
        scores = r.json().get("scores", [])
        if not scores or len(scores) != len(candidates):
            return None
        for c, s in zip(candidates, scores, strict=True):
            c["rerank_score"] = float(s)
            c["reranked"] = True
        candidates.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
        # Flatten to the consumer item shape (same fields as the rugbrain
        # HTTP endpoint returns) so callers need no lane-specific handling.
        items: list[dict[str, Any]] = []
        for c in candidates[:limit]:
            raw_meta = c.get("metadata")
            meta: dict[str, Any] = raw_meta if isinstance((raw_meta := c.get("metadata")), dict) else {}
            items.append(
                {
                    "id": meta.get("news_id") or c.get("doc_id") or "",
                    "news_id": meta.get("news_id") or "",
                    "title": meta.get("title") or "",
                    "source": meta.get("source") or "",
                    "url": meta.get("url") or "",
                    "published_at": meta.get("published_at") or "",
                    "summary": str(c.get("content") or "")[:500],
                    "image_url": meta.get("image_url") or None,
                    "category": meta.get("category") or "",
                    "score": c.get("rerank_score", 0.0),
                    "reranked": True,
                }
            )
        return items
    except Exception as e:
        logger.warning("deepinfra_news_rerank_error: %s", e)
        return None


async def rugbrain_news_rerank(
    query: str,
    limit: int = 20,
    category: str | None = None,
    timeout_s: float = 3.0,
) -> list[dict[str, Any]] | None:
    """Consumer news rerank via rugbrain ``POST /api/v1/rag/news-rerank``.

    Lane 1: rugbrain HTTP endpoint (embed + hybrid search + ~1s rerank).
    Lane 2 (container fallback): rag-api binds host-loopback and is NOT
    reachable from this container, so when the HTTP lane fails we search
    ``news_v2`` directly (existing Qdrant-direct lane) and rerank the
    candidates with DeepInfra Qwen3-Reranker-4B — same model family as
    rugbrain's local lane, same provider as the embed fallback.

    Returns the ranked ``items`` list on success, or ``None`` on ANY failure
    so the caller falls back to recency ranking — never raises, never
    returns fabricated data.
    """
    if not query:
        return None
    api_key = _split_key_rotation(_RUGBRAIN_API_KEY)
    body: dict[str, Any] = {"query": query, "limit": limit}
    if category:
        body["category"] = category
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=1.5)) as client:
            r = await client.post(
                f"{_RUGBRAIN_API_URL}/rag/news-rerank",
                json=body,
                headers={"X-API-Key": api_key} if api_key else {},
            )
        if r.status_code == 200:
            items = r.json().get("items")
            if isinstance(items, list) and items:
                return items
        else:
            logger.warning("rugbrain_news_rerank status=%s body=%s", r.status_code, r.text[:150])
    except Exception as e:
        logger.warning("rugbrain_news_rerank_error: %s", e)

    # ── Container fallback: Qdrant direct + DeepInfra rerank ────────────
    try:
        candidates = await _qdrant_direct_search(query, ["news_v2"], max(limit * 2, 30))
        if not candidates:
            return None
        return await _deepinfra_news_rerank(query, candidates, limit)
    except Exception as e:
        logger.warning("rugbrain_news_rerank_fallback_error: %s", e)
        return None


async def rugbrain_ingest(
    content: str,
    collection: str = "default",
    doc_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    source: str = "api",
    severity: str = "medium",
    chain: str = "",
) -> bool:
    """Ingest a document via rugbrain's POST /api/v1/ingest.

    Returns True if the document was ingested successfully.
    """
    payload: dict[str, Any] = {
        "documents": [
            {
                "doc_id": doc_id or content[:64],
                "content": content,
                "collection": collection,
                "metadata": metadata or {},
                "source": source,
                "severity": severity,
                "chain": chain,
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(
                f"{_RUGBRAIN_API_URL}/ingest",
                json=payload,
                headers={"X-API-Key": _RUGBRAIN_API_KEY},
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("ingested", 0) > 0
            else:
                logger.warning(
                    "rugbrain_ingest_failed status=%s body=%s",
                    r.status_code,
                    r.text[:200],
                )
                return False
    except Exception as e:
        logger.warning("rugbrain_ingest_error: %s", e)
        return False
