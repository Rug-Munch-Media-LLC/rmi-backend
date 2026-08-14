"""Qdrant storage/client primitives for the M3 RAG engine.

Extracted from ``app/rag/engine.py`` during the Qdrant consolidation
de-godding pass: collection probing, named-vector detection, the legacy
HTTP vector-search fallback, collection resolution, the sparse query
builder and the additive chunk upsert write path. ``engine.py`` re-exports
every public name.

Module-level side effects are intentional and preserved: ``_QDRANT_URL``
reads ``app.config.settings`` and ``_DEFAULT_QDRANT_COLLECTION`` reads the
environment, exactly as the original ``engine.py`` did at import time.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.rag.ann_index import Hit

if TYPE_CHECKING:
    from app.rag.chunking import Chunk

log = get_logger(__name__)

# Qdrant fallback: collections that live in Qdrant (e.g. `news` seeded by
# rmi-rag/scripts/news_to_rag.py) are searchable even though the in-process
# numpy/Redis index has no data for them. Cache which collections exist.
# Resolved via app.config (env var wins; Docker name inside the network;
# host-mapped localhost on host-side systemd runs).
_QDRANT_URL = settings.qdrant_url.rstrip("/")
_QDRANT_COLLECTIONS: set[str] = set()
_QDRANT_PROBED = False

# Collection name resolved for the primary Qdrant store when the requested
# RAG collection does not itself exist in Qdrant (env-driven, default
# "rag_documents" — mirrors app.rag.qdrant_store._resolve_collection).
_DEFAULT_QDRANT_COLLECTION = os.getenv("RAG_QDRANT_COLLECTION", "rag_documents").strip() or "rag_documents"

# Resolved collection cache for the primary store path (requested RAG
# collection → Qdrant collection name).
_QDRANT_RESOLVED: dict[str, str] = {}

# Lexical stopwords for the query→sparse-vector builder (mirrors the set
# used by the legacy _keyword_search so the two lexical paths stay consistent).
_SEARCH_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "this",
        "that",
        "and",
        "for",
        "with",
        "from",
        "has",
        "have",
        "was",
        "were",
        "been",
        "are",
        "not",
        "but",
        "its",
        "all",
        "can",
        "will",
        "just",
        "very",
        "also",
        "than",
        "then",
        "each",
        "which",
        "what",
        "when",
        "where",
        "how",
        "who",
    }
)


async def _qdrant_collection_exists(client: httpx.AsyncClient, collection: str) -> bool:
    """Probe Qdrant once per process for a collection's existence.

    Resolves both real collections and aliases (e.g. ``news`` → ``news_v2``)
    so legacy collection names stay searchable. ``GET /collections`` does not
    return alias names in Qdrant 1.18, so aliases are probed separately via
    ``GET /aliases``; without this the Qdrant fallback returned 0 results for
    every alias-named collection (news, scams, scanner, research, ...).
    """
    global _QDRANT_PROBED
    if collection in _QDRANT_COLLECTIONS:
        return True
    if not _QDRANT_PROBED:
        try:
            r = await client.get("/collections")
            if r.status_code == 200:
                names = {c.get("name") for c in r.json().get("result", {}).get("collections", [])}
                _QDRANT_COLLECTIONS.update(names)
            a = await client.get("/aliases")
            if a.status_code == 200:
                alias_names = {c.get("alias_name") for c in a.json().get("result", {}).get("aliases", [])}
                _QDRANT_COLLECTIONS.update(alias_names)
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.debug("qdrant_probe_failed err=%s", e)
        _QDRANT_PROBED = True
    return collection in _QDRANT_COLLECTIONS


async def _qdrant_uses_named_vectors(client: httpx.AsyncClient, collection: str) -> bool:
    """Probe a collection's config to detect named-vector collections.

    v2 collections (e.g. ``threat_intel_v2``, ``scanner_v2``) are created
    with named vectors (``{"dense": ..., "bm25": ...}``) and reject a bare
    vector with "Not existing vector name". Dense-only legacy collections
    accept the bare vector format. Returns False on any probe failure
    (missing collection, unreachable Qdrant) so the caller falls back to
    the legacy request shape.
    """
    try:
        r = await client.get(f"/collections/{collection}")
        if r.status_code != 200:
            return False
        vectors = r.json().get("result", {}).get("config", {}).get("params", {}).get("vectors", {})
        return isinstance(vectors, dict) and "dense" in vectors
    except (httpx.HTTPError, httpx.TimeoutException, ValueError, KeyError) as e:
        log.debug("qdrant_config_probe_failed collection=%s err=%s", collection, e)
        return False


async def _qdrant_search(
    query_vec: list[float],
    collection: str,
    limit: int = 20,
    score_threshold: float = 0.0,
) -> list[Hit]:
    """Vector search against Qdrant (fallback for Qdrant-backed collections).

    Handles both legacy collections (bare vector) and v2 collections that
    use named vectors. The collection config is probed first so the named
    ``dense`` vector is sent when required — a bare vector against a
    named-vector collection is rejected by Qdrant with "Not existing
    vector name", which previously made v2 data unreachable (0 results).

    ``score_threshold`` defaults to 0.0 = no threshold, matching the
    legacy behavior (three_pillar_search passes ``min_similarity`` and
    only filters when it is > 0). v2 bge-m3 cosine scores for relevant
    matches cluster around 0.45-0.70, so a high fixed default would make
    the fallback return 0 results for typical queries.

    Returns Hit objects in the same shape as the local ANN index so the
    existing RRF fusion / response wrapping keeps working unchanged.
    Returns [] when the collection is missing or Qdrant is unreachable.
    """
    try:
        async with httpx.AsyncClient(base_url=_QDRANT_URL, timeout=10.0) as client:
            if not await _qdrant_collection_exists(client, collection):
                return []
            # v2 collections carry named vectors — send the named dense
            # vector instead of the bare vector format.
            vector_field: Any = query_vec
            if await _qdrant_uses_named_vectors(client, collection):
                vector_field = {"name": "dense", "vector": query_vec}
            payload: dict[str, Any] = {
                "vector": vector_field,
                "limit": limit,
                "score_threshold": score_threshold if score_threshold > 0 else None,
                "with_payload": True,
            }
            r = await client.post(f"/collections/{collection}/points/search", json=payload)
            if r.status_code != 200:
                log.warning("qdrant_search_failed status=%d body=%s", r.status_code, r.text[:200])
                return []
            hits: list[Hit] = []
            for point in r.json().get("result", []):
                p = point.get("payload", {})
                meta = p.get("metadata", {}) or {}
                hits.append(
                    Hit(
                        doc_id=str(point.get("id", "")),
                        score=float(point.get("score", 0.0) or 0.0),
                        text=p.get("content", ""),
                        metadata={**meta, "source": p.get("source", meta.get("source", ""))},
                    )
                )
            return hits
    except (httpx.HTTPError, httpx.TimeoutException, ValueError, KeyError) as e:
        log.debug("qdrant_search_error err=%s", e)
        return []


async def _resolve_qdrant_collection(collection: str) -> str:
    """Map the requested RAG collection to a Qdrant collection name.

    Uses the collection itself when it exists in Qdrant (probe cached once
    per process by ``_qdrant_collection_exists``), otherwise the default
    store collection (``RAG_QDRANT_COLLECTION`` / ``rag_documents``).
    """
    if collection in _QDRANT_RESOLVED:
        return _QDRANT_RESOLVED[collection]
    try:
        async with httpx.AsyncClient(base_url=_QDRANT_URL, timeout=5.0) as client:
            resolved = collection if await _qdrant_collection_exists(client, collection) else _DEFAULT_QDRANT_COLLECTION
    except (httpx.HTTPError, httpx.TimeoutException):
        resolved = _DEFAULT_QDRANT_COLLECTION
    _QDRANT_RESOLVED[collection] = resolved
    return resolved


def _build_sparse_query(query: str) -> dict[str, Any] | None:
    """Lexical sparse vector (``{"indices": [...], "values": [...]}``) from a query.

    Stand-in for a learned sparse encoder (SPLADE) until one is wired into the
    primary path: tokens are hashed into a 32-bit index space and weighted by
    a small TF boost so the sparse pillar can fuse with the dense pillar via
    Qdrant RRF. Returns None for stopword-only/empty queries (dense-only).
    """
    import hashlib
    import re

    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_#]*", query.lower())
    tokens = [t for t in tokens if t not in _SEARCH_STOPWORDS and len(t) > 1]
    if not tokens:
        return None
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    indices: list[int] = []
    values: list[float] = []
    for t, c in counts.items():
        indices.append(int(hashlib.sha256(t.encode("utf-8")).hexdigest()[:8], 16))
        values.append(1.0 + 0.5 * (c - 1))
    return {"indices": indices, "values": values}


def _timestamp_epoch(iso_ts: str) -> int:
    """Parse an ISO timestamp to a unix-epoch int (best-effort → now)."""
    try:
        return int(datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return int(time.time())


async def _qdrant_upsert_chunk(
    chunk_doc_id: str,
    chunk: Chunk,
    chunk_meta: dict[str, Any],
    collection: str,
) -> bool:
    """Upsert one ingested chunk into the unified Qdrant store (additive).

    Fail-open: returns False on any error — the legacy numpy/Redis write
    stays authoritative and the caller's ingest status is unaffected.
    Payload follows the QDRANT-CONSOLIDATION schema: ``content`` (sparse
    source), top-level filter fields (``chain``, ``address``, ``collection``
    provenance, unix timestamps) plus the full ``metadata`` object the
    engine's search path reads back (``app.rag.qdrant_store._point_to_hit``).
    """
    from app.rag.qdrant_store import upsert_rag_document

    ts_epoch = _timestamp_epoch(chunk.timestamp)
    payload: dict[str, Any] = {
        "doc_id": chunk_doc_id,
        "source": str(chunk_meta.get("source") or collection),
        "content_type": chunk.content_type,
        "content": chunk.text,
        "collection": collection,
        "chain": chunk_meta.get("chain"),
        "address": chunk_meta.get("address"),
        "created_at": ts_epoch,
        "updated_at": ts_epoch,
        "metadata": chunk_meta,
    }
    try:
        ok = await upsert_rag_document(chunk_doc_id, chunk.text, payload)
    except Exception as exc:  # fail-open contract (never raises into ingest)
        log.warning("rag_qdrant_chunk_upsert_failed doc_id=%s err=%s", chunk_doc_id, exc)
        return False
    if not ok:
        log.info("rag_qdrant_chunk_upsert_skipped doc_id=%s", chunk_doc_id)
    return ok


__all__ = [
    "_DEFAULT_QDRANT_COLLECTION",
    "_QDRANT_COLLECTIONS",
    "_QDRANT_PROBED",
    "_QDRANT_RESOLVED",
    "_QDRANT_URL",
    "_SEARCH_STOPWORDS",
    "_build_sparse_query",
    "_qdrant_collection_exists",
    "_qdrant_search",
    "_qdrant_upsert_chunk",
    "_qdrant_uses_named_vectors",
    "_resolve_qdrant_collection",
    "_timestamp_epoch",
]
