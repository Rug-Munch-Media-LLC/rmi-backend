"""Search and retrieval pipeline for RAG."""

from __future__ import annotations
import asyncio
import json
import re
import time
from typing import Any

from app.core.logging import get_logger
from app.crypto_embeddings import COLLECTIONS, get_embedder
from app.rag_service.cache import _get_redis

logger = get_logger(__name__)

async def search_similar(
    query: str,
    collection: str = "wallet_profiles",
    limit: int = 10,
    min_similarity: float = 0.0,
) -> list[dict[str, Any]]:
    """Semantic search across any collection. Uses FAISS ANN index with semantic cache."""
    t0 = time.time()
    embedder = await get_embedder()
    query_vec = await embedder.embed_query(query)

    cache_hit = False
    try:
        from app.semantic_cache import get_semantic_cache

        cache = get_semantic_cache()
        cached = await cache.check(query_vec)
        if cached is not None:
            cache_hit = True
            try:
                from app.rag_observability import trace_retrieval

                trace_retrieval((time.time() - t0) * 1000, collection, len(cached), cache_hit=True)
            except (ImportError, AttributeError):
                pass
            return cached
    except (ConnectionError, OSError, TimeoutError):
        pass

    results = []
    try:
        from app.ann_index import get_ann_index

        ann = get_ann_index()
        if ann.is_built(collection):
            ann_results = await ann.search(
                query_embedding=query_vec,
                collection=collection,
                limit=limit,
                min_similarity=min_similarity,
            )
            if ann_results:
                needs_enrich = [r for r in ann_results if "content" not in r]
                enriched = (
                    await _enrich_ann_results(collection, needs_enrich) if needs_enrich else []
                )
                results = [r for r in ann_results if "content" in r] + enriched
        else:
            logger.info(f"FAISS not built for {collection}, using brute-force fallback")
            results = await embedder.search(
                query=query, collection=collection, limit=limit, min_similarity=min_similarity
            )
    except Exception as e:
        logger.warning(f"ANN/pgvector search failed, falling back to brute-force: {e}")
        results = await embedder.search(
            query=query, collection=collection, limit=limit, min_similarity=min_similarity
        )

    if results:
        try:
            from app.semantic_cache import get_semantic_cache

            cache = get_semantic_cache()
            await cache.store(query_vec, results)
        except (ConnectionError, OSError, TimeoutError):
            pass

    try:
        from app.rag_observability import trace_retrieval

        trace_retrieval((time.time() - t0) * 1000, collection, len(results), cache_hit=cache_hit)
    except (ImportError, AttributeError):
        pass

    return results

async def _enrich_ann_results(
    collection: str, ann_results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fetch full documents from Redis for ANN results."""
    if not ann_results:
        return []
    r = await _get_redis()
    pipe = r.pipeline()
    for ar in ann_results:
        pipe.get(f"rag:{collection}:{ar['id']}")
    raw_docs = await pipe.execute()
    enriched = []
    for ar, data in zip(ann_results, raw_docs, strict=False):
        if not data:
            enriched.append(ar)
            continue
        try:
            doc = json.loads(data)
            enriched.append(
                {
                    "id": doc.get("id", ar["id"]),
                    "similarity": ar["similarity"],
                    "content": doc.get("content", "")[:500],
                    "metadata": doc.get("metadata", {}),
                    "model": doc.get("model", ""),
                    "collection": doc.get("collection", collection),
                }
            )
        except json.JSONDecodeError:
            enriched.append(ar)
    return enriched

async def search_multi_collection(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
) -> list[dict[str, Any]]:
    """Search across multiple collections and merge results."""
    if collections is None:
        collections = COLLECTIONS

    async def _search_one(coll: str) -> list[dict[str, Any]]:
        try:
            results = await search_similar(
                query, collection=coll, limit=limit, min_similarity=min_similarity
            )
            for r in results:
                if "collection" not in r:
                    r["collection"] = coll
            return results
        except Exception as e:
            logger.warning(f"Search failed for {coll}: {e}")
            return []

    all_per_collection = await asyncio.gather(*[_search_one(c) for c in collections])
    all_results = [r for results in all_per_collection for r in results]
    all_results.sort(key=lambda x: x["similarity"], reverse=True)
    return all_results[:limit]

def _classify_query(query: str) -> str:
    """Classify query into entity_heavy, keyword_heavy, semantic_heavy, or balanced."""
    q = query.lower().strip()
    if re.search(r"0x[a-f0-9]{6,}", q) or re.search(r"\$[a-z]{2,10}", q):
        return "entity_heavy"
    words = q.split()
    if len(words) <= 3 and not any(
        w in q for w in ["what", "how", "why", "when", "explain", "describe", "compare", "analyze"]
    ):
        return "keyword_heavy"
    if any(
        w in q
        for w in [
            "what is",
            "how does",
            "why did",
            "explain",
            "describe",
            "compare",
            "analyze",
            "tell me",
            "show me",
            "find",
        ]
    ):
        return "semantic_heavy"
    return "balanced"

def _rrf_fuse(
    ranked_lists: list[list[dict[str, Any]]],
    k: int = 60,
    entity_boost: float = 1.5,
    entity_doc_ids: set | None = None,
    pillar_weights: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion of multiple result lists."""
    entity_doc_ids = entity_doc_ids or set()
    if pillar_weights is None:
        pillar_weights = [1.0] * len(ranked_lists)
    scores: dict[str, float] = {}
    doc_data: dict[str, dict[str, Any]] = {}

    for list_idx, rlist in enumerate(ranked_lists):
        weight = pillar_weights[list_idx] if list_idx < len(pillar_weights) else 1.0
        for rank, item in enumerate(rlist, start=1):
            doc_id = item.get("doc_id") or item.get("id")
            if not doc_id:
                continue
            rrf = weight / (k + rank)
            if doc_id in entity_doc_ids:
                rrf *= entity_boost
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf
            if doc_id not in doc_data:
                doc_data[doc_id] = item

    sorted_ids = sorted(scores, key=lambda d: scores[d], reverse=True)
    results = []
    for doc_id in sorted_ids:
        entry = dict(doc_data[doc_id])
        entry["rrf_score"] = round(scores[doc_id], 6)
        entry["entity_match"] = doc_id in entity_doc_ids
        results.append(entry)
    return results

async def three_pillar_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
    entity_boost: float = 1.5,
    use_mmr: bool = True,
    mmr_lambda: float = 0.6,
    use_kg: bool = True,
    use_parent_child: bool = True,
    use_reranker: bool = False,
) -> dict[str, Any]:
    """Three-pillar hybrid search with KG expansion, MMR dedup, and parent-child retrieval."""
    if collections is None:
        collections = COLLECTIONS
    limit = min(limit, 100)

    embedder = await get_embedder()
    query_vec = await embedder.embed_query(query)

    # ── Pillar 1: Dense vector search ──
    pillar1_results: list[dict[str, Any]] = []
    try:
        from app.semantic_cache import get_semantic_cache

        cache = get_semantic_cache()
        cached = await cache.check(query_vec)
        if cached is not None:
            pillar1_results = cached
            for r in pillar1_results:
                r["pillar"] = "dense (cached)"
        else:
            for coll in collections:
                try:
                    from app.ann_index import get_ann_index

                    ann = get_ann_index()
                    ann_hits = await ann.search(
                        query_embedding=query_vec,
                        collection=coll,
                        limit=limit,
                        min_similarity=min_similarity,
                    )
                    enriched = await _enrich_ann_results(coll, ann_hits)
                    for r in enriched:
                        r["pillar"] = "dense"
                    pillar1_results.extend(enriched)
                except (ConnectionError, OSError, TimeoutError) as e:
                    logger.debug(f"ANN search failed for {coll}: {e}")
                    try:
                        hits = await embedder.search(
                            query, coll, limit=limit, min_similarity=min_similarity
                        )
                        for r in hits:
                            r["pillar"] = "dense"
                        pillar1_results.extend(hits)
                    except (ConnectionError, OSError, TimeoutError) as e2:
                        logger.debug(f"Fallback search failed for {coll}: {e2}")
            if pillar1_results:
                await cache.store(query_vec, pillar1_results)
    except (ConnectionError, OSError, TimeoutError) as e:
        logger.debug(f"Semantic cache check failed: {e}")
        try:
            pillar1_results = await search_multi_collection(
                query, collections, limit=limit * 2, min_similarity=min_similarity
            )
            for r in pillar1_results:
                r["pillar"] = "dense"
        except Exception as e2:
            logger.debug(f"Full search fallback failed: {e2}")

    # ── Pillar 2: Sparse text search ──
    pillar2_results: list[dict[str, Any]] = []
    try:
        from app.splade_bm25 import splade_search

        pillar2_results = await splade_search(query, collections, limit=limit * 2)
        for r in pillar2_results:
            r["pillar"] = "sparse"
    except ImportError:
        try:
            pillar2_results = await _sparse_text_search(query, collections, limit=limit * 2)
            for r in pillar2_results:
                r["pillar"] = "sparse"
        except (ConnectionError, OSError, TimeoutError) as e:
            logger.debug(f"Sparse text search failed: {e}")
            pass
    except (ConnectionError, OSError, TimeoutError) as e:
        logger.debug(f"SPLADE search failed: {e}")
        try:
            pillar2_results = await _sparse_text_search(query, collections, limit=limit * 2)
            for r in pillar2_results:
                r["pillar"] = "sparse"
        except (ConnectionError, OSError, TimeoutError) as e2:
            logger.debug(f"Sparse text search fallback failed: {e2}")

    # ── Pillar 3: Entity exact-match + KG expansion ──
    pillar3_results: list[dict[str, Any]] = []
    entity_extraction = None
    entity_doc_ids: set = set()
    try:
        from app.entity_extraction import EntityLookup, extract_entities

        entity_extraction = extract_entities(query)
        if entity_extraction.has_entities:
            lookup = EntityLookup.get_instance()
            for entity in entity_extraction.all_entities():
                try:
                    matches = await lookup.lookup(entity["value"])
                    for m in matches:
                        m["pillar"] = "entity"
                    pillar3_results.extend(matches)
                except (ConnectionError, OSError, TimeoutError) as e:
                    logger.debug(f"Entity lookup failed: {e}")
            if use_kg:
                try:
                    from app.knowledge_graph import get_knowledge_graph

                    kg = await get_knowledge_graph()
                    kg_results = await kg.expand_query_entities(
                        entity_extraction.all_entities(), max_depth=2
                    )
                    if kg_results:
                        for r in kg_results:
                            r["pillar"] = "entity+kg"
                        pillar3_results.extend(kg_results)
                except (ConnectionError, OSError, TimeoutError) as e:
                    logger.debug(f"KG expansion failed: {e}")
        for r in pillar3_results:
            did = r.get("doc_id") or r.get("id")
            if did:
                entity_doc_ids.add(did)
    except Exception as e:
        logger.debug(f"Entity extraction failed: {e}")

    # ── RRF fusion ──
    query_type = _classify_query(query)
    WEIGHTS = {
        "entity_heavy": (0.3, 0.2, 0.5),
        "keyword_heavy": (0.3, 0.6, 0.1),
        "semantic_heavy": (0.7, 0.2, 0.1),
        "balanced": (0.4, 0.4, 0.2),
    }
    w_dense, w_sparse, w_entity = WEIGHTS.get(query_type, WEIGHTS["balanced"])

    p1_ranked = sorted(pillar1_results, key=lambda x: x.get("similarity", 0), reverse=True)
    p2_ranked = sorted(
        pillar2_results,
        key=lambda x: x.get("text_score", 0) or x.get("sparse_score", 0) or x.get("bm25_score", 0),
        reverse=True,
    )
    p3_ranked = sorted(pillar3_results, key=lambda x: x.get("score", 0), reverse=True)

    fused = _rrf_fuse(
        [p1_ranked, p2_ranked, p3_ranked],
        k=60,
        entity_boost=entity_boost,
        entity_doc_ids=entity_doc_ids,
        pillar_weights=[w_dense, w_sparse, w_entity],
    )

    # Pillar attribution
    pillar_map: dict[str, list[str]] = {}
    for r in pillar1_results:
        did = r.get("id") or r.get("doc_id")
        if did and "dense" not in pillar_map.setdefault(did, []):
            pillar_map[did].append("dense")
    for r in pillar2_results:
        did = r.get("id") or r.get("doc_id")
        if did and "sparse" not in pillar_map.setdefault(did, []):
            pillar_map[did].append("sparse")
    for r in pillar3_results:
        did = r.get("id") or r.get("doc_id")
        if did and "entity" not in pillar_map.setdefault(did, []):
            pillar_map[did].append("entity")
    for r in fused:
        did = r.get("id") or r.get("doc_id")
        r["pillars"] = pillar_map.get(did, ["dense"])
        r["match_type"] = "+".join(r["pillars"])

    # ── MMR deduplication ──
    pre_mmr_count = len(fused)
    mmr_applied = False
    if use_mmr and len(fused) > limit:
        try:
            from app.mmr_dedup import mmr_dedup_results

            fused = await mmr_dedup_results(
                fused,
                query_score_field="rrf_score",
                content_field="content",
                lambda_param=mmr_lambda,
                top_k=limit * 3,
            )
            mmr_applied = True
        except (ImportError, ConnectionError, OSError) as e:
            logger.debug(f"MMR dedup failed: {e}")

    # ── Cross-encoder reranking ──
    reranker_applied = False
    if use_reranker and fused:
        try:
            from app.cross_encoder_reranker import get_reranker

            for r in fused:
                r["similarity"] = r.get("rrf_score", 0.0)
            reranker = await get_reranker()
            fused = await reranker.rerank(query, fused, top_k=limit * 3)
            reranker_applied = True
        except (ImportError, ConnectionError, OSError) as e:
            logger.debug(f"Reranker failed: {e}")

    # ── Parent-child context expansion ──
    parent_child_applied = False
    if use_parent_child and fused:
        try:
            expanded_results = []
            for r in fused[: limit * 2]:
                content = r.get("content", "") or ""
                metadata = r.get("metadata", {}) or {}
                parent_id = metadata.get("parent_id") or r.get("parent_id")
                if parent_id:
                    try:
                        pr = await _get_redis()
                        coll = r.get("collection", "wallet_profiles")
                        parent_data = await pr.get(f"rag:{coll}:{parent_id}")
                        if parent_data:
                            pdoc = json.loads(parent_data)
                            r["parent_content"] = pdoc.get("content", "")[:2000]
                            r["parent_id"] = parent_id
                            parent_child_applied = True
                    except (ConnectionError, OSError, TimeoutError, json.JSONDecodeError) as e:
                        logger.debug(f"Parent fetch failed: {e}")
                if content and len(content) < 800:
                    r["chunk_type"] = "child"
                    r["retrieval_note"] = "Short chunk - consider parent context for generation"
                expanded_results.append(r)
            if expanded_results:
                fused = expanded_results
        except Exception as e:
            logger.debug(f"Parent-child expansion failed: {e}")

    final_results = fused[:limit]

    # ── Confidence scoring ──
    confidence = None
    try:
        from app.confidence import score_confidence

        entity_list = list(entity_doc_ids)[:5] if entity_doc_ids else []
        confidence = score_confidence(final_results, query=query, entity_matches=entity_list)
    except (ImportError, ConnectionError, OSError) as e:
        logger.debug(f"Confidence scoring failed: {e}")

    return {
        "results": final_results,
        "pillar_summary": {
            "dense_hits": len(pillar1_results),
            "sparse_hits": len(pillar2_results),
            "entity_hits": len(pillar3_results),
            "entity_doc_ids": list(entity_doc_ids),
            "pillars_used": [
                p
                for p, n in [
                    ("dense", len(pillar1_results)),
                    ("sparse", len(pillar2_results)),
                    ("entity", len(pillar3_results)),
                ]
                if n > 0
            ],
            "mmr_applied": mmr_applied,
            "mmr_lambda": mmr_lambda if mmr_applied else None,
            "pre_mmr_count": pre_mmr_count,
            "post_mmr_count": len(fused) if mmr_applied else pre_mmr_count,
            "kg_expansion": len([r for r in pillar3_results if r.get("pillar") == "entity+kg"]),
            "parent_child_applied": parent_child_applied,
            "reranker_applied": reranker_applied,
        },
        "entity_extraction": entity_extraction.to_dict() if entity_extraction else None,
        "query": query,
        "query_type": query_type,
        "fusion_weights": {"dense": w_dense, "sparse": w_sparse, "entity": w_entity},
        "collections": collections,
        "confidence": confidence,
    }

async def search_with_transform(
    query: str,
    collection: str = "wallet_profiles",
    limit: int = 10,
    strategy: str = "auto",
) -> dict[str, Any]:
    """Search with query transformation."""
    from app.query_transform import transform_query

    transformed = await transform_query(query, strategy=strategy)
    all_result_lists = []
    for tq in transformed.transformed_queries:
        try:
            results = await search_similar(
                tq, collection=collection, limit=limit, min_similarity=0.3
            )
            if results:
                all_result_lists.append(results)
        except (ConnectionError, OSError, TimeoutError) as e:
            logger.debug(f"Query transform search failed: {e}")

    if len(all_result_lists) <= 1:
        results = all_result_lists[0] if all_result_lists else []
        return {
            "query": query,
            "collection": collection,
            "results": results[:limit],
            "total": len(results),
            "strategy": transformed.strategy,
            "transformed_queries": transformed.transformed_queries,
            "metadata": transformed.metadata,
        }

    rrf_k = 60
    scores: dict[str, float] = {}
    doc_data: dict[str, dict[str, Any]] = {}
    for rlist in all_result_lists:
        sorted_list = sorted(rlist, key=lambda x: x.get("similarity", 0), reverse=True)
        for rank, item in enumerate(sorted_list, start=1):
            doc_id = item.get("doc_id") or item.get("id")
            if not doc_id:
                continue
            rrf = 1.0 / (rrf_k + rank)
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf
            if doc_id not in doc_data:
                doc_data[doc_id] = item
    sorted_ids = sorted(scores, key=lambda d: scores[d], reverse=True)
    merged = []
    for doc_id in sorted_ids:
        entry = dict(doc_data[doc_id])
        entry["rrf_score"] = round(scores[doc_id], 6)
        merged.append(entry)
    return {
        "query": query,
        "collection": collection,
        "results": merged[:limit],
        "total": len(merged),
        "strategy": transformed.strategy,
        "transformed_queries": transformed.transformed_queries,
        "metadata": transformed.metadata,
    }

async def _sparse_text_search(
    query: str, collections: list[str], limit: int = 20
) -> list[dict[str, Any]]:
    """Simple keyword-based text search over Redis-stored documents."""
    r = await _get_redis()
    query_tokens = set(query.lower().split())
    if not query_tokens:
        return []
    results = []
    for coll in collections:
        try:
            doc_ids = list(await r.smembers(f"rag:idx:{coll}"))
            if not doc_ids:
                continue
            sample_ids = doc_ids[:200]
            pipe = r.pipeline()
            for k in [f"rag:{coll}:{did}" for did in sample_ids]:
                pipe.get(k)
            raw_docs = await pipe.execute()
            for data in raw_docs:
                if not data:
                    continue
                try:
                    doc = json.loads(data)
                except json.JSONDecodeError:
                    continue
                content = doc.get("content", "").lower()
                if not content:
                    continue
                content_tokens = set(content.split())
                matching = query_tokens & content_tokens
                if not matching:
                    continue
                score = len(matching) / (len(query_tokens) + 0.5)
                if query.lower() in content:
                    score *= 2.0
                results.append(
                    {
                        "id": doc.get("id", ""),
                        "doc_id": doc.get("id", ""),
                        "text_score": round(score, 4),
                        "content": doc.get("content", "")[:500],
                        "metadata": doc.get("metadata", {}),
                        "collection": coll,
                    }
                )
        except (ConnectionError, OSError, TimeoutError):
            pass
    results.sort(key=lambda x: x["text_score"], reverse=True)
    return results[:limit]
