"""
Entity Extraction - Hybrid Query & RRF
========================================
Entity-aware hybrid query: exact-match lookup FIRST, then vector
search, merged with Reciprocal Rank Fusion.

When a query contains an address, symbol, hash, or other exact entity,
pure cosine similarity will almost never surface the right document.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.entity_extraction.extractor import extract_entities
from app.entity_extraction.lookup import EntityLookup

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    *ranked_lists: list[dict[str, Any]],
    k: int = 60,
    entity_boost: float = 1.5,
    entity_doc_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Merge multiple ranked result lists using Reciprocal Rank Fusion.

    RRF score = sum( 1 / (k + rank_i) ) across all lists.
    Entity-matched docs get an additional boost multiplier.

    Each item in the input lists must have a ``doc_id`` or ``id`` key.
    """
    entity_doc_ids = entity_doc_ids or set()
    scores: dict[str, float] = {}
    doc_data: dict[str, dict[str, Any]] = {}

    for rlist in ranked_lists:
        for rank, item in enumerate(rlist, start=1):
            doc_id = item.get("doc_id") or item.get("id")
            if not doc_id:
                continue
            rrf = 1.0 / (k + rank)
            if doc_id in entity_doc_ids:
                rrf *= entity_boost
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf
            if doc_id not in doc_data:
                doc_data[doc_id] = item

    sorted_ids = sorted(scores, key=lambda d: scores[d], reverse=True)
    results: list[dict[str, Any]] = []
    for doc_id in sorted_ids:
        entry = dict(doc_data[doc_id])
        entry["rrf_score"] = scores[doc_id]
        entry["entity_match"] = doc_id in entity_doc_ids
        results.append(entry)

    return results


async def hybrid_query(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
    entity_boost: float = 1.5,
    vector_search_fn: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
) -> dict[str, Any]:
    """
    Entity-aware hybrid query: exact-match lookup + vector search,
    merged with Reciprocal Rank Fusion.

    Args:
        query:            User query string.
        collections:      Collections to search (default: all).
        limit:            Max results to return.
        min_similarity:   Minimum cosine similarity for vector results.
        entity_boost:     RRF boost multiplier for entity-matched docs.
        vector_search_fn: Async callable for vector search.
                          Default: app.rag.compat.search_multi_collection.

    Returns:
        Dict with keys:
          results           - merged, RRF-ranked list
          entity_extraction - EntityExtractionResult.to_dict()
          entity_docs       - docs from exact-match lookup
          vector_docs       - docs from vector search
    """
    # Late import to avoid circular dependency
    from app.rag.compat import search_multi_collection

    # 1. Extract entities from query
    extraction = extract_entities(query)

    # 2. Entity exact-match lookup
    entity_docs: list[dict[str, Any]] = []
    entity_doc_ids: set[str] = set()

    if extraction.has_entities:
        lookup = EntityLookup.get_instance()
        for entity in extraction.all_entities():
            try:
                matches = await lookup.lookup(entity["value"])
                for match in matches:
                    if match["doc_id"] not in entity_doc_ids:
                        entity_doc_ids.add(match["doc_id"])
                        entity_docs.append(match)
            except Exception as exc:
                logger.warning("Entity lookup error for %s: %s", entity["value"], exc)

    # 3. Vector (semantic) search
    vector_docs: list[dict[str, Any]] = []
    search_fn = vector_search_fn or search_multi_collection
    try:
        if collections:
            vector_docs = await search_fn(
                query,
                collections,
                limit=limit * 2,
                min_similarity=min_similarity,
            )
        else:
            vector_docs = await search_fn(
                query,
                limit=limit * 2,
                min_similarity=min_similarity,
            )
    except Exception as exc:
        logger.warning("Vector search failed in hybrid_query: %s", exc)

    # 4. Merge with RRF
    entity_ranked = sorted(entity_docs, key=lambda d: d.get("score", 0), reverse=True)
    vector_ranked = sorted(vector_docs, key=lambda d: d.get("similarity", 0), reverse=True)

    merged = reciprocal_rank_fusion(
        entity_ranked,
        vector_ranked,
        k=60,
        entity_boost=entity_boost,
        entity_doc_ids=entity_doc_ids,
    )

    return {
        "results": merged[:limit],
        "entity_extraction": extraction.to_dict(),
        "entity_docs": entity_docs,
        "vector_docs": vector_docs[:limit],
    }


def get_entity_lookup() -> EntityLookup:
    """Return the singleton EntityLookup instance."""
    return EntityLookup.get_instance()
