#!/usr/bin/env python3
"""
MMR (Maximal Marginal Relevance) Deduplication for RRF Results
================================================================
Re-ranks fused RRF results to maximize relevance while minimizing redundancy.

MMR formula:
  MMR = argmax_{d∈R\\S} [ λ·sim(d,q) - (1-λ)·max_{d'∈S} sim(d,d') ]

Where:
  R = all candidate results
  S = already-selected results
  q = original query
  sim(d,q) = relevance to query (RRF score)
  sim(d,d') = similarity between documents (embedding cosine)
  λ = trade-off (0.5-0.7 typical; higher = more diverse)

Used after _rrf_fuse() in rag_service.py to ensure the final
top-K results contain minimal duplicate/near-duplicate content.
"""

import contextlib
import json
from app.core.logging import get_logger
import math
from typing import Any

logger = get_logger(__name__)


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0

    # Handle different-length vectors by truncating to shorter
    min_len = min(len(vec_a), len(vec_b))
    a = vec_a[:min_len]
    b = vec_b[:min_len]

    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))

    if mag_a == 0 or mag_b == 0:
        return 0.0

    return dot / (mag_a * mag_b)


def text_jaccard_similarity(text_a: str, text_b: str) -> float:
    """
    Token-level Jaccard similarity for text dedup when embeddings unavailable.
    Fast and good enough for surface-level dedup.
    """
    if not text_a or not text_b:
        return 0.0

    # Tokenize with simple split + lowercase
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())

    if not tokens_a or not tokens_b:
        return 0.0

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b

    return len(intersection) / len(union) if union else 0.0


def content_hash_dedup(
    results: list[dict[str, Any]],
    hash_field: str = "doc_id",
    id_field: str = "id",
) -> list[dict[str, Any]]:
    """
    Remove exact duplicates based on content hash or ID.
    Fast pre-filter before MMR.
    """
    seen: set[str] = set()
    deduped = []

    for r in results:
        # Use doc_id or id as dedup key
        key = r.get(hash_field) or r.get(id_field) or ""
        if not key:
            # Fallback: hash the content
            content = r.get("content", "")[:500]
            key = f"hash:{hash(content)}" if content else ""

        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(r)

    return deduped


def mmr_rerank(
    results: list[dict[str, Any]],
    query_score_field: str = "rrf_score",
    content_field: str = "content",
    embedding_field: str = "embedding",
    lambda_param: float = 0.6,
    top_k: int = 20,
    similarity_threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """
    Maximal Marginal Relevance reranking.

    Args:
        results: fused results from _rrf_fuse(), sorted by rrf_score desc
        query_score_field: field name for query relevance (rrf_score)
        content_field: field name for text content (used for Jaccard sim)
        embedding_field: field name for vector embeddings (used for cosine sim)
        lambda_param: relevance-diversity trade-off (0=all diverse, 1=all relevant)
        top_k: number of results to return
        similarity_threshold: skip MMR for results below this relevance threshold

    Returns:
        Re-ranked list with up to top_k results, maximizing relevance + diversity.
    """
    if not results:
        return []

    # Pre-filter: remove exact duplicates
    deduped = content_hash_dedup(results)
    if len(deduped) <= 1:
        return deduped[:top_k]

    # Normalize query relevance scores to [0, 1]
    max_score = max(r.get(query_score_field, 0) for r in deduped)
    if max_score == 0:
        return deduped[:top_k]

    for r in deduped:
        r["_mmr_relevance"] = r.get(query_score_field, 0) / max_score

    # Greedy MMR selection
    selected: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = list(deduped)

    # First result: highest relevance
    remaining.sort(key=lambda x: x.get("_mmr_relevance", 0), reverse=True)
    selected.append(remaining.pop(0))

    while len(selected) < top_k and remaining:
        best_mmr = -1.0
        best_idx = 0

        for i, candidate in enumerate(remaining):
            relevance = candidate.get("_mmr_relevance", 0)

            # Skip candidates below threshold
            if relevance < 0.01:
                continue

            # Maximum similarity to already-selected results
            max_sim = 0.0
            for sel in selected:
                # Try embedding similarity first (most accurate)
                c_emb = candidate.get(embedding_field)
                s_emb = sel.get(embedding_field)
                if c_emb and s_emb and isinstance(c_emb, list) and isinstance(s_emb, list):
                    sim = cosine_similarity(c_emb, s_emb)
                else:
                    # Fall back to text Jaccard similarity
                    c_text = candidate.get(content_field, "") or ""
                    s_text = sel.get(content_field, "") or ""
                    sim = text_jaccard_similarity(c_text, s_text)

                max_sim = max(max_sim, sim)

            # MMR score: λ·relevance - (1-λ)·max_similarity
            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_sim

            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        if best_mmr <= -1.0:
            # No valid candidates left
            break

        selected.append(remaining.pop(best_idx))

    # Clean up temporary fields
    for r in deduped:
        r.pop("_mmr_relevance", None)

    # Add MMR metadata
    for i, r in enumerate(selected):
        r["mmr_rank"] = i + 1
        r["mmr_selected"] = True

    return selected


async def mmr_dedup_results(
    results: list[dict[str, Any]],
    query_score_field: str = "rrf_score",
    content_field: str = "content",
    lambda_param: float = 0.6,
    top_k: int = 20,
) -> list[dict[str, Any]]:
    """
    Async wrapper for MMR deduplication.
    This is the main entry point used by rag_service.py.

    Attempts to load embeddings from Redis for similarity computation.
    Falls back to text Jaccard if embeddings are unavailable.
    """
    try:
        # Try to enrich results with embeddings from Redis for better dedup
        enriched = await _enrich_with_embeddings(results)
        return mmr_rerank(
            enriched,
            query_score_field=query_score_field,
            content_field=content_field,
            embedding_field="_embedding",
            lambda_param=lambda_param,
            top_k=top_k,
        )
    except Exception as e:
        # Fall back to text-only dedup
        logger.debug(f"MMR embedding enrichment failed, using text-only: {e}")
        return mmr_rerank(
            results,
            query_score_field=query_score_field,
            content_field=content_field,
            lambda_param=lambda_param,
            top_k=top_k,
        )


async def _enrich_with_embeddings(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Try to load embedding vectors from Redis for each result.
    Used for more accurate MMR similarity computation.
    Falls back gracefully if Redis/embeddings aren't available.
    """
    import os

    import redis.asyncio as aioredis

    if not results:
        return results

    # Only enrich if we have doc_ids and collection info
    docs_with_ids = [(i, r) for i, r in enumerate(results) if r.get("doc_id") or r.get("id")]

    if not docs_with_ids:
        return results

    r = None
    try:
        r = aioredis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=False,  # binary for vectors
        )

        # Batch fetch embeddings
        pipe = r.pipeline()
        keys_to_fetch = []
        for idx, result in docs_with_ids:
            doc_id = result.get("doc_id") or result.get("id")
            coll = result.get("collection", "wallet_profiles")
            key = f"rag:{coll}:{doc_id}"
            pipe.get(key)
            keys_to_fetch.append((idx, key))

        raw_docs = await pipe.execute()

        for (idx, key), data in zip(keys_to_fetch, raw_docs, strict=False):  # noqa: B007
            if data:
                try:
                    doc = json.loads(data)
                    embedding = doc.get("vector", [])
                    if isinstance(embedding, list) and len(embedding) > 0:
                        # Store embedding for MMR similarity computation
                        # Use first 128 dims for speed (cosine sim is preserved in high dims)
                        results[idx]["_embedding"] = embedding[:128]
                except (json.JSONDecodeError, Exception):
                    pass

    except Exception as e:
        logger.debug(f"Embedding enrichment from Redis failed: {e}")
    finally:
        if r is not None:
            with contextlib.suppress(Exception):
                await r.aclose()

    return results


def cluster_similar_results(
    results: list[dict[str, Any]],
    content_field: str = "content",
    similarity_threshold: float = 0.7,
) -> list[list[dict[str, Any]]]:
    """
    Group results into clusters of similar content.
    Each cluster contains near-duplicate results.

    Returns list of clusters, where each cluster is a list of similar results.
    The first result in each cluster is the "representative" (highest score).
    """
    if not results:
        return []

    clusters: list[list[dict[str, Any]]] = []
    assigned: set[int] = set()

    for i, result in enumerate(results):
        if i in assigned:
            continue

        cluster = [result]
        assigned.add(i)

        for j, other in enumerate(results):
            if j in assigned or j == i:
                continue

            sim = text_jaccard_similarity(
                result.get(content_field, ""),
                other.get(content_field, ""),
            )
            if sim >= similarity_threshold:
                cluster.append(other)
                assigned.add(j)

        clusters.append(cluster)

    # Sort clusters by highest-scoring result
    clusters.sort(key=lambda c: max(r.get("rrf_score", 0) for r in c), reverse=True)

    return clusters
