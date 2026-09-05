#!/usr/bin/env python3
"""
FAISS-based ANN Index Manager for RMI RAG
==========================================
Replaces O(n) brute-force Redis cosine scan with sub-millisecond
FAISS HNSW / IVFFlat approximate nearest-neighbor search.

Architecture:
  - Loads all vectors from Redis for each collection into a FAISS index
  - Keeps index in memory; auto-rebuilds when stale
  - Persists pickled indexes to /app/data/faiss/{collection}.index
  - Tracks version counter in Redis key rag:idx_version:{collection}
  - Invalidate on new ingestion (version bump)
"""

from __future__ import annotations

import asyncio
import json
import os
import pickle
import time
from typing import Any

import numpy as np

from app.core.logging import get_logger

logger = get_logger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

FAISS_DATA_DIR = os.getenv("FAISS_DATA_DIR", "/app/data/faiss")

# Qdrant fallback collection for the dual-backend search. Wallet
# embeddings migrated from FAISS live in this collection.
WALLET_COLLECTION = os.getenv("WALLET_EMBEDDINGS_COLLECTION", "wallet_embeddings")

# HNSW defaults
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 200
HNSW_EF_SEARCH = 128

# IVFFlat defaults
IVF_LISTS_FACTOR = 40  # lists = n_vectors / factor, min 4
IVF_NPROBE = 16

# Minimum docs to use IVFFlat/HNSW; below this, flat search is fine
MIN_DOCS_FOR_ANN = 50


class ANNIndex:
    """
    FAISS-backed approximate nearest-neighbor index manager.

    Each collection gets its own FAISS index built from Redis-stored
    vectors.  The index is kept in process memory and persisted to
    disk so it survives restarts.

    Usage:
        idx = ANNIndex()
        await idx.build_index("scam_patterns")
        results = idx.search(query_embedding, "scam_patterns", limit=10)
    """

    _instance: ANNIndex | None = None

    def __init__(self) -> None:
        self._indexes: dict[str, Any] = {}  # collection -> faiss index
        self._id_maps: dict[str, list[str]] = {}  # collection -> [doc_id, ...]
        self._meta: dict[str, dict[str, Any]] = {}  # collection -> build metadata
        self._redis: Any = None

    @classmethod
    def get_instance(cls) -> ANNIndex:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def _get_redis(self) -> Any:
        import redis.asyncio as aioredis

        if self._redis is None:
            self._redis = aioredis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD or None,
                db=0,
                decode_responses=True,
            )
        return self._redis

    # ── Build ────────────────────────────────────────────────────

    async def build_index(self, collection: str, force: bool = False) -> dict[str, Any]:
        """
        Build (or rebuild) a FAISS index for *collection*.

        Reads all documents from Redis rag:{collection}:* and builds
        an HNSW or IVFFlat index depending on document count.

        Returns build metadata dict.
        """
        # Skip if fresh enough (unless forced)
        if not force and self.is_built(collection):
            version_redis = await self._get_version(collection)
            version_local = self._meta.get(collection, {}).get("version", -1)
            if version_redis == version_local:
                logger.info(f"ANN index for {collection} is fresh (v{version_local})")
                return self._meta.get(collection, {})

        r = await self._get_redis()

        # Fetch all document IDs
        doc_ids = list(await r.smembers(f"rag:idx:{collection}"))
        n = len(doc_ids)
        if n == 0:
            logger.warning(f"No documents found for {collection}")
            self._meta[collection] = {"status": "empty", "n": 0, "collection": collection}
            return self._meta[collection]

        # Batch-fetch documents
        keys = [f"rag:{collection}:{did}" for did in doc_ids]
        pipe = r.pipeline()
        for k in keys:
            pipe.get(k)
        raw_docs = await pipe.execute()

        # Extract vectors and metadata; track dimension
        vectors = []
        valid_ids = []
        dims = 0
        for i, data in enumerate(raw_docs):
            if not data:
                continue
            try:
                doc = json.loads(data)
            except json.JSONDecodeError:
                continue
            vec = doc.get("vector", [])
            # Handle JSON-string vectors (from hash re-embed)
            if isinstance(vec, str):
                try:
                    vec = json.loads(vec)
                except (json.JSONDecodeError, TypeError):
                    continue
            if not vec or not isinstance(vec, list):
                continue
            if dims == 0:
                dims = len(vec)
            if len(vec) != dims:
                # Pad or truncate to match first vector's dimension
                vec = vec + [0.0] * (dims - len(vec)) if len(vec) < dims else vec[:dims]
            vectors.append(vec)
            valid_ids.append(doc_ids[i])

        n_valid = len(vectors)
        if n_valid == 0:
            logger.warning(f"No valid vectors for {collection}")
            self._meta[collection] = {"status": "no_vectors", "n": 0, "collection": collection}
            return self._meta[collection]

        mat = np.array(vectors, dtype=np.float32)

        # Choose index type
        import faiss

        if n_valid < MIN_DOCS_FOR_ANN:
            # Flat index - exact search, small collection
            index: Any = faiss.IndexFlatIP(dims)  # inner product (cosine after norm)
            index_type = "flat"
        else:
            # Normalize vectors for cosine similarity via inner product
            faiss.normalize_L2(mat)

            # Try HNSW first (best quality, no training needed)
            try:
                index = faiss.IndexHNSWFlat(dims, HNSW_M, faiss.METRIC_INNER_PRODUCT)
                index.hnsw.efConstruction = HNSW_EF_CONSTRUCTION
                index.hnsw.efSearch = HNSW_EF_SEARCH
                index_type = "hnsw"
                logger.info(f"Building HNSW index for {collection}: {n_valid} vectors, {dims}d")
            except Exception as e:
                logger.warning(f"HNSW failed, falling back to IVFFlat: {e}")
                # IVFFlat fallback
                nlist = max(4, n_valid // IVF_LISTS_FACTOR)
                quantizer = faiss.IndexFlatIP(dims)
                index = faiss.IndexIVFFlat(quantizer, dims, nlist, faiss.METRIC_INNER_PRODUCT)
                index.nprobe = IVF_NPROBE
                index.train(mat)
                index_type = "ivfflat"

        # Normalize for cosine via inner product (skip if already done for HNSW path)
        if index_type == "flat":
            faiss.normalize_L2(mat)

        index.add(mat)

        # Store in memory
        self._indexes[collection] = index
        self._id_maps[collection] = valid_ids

        version = await self._get_version(collection)

        # Persist to disk
        os.makedirs(FAISS_DATA_DIR, exist_ok=True)
        index_path = os.path.join(FAISS_DATA_DIR, f"{collection}.index")
        try:
            # faiss indexes can be serialized directly
            faiss.write_index(index, index_path)
            # Save id_map alongside
            id_map_path = os.path.join(FAISS_DATA_DIR, f"{collection}.ids")
            with open(id_map_path, "wb") as f:
                pickle.dump(valid_ids, f)
            logger.info(f"Persisted FAISS index to {index_path}")
        except Exception as e:
            logger.warning(f"Failed to persist FAISS index: {e}")

        build_meta = {
            "status": "built",
            "collection": collection,
            "n": n_valid,
            "dims": dims,
            "index_type": index_type,
            "version": version,
            "built_at": time.time(),
            "persisted": os.path.exists(index_path),
        }
        self._meta[collection] = build_meta
        logger.info(f"ANN index built: {collection} ({n_valid} docs, {dims}d, {index_type})")
        return build_meta

    # ── Load from disk ────────────────────────────────────────────

    def _load_from_disk(self, collection: str) -> bool:
        """Try to load a persisted FAISS index and id_map from disk."""
        import faiss

        index_path = os.path.join(FAISS_DATA_DIR, f"{collection}.index")
        id_map_path = os.path.join(FAISS_DATA_DIR, f"{collection}.ids")

        if not os.path.exists(index_path) or not os.path.exists(id_map_path):
            return False

        try:
            index = faiss.read_index(index_path)
            try:
                with open(id_map_path) as f:
                    id_list = json.load(f)
            except (ValueError, UnicodeDecodeError):
                logger.warning("Loading legacy pickle artifact — re-export to new format needed")
                with open(id_map_path, "rb") as f:
                    id_list = pickle.load(f)
            self._indexes[collection] = index
            self._id_maps[collection] = id_list
            self._meta[collection] = {
                "status": "loaded",
                "collection": collection,
                "n": len(id_list),
                "dims": index.d,
                "index_type": type(index).__name__,
                "loaded_at": time.time(),
            }
            logger.info(f"Loaded FAISS index for {collection} from disk ({len(id_list)} vectors)")
            return True
        except Exception as e:
            logger.warning(f"Failed to load FAISS index from disk: {e}")
            return False

    # ── Search ────────────────────────────────────────────────────

    async def search(
        self,
        query_embedding: list[float],
        collection: str,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """
        ANN search: find top-k documents similar to query_embedding.

        Auto-builds the index on first search if not yet built.
        Hydrates results with content/metadata from Redis.
        Returns list of {id, similarity, content, metadata, source, severity} dicts.
        """
        # Auto-build if needed
        if not self.is_built(collection):
            # Try disk first, then build from Redis (disk I/O offloaded to thread)
            loaded = await asyncio.to_thread(self._load_from_disk, collection)
            if not loaded:
                await self.build_index(collection)

        if not self.is_built(collection):
            logger.warning(f"No ANN index available for {collection}")
            return []

        index = self._indexes[collection]
        id_list = self._id_maps[collection]
        dims = index.d

        # Prepare query vector
        q = np.array([query_embedding[:dims]], dtype=np.float32)
        # Pad if query is shorter
        if q.shape[1] < dims:
            q = np.pad(q, ((0, 0), (0, dims - q.shape[1])))
        # Truncate if query is longer
        if q.shape[1] > dims:
            q = q[:, :dims]

        # Normalize for cosine via inner product
        import faiss

        faiss.normalize_L2(q)

        # Search
        search_k = min(limit * 2, len(id_list))  # fetch extra for filtering
        distances, indices = index.search(q, search_k)

        # Collect matching doc IDs for hydration
        raw_hits = []
        for rank, (dist, idx) in enumerate(zip(distances[0], indices[0], strict=False)):
            if idx < 0:
                continue  # FAISS returns -1 for empty slots
            sim = float(dist)  # inner product on normalized vectors = cosine similarity
            if sim < min_similarity:
                continue
            doc_id = id_list[idx] if idx < len(id_list) else f"unknown_{idx}"
            raw_hits.append((doc_id, sim, rank))

        if not raw_hits:
            return []

        # Hydrate from Redis - batch-fetch all matched docs
        r = await self._get_redis()
        keys = [f"rag:{collection}:{doc_id}" for doc_id, _, _ in raw_hits]
        pipe = r.pipeline()
        for k in keys:
            pipe.get(k)
        raw_docs = await pipe.execute()

        results = []
        for (doc_id, sim, rank), data in zip(raw_hits, raw_docs, strict=False):
            result = {
                "id": doc_id,
                "similarity": round(sim, 4),
                "rank": rank,
            }
            if data:
                try:
                    doc = json.loads(data)
                    result["content"] = doc.get("content", "")[:500]
                    result["metadata"] = doc.get("metadata", {})
                    result["source"] = doc.get("source", "")
                    result["severity"] = doc.get("severity", "")
                except json.JSONDecodeError:
                    pass
            results.append(result)

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]

    # ── Status ────────────────────────────────────────────────────

    def is_built(self, collection: str) -> bool:
        """Return True if an in-memory index exists for the collection."""
        return collection in self._indexes and collection in self._id_maps

    def stats(self) -> dict[str, Any]:
        """Return stats for all loaded indexes."""
        out = {}
        for coll in set(list(self._indexes.keys()) + list(self._meta.keys())):
            idx = self._indexes.get(coll)
            out[coll] = {
                "built": coll in self._indexes,
                "n_vectors": len(self._id_maps.get(coll, [])),
                "dims": idx.d if idx else 0,
                "index_type": type(idx).__name__ if idx else "none",
                **self._meta.get(coll, {}),
            }
        return out

    # ── Version tracking ─────────────────────────────────────────

    async def _get_version(self, collection: str) -> int:
        """Get the current version counter from Redis."""
        r = await self._get_redis()
        val = await r.get(f"rag:idx_version:{collection}")
        return int(val) if val else 0

    async def bump_version(self, collection: str) -> int:
        """
        Bump the version counter (call after new ingestion).
        This signals that the index needs rebuilding.
        """
        r = await self._get_redis()
        new_ver = await r.incr(f"rag:idx_version:{collection}")
        # Invalidate in-memory index
        self._indexes.pop(collection, None)
        self._id_maps.pop(collection, None)
        logger.info(f"Version bumped for {collection}: now v{new_ver}")
        return new_ver

    # ── Invalidate ────────────────────────────────────────────────

    def invalidate(self, collection: str) -> None:
        """Drop the in-memory index for *collection* (next search will rebuild)."""
        self._indexes.pop(collection, None)
        self._id_maps.pop(collection, None)
        self._meta.pop(collection, None)
        logger.info(f"Invalidated ANN index for {collection}")


# ══════════════════════════════════════════════════════════════════════
# Singleton accessor
# ══════════════════════════════════════════════════════════════════════

_ann_index: ANNIndex | None = None


def get_ann_index() -> ANNIndex:
    """Return the singleton ANNIndex instance."""
    global _ann_index
    if _ann_index is None:
        _ann_index = ANNIndex()
    return _ann_index


# ══════════════════════════════════════════════════════════════════════
# Dual-backend search: Qdrant primary, FAISS fallback
#
# RugMaps FAISS → Qdrant migration (tactical step). The module-level
# ``search()`` mirrors the FAISS search shape but routes through Qdrant
# first. Any Qdrant failure (unreachable, missing collection, reject)
# falls back to the legacy in-process FAISS index so callers never
# observe a hard error during the migration window.
# ══════════════════════════════════════════════════════════════════════


async def _qdrant_search(
    query_vector: list[float],
    k: int = 10,
    collection: str = WALLET_COLLECTION,
    min_similarity: float = 0.0,
) -> list[dict[str, Any]]:
    """Search via the Qdrant ANN backend (app.qdrant_ann)."""
    from app.qdrant_ann import search as qdrant_search

    return await qdrant_search(
        query_vector=query_vector,
        k=k,
        collection=collection,
        min_similarity=min_similarity,
    )


async def _faiss_search(
    query_vector: list[float],
    k: int = 10,
    collection: str = WALLET_COLLECTION,
    min_similarity: float = 0.0,
) -> list[dict[str, Any]]:
    """Search via the legacy in-process FAISS index."""
    ann = get_ann_index()
    if not ann.is_built(collection):
        await ann.build_index(collection)
    if not ann.is_built(collection):
        return []
    return await ann.search(
        query_embedding=query_vector,
        collection=collection,
        limit=k,
        min_similarity=min_similarity,
    )


async def search(
    query_vector: list[float],
    k: int = 10,
    collection: str = WALLET_COLLECTION,
    min_similarity: float = 0.0,
) -> list[dict[str, Any]]:
    """Dual-backend search: try Qdrant first, fall back to FAISS.

    Returns the FAISS result shape ``[{id, similarity, rank, content,
    metadata, source, severity}, ...]`` regardless of which backend
    served the request.
    """
    try:
        results = await _qdrant_search(
            query_vector=query_vector,
            k=k,
            collection=collection,
            min_similarity=min_similarity,
        )
        if results:
            return results
    except Exception as e:
        logger.warning("qdrant_backend_search_failed collection=%s err=%s", collection, e)
    try:
        return await _faiss_search(
            query_vector=query_vector,
            k=k,
            collection=collection,
            min_similarity=min_similarity,
        )
    except Exception as e:
        logger.warning("faiss_backend_search_failed collection=%s err=%s", collection, e)
        return []
