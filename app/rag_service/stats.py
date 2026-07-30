"""RAG statistics."""

from __future__ import annotations

from typing import Any

from app.crypto_embeddings import COLLECTIONS, get_embedder
from app.rag_service.cache import _get_redis
from app.rag_service.state import _seeded


async def get_stats() -> dict[str, Any]:
    """Get RAG system statistics."""
    embedder = await get_embedder()
    r = await _get_redis()

    ann_stats = {}
    try:
        from app.ann_index import get_ann_index

        ann = get_ann_index()
        ann_stats = ann.stats()
    except (AttributeError, ImportError):
        pass

    collection_sizes = {}
    for coll in COLLECTIONS:
        ann_entry = ann_stats.get(coll, {})
        if ann_entry.get("built") and ann_entry.get("n_vectors", 0) > 0:
            collection_sizes[coll] = ann_entry["n_vectors"]
        else:
            try:
                size = await r.scard(f"rag:idx:{coll}")
                collection_sizes[coll] = size
            except (AttributeError, ImportError):
                collection_sizes[coll] = 0

    cache_stats = {}
    try:
        from app.semantic_cache import get_semantic_cache

        cache = get_semantic_cache()
        cache_stats = await cache.stats()
    except (AttributeError, ImportError):
        pass

    return {
        "embedder": embedder.stats,
        "collections": collection_sizes,
        "total_docs": sum(collection_sizes.values()),
        "seeded": _seeded,
        "ann_index": ann_stats,
        "semantic_cache": cache_stats,
    }
