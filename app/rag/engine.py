"""M3 RAG Engine - three-pillar search + ingest.

The missing module that app/rag/service.py was importing (the legacy
app.rag_engine was nuked during consolidation but never replaced).

Three-Pillar Search (per 2026 RAG standards):
  Pillar 1: ANN vector similarity (semantic match)
  Pillar 2: BM25-lite keyword search (lexical match)
  Pillar 3: Metadata filter (structured constraints)
  Fusion: Reciprocal Rank Fusion (RRF) - k=60

Ingest:
  - Chunk → embed → store in ANN index + Redis doc store
  - SHA-256 dedup per collection
  - Per-chunk doc_id = "{collection}:{hash}:{idx}"
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.rag.ann_index import Hit, get_index
from app.rag.chunking import chunk_text, content_hash, is_duplicate, mark_ingested
from app.rag.embeddings import current_backend, get_embedding

log = get_logger(__name__)

# RRF constant
_RRF_K = 60


# ── Three-pillar search ──────────────────────────────────────────────
async def three_pillar_search(
    query: str,
    collection: str = "scam_intel",
    top_k: int = 5,
    min_similarity: float = 0.0,
    filters: dict[str, Any] | None = None,
) -> list[dict]:
    """Run the three-pillar search and return RRF-fused hits.

    Each returned hit: {doc_id, score, text, metadata, source_pillars}
    """
    start = time.monotonic()

    # Pillar 1: ANN vector search
    qvec = await get_embedding(query)
    idx = get_index(collection)
    ann_hits = idx.search(qvec, top_k=top_k * 3, min_similarity=min_similarity)

    # Pillar 2: keyword (BM25-lite via Redis text scan on stored docs)
    keyword_hits = await _keyword_search(query, collection, top_k * 3)

    # Pillar 3: metadata filter (apply to ann+keyword results)
    _apply_filters(ann_hits + keyword_hits, filters or {})

    # RRF fusion
    fused = _reciprocal_rank_fusion([ann_hits, keyword_hits], top_k=top_k)

    # Apply filters post-fusion
    if filters:
        fused = [h for h in fused if _matches_filters(h, filters)]

    took_ms = int((time.monotonic() - start) * 1000)
    log.info(
        "rag_search_done collection=%s ann=%d kw=%d fused=%d took_ms=%d backend=%s",
        collection,
        len(ann_hits),
        len(keyword_hits),
        len(fused),
        took_ms,
        current_backend(),
    )
    return [
        {
            "doc_id": h.doc_id,
            "score": h.score,
            "text": h.text,
            "metadata": h.metadata,
        }
        for h in fused
    ]


async def _keyword_search(query: str, collection: str, limit: int) -> list[Hit]:
    """BM25-lite keyword search. Simple TF scoring on stored text.

    Returns Hits with score in [0, 1] (normalized). We don't pretend this
    is real BM25 - but it's good enough for a fallback that surfaces
    lexically-matching docs the ANN might miss.
    """
    try:
        from app.core.redis import get_redis

        r = get_redis()
        # Pull all doc texts for this collection's ANN store
        raw = r.hgetall(f"rag:ann:{collection}:docs")
    except Exception as e:
        log.debug("keyword_search_redis_failed: %s", e)
        return []

    if not raw:
        return []

    import json

    terms = [t.lower() for t in query.split() if len(t) > 2]
    if not terms:
        return []

    scored: list[tuple[float, str, str, dict]] = []
    for doc_id, blob in raw.items():
        try:
            entry = json.loads(blob)
        except Exception:
            continue
        text = entry.get("text", "")
        if not text:
            continue
        text_l = text.lower()
        # Simple TF
        tf = sum(text_l.count(t) for t in terms)
        if tf == 0:
            continue
        # Normalize by length
        score = tf / max(10, len(text_l.split()))
        scored.append((score, doc_id, text, entry.get("metadata", {})))

    scored.sort(key=lambda x: -x[0])
    out: list[Hit] = []
    max_score = scored[0][0] if scored else 1.0
    for score, doc_id, text, metadata in scored[:limit]:
        out.append(
            Hit(
                doc_id=doc_id,
                score=min(1.0, score / max_score) if max_score > 0 else 0.0,
                text=text,
                metadata=metadata,
            )
        )
    return out


def _apply_filters(hits: list[Hit], filters: dict[str, Any]) -> list[Hit]:
    """Pre-filter: keep hits whose metadata matches all filter key=val pairs."""
    if not filters:
        return hits
    return [h for h in hits if _matches_filters(h, filters)]


def _matches_filters(hit: Hit, filters: dict[str, Any]) -> bool:
    return all(hit.metadata.get(k) == v for k, v in filters.items())


def _reciprocal_rank_fusion(pillar_hits: list[list[Hit]], top_k: int, k: int = _RRF_K) -> list[Hit]:
    """Reciprocal Rank Fusion across multiple ranked lists.

    RRF score(d) = sum( 1 / (k + rank_i(d)) ) for each pillar that contains d.
    """
    scores: dict[str, float] = {}
    by_id: dict[str, Hit] = {}
    for pillar in pillar_hits:
        for rank, h in enumerate(pillar, start=1):
            scores[h.doc_id] = scores.get(h.doc_id, 0.0) + 1.0 / (k + rank)
            if h.doc_id not in by_id:
                by_id[h.doc_id] = h

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    out: list[Hit] = []
    for doc_id, rrf_score in ranked[:top_k]:
        h = by_id[doc_id]
        # Normalize to [0, 1] roughly - RRF max is ~3/k for 3 pillars
        norm = min(1.0, rrf_score * k / 3.0)
        out.append(
            Hit(
                doc_id=h.doc_id,
                score=norm,
                text=h.text,
                metadata=h.metadata,
            )
        )
    return out


# ── Ingest ───────────────────────────────────────────────────────────
async def ingest_document(
    collection: str,
    doc_id: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    chunk: bool = True,
) -> dict:
    """Ingest a document into the RAG system.

    - Chunks the content (recursive split, dedup)
    - Embeds each chunk
    - Adds to the collection's ANN index
    - Marks each chunk's hash as ingested for dedup
    """
    if not content or not content.strip():
        return {"doc_id": doc_id, "collection": collection, "status": "empty", "chunks": 0}

    metadata = metadata or {}
    idx = get_index(collection)
    chunks = (
        chunk_text(content)
        if chunk
        else [
            # Single-chunk path: still dedup
            __import__("app.rag.chunking", fromlist=["Chunk"]).Chunk(
                text=content, content_hash=content_hash(content), index=0, quality=1.0
            )
        ]
    )

    # Dedup
    new_chunks = [c for c in chunks if not is_duplicate(c.content_hash, collection)]
    skipped = len(chunks) - len(new_chunks)
    if not new_chunks:
        return {
            "doc_id": doc_id,
            "collection": collection,
            "status": "duplicate",
            "chunks": 0,
            "skipped": skipped,
        }

    # Embed + insert
    added = 0
    for c in new_chunks:
        vec = await get_embedding(c.text)
        chunk_doc_id = f"{doc_id}:{c.content_hash[:8]}:{c.index}"
        chunk_meta = {
            **metadata,
            "chunk_index": c.index,
            "content_hash": c.content_hash,
            "quality": c.quality,
        }
        idx.add(chunk_doc_id, vec, chunk_meta, text=c.text)
        mark_ingested(c.content_hash, collection)
        added += 1

    return {
        "doc_id": doc_id,
        "collection": collection,
        "status": "ok",
        "chunks": added,
        "skipped": skipped,
    }


# ── Stats ────────────────────────────────────────────────────────────
def get_collection_stats(collection: str) -> dict:
    """Get stats for one collection: vector count + dedup hashes count."""
    idx = get_index(collection)
    count = idx.count()
    try:
        from app.core.redis import get_redis

        hashes = get_redis().scard(f"rag:hashes:{collection}")
    except Exception:
        hashes = 0
    return {
        "collection": collection,
        "vector_count": count,
        "dedup_hashes": hashes,
    }


def get_stats(collections: list[str] | None = None) -> dict:
    """Get stats for all (or specified) collections + the active embedder backend."""
    from app.rag.models import COLLECTIONS as DEFAULT_COLLECTIONS

    cols = collections or DEFAULT_COLLECTIONS
    return {
        "total_docs": sum(get_collection_stats(c)["vector_count"] for c in cols),
        "backend": current_backend(),
        "collections": [get_collection_stats(c) for c in cols],
    }


# ── Background helpers (used by ingest_cron worker) ──────────────────
async def bulk_ingest(
    items: list[dict],
    collection: str = "scam_intel",
) -> dict:
    """Ingest many items sequentially. Returns summary counts."""
    if not items:
        return {"total": 0, "ok": 0, "duplicate": 0, "empty": 0, "errors": 0}

    counts = {"total": len(items), "ok": 0, "duplicate": 0, "empty": 0, "errors": 0}
    for it in items:
        try:
            r = await ingest_document(
                collection=collection,
                doc_id=it.get("doc_id") or it.get("id", f"bulk:{int(time.time() * 1000)}"),
                content=it.get("content") or it.get("text", ""),
                metadata=it.get("metadata") or {},
            )
            if r["status"] == "ok":
                counts["ok"] += 1
            elif r["status"] == "duplicate":
                counts["duplicate"] += 1
            elif r["status"] == "empty":
                counts["empty"] += 1
        except Exception as e:
            log.warning("bulk_ingest_item_failed: %s", e)
            counts["errors"] += 1
    return counts
