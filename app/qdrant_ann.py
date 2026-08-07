#!/usr/bin/env python3
"""
Qdrant-backed ANN index mirroring the FAISS ``app.ann_index`` interface.

RugMaps FAISS → Qdrant migration (tactical step).
=================================================
The legacy FAISS index manager (``app.ann_index.ANNIndex``) reads vectors
from Redis, builds an in-process FAISS HNSW/IVFFlat index and persists
pickled indexes to disk. This module exposes the same search surface over
Qdrant so callers can be switched incrementally:

  - ``search(query_vector, k)``      → Qdrant vector search (named dense)
  - ``add(doc_id, vector, metadata)`` → Qdrant upsert
  - ``QdrantANN`` class mirrors the ``ANNIndex`` class interface
    (``search(query_embedding, collection, limit, min_similarity)``)
    so existing callers keep working unchanged.

Collection: ``wallet_embeddings`` (named dense vector, created on demand).
The collection is created with the named-dense config (``{"dense": ...}``)
and all requests use the named vector format — bare vectors are rejected
by Qdrant against named-vector collections ("Not existing vector name").

No secrets, no RPC URLs. Qdrant URL comes from env (QDRANT_URL) with the
same Docker-compose default as the rest of the backend.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://rmi-qdrant:6333").rstrip("/")
WALLET_COLLECTION = os.getenv("WALLET_EMBEDDINGS_COLLECTION", "wallet_embeddings")
DENSE_VECTOR_NAME = "dense"
DEFAULT_DIM = int(os.getenv("WALLET_EMBEDDINGS_DIM", "1024"))

_client: httpx.AsyncClient | None = None
_collections_known: set[str] = set()


async def _get_client() -> httpx.AsyncClient:
    """Return a shared async httpx client for Qdrant REST calls."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=QDRANT_URL, timeout=30.0)
    return _client


async def close() -> None:
    """Close the shared client (call on shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ensure_collection(collection: str = WALLET_COLLECTION, dim: int = DEFAULT_DIM) -> bool:
    """Create *collection* when missing (named dense vector config).

    Collection configs are immutable in Qdrant, so the dimension must be
    chosen at creation time. Existing collections are left untouched.
    """
    if collection in _collections_known:
        return True
    client = await _get_client()
    try:
        r = await client.get(f"/collections/{collection}")
        if r.status_code == 200:
            _collections_known.add(collection)
            return True
        payload: dict[str, Any] = {"vectors": {DENSE_VECTOR_NAME: {"size": dim, "distance": "Cosine"}}}
        r = await client.put(f"/collections/{collection}", json=payload)
        ok = r.status_code in (200, 201)
        if ok:
            _collections_known.add(collection)
            return ok
        logger.warning(
            "qdrant_ann_create_failed collection=%s status=%d body=%s", collection, r.status_code, r.text[:200]
        )
        return False
    except Exception as e:
        logger.warning("qdrant_ann_create_error collection=%s err=%s", collection, e)
        return False


def _to_uuid(doc_id: str) -> str:
    """Deterministic UUID v5 for a doc id (Qdrant requires UUID/int ids)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ann:{doc_id}"))


async def add(
    doc_id: str,
    vector: list[float],
    metadata: dict[str, Any] | None = None,
    text: str = "",
    collection: str = WALLET_COLLECTION,
) -> bool:
    """Upsert one vector into *collection* (named dense format)."""
    if not vector:
        return False
    if not await ensure_collection(collection, len(vector)):
        return False
    client = await _get_client()
    payload = {
        "points": [
            {
                "id": _to_uuid(doc_id),
                "vector": {DENSE_VECTOR_NAME: vector},
                "payload": {
                    "doc_id": doc_id,
                    "content": (text or "")[:10000],
                    "metadata": metadata or {},
                },
            }
        ]
    }
    try:
        r = await client.put(f"/collections/{collection}/points", json=payload)
        ok = r.status_code in (200, 201)
        if not ok:
            logger.warning("qdrant_ann_add_failed status=%d body=%s", r.status_code, r.text[:200])
        return ok
    except Exception as e:
        logger.warning("qdrant_ann_add_error: %s", e)
        return False


async def add_many(
    vectors: list[list[float]],
    metadata: list[dict[str, Any]] | None = None,
    doc_ids: list[str] | None = None,
    texts: list[str] | None = None,
    collection: str = WALLET_COLLECTION,
) -> int:
    """Batch upsert *vectors* into *collection*. Returns count upserted.

    Mirrors the FAISS ``add`` bulk pattern; metadata/docs_ids/texts are
    optional parallel lists. All vectors must share the same dimension.
    """
    if not vectors:
        return 0
    dim = len(vectors[0])
    if not await ensure_collection(collection, dim):
        return 0
    points = []
    for i, vec in enumerate(vectors):
        if not vec or len(vec) != dim:
            continue
        doc_id = doc_ids[i] if doc_ids and i < len(doc_ids) else f"ann-{i}"
        points.append(
            {
                "id": _to_uuid(doc_id),
                "vector": {DENSE_VECTOR_NAME: vec},
                "payload": {
                    "doc_id": doc_id,
                    "content": ((texts[i] if texts and i < len(texts) else "") or "")[:10000],
                    "metadata": (metadata[i] if metadata and i < len(metadata) else {}) or {},
                },
            }
        )
    if not points:
        return 0
    client = await _get_client()
    try:
        r = await client.put(
            f"/collections/{collection}/points",
            json={"points": points},
        )
        if r.status_code not in (200, 201):
            logger.warning("qdrant_ann_add_many_failed status=%d body=%s", r.status_code, r.text[:200])
            return 0
        return len(points)
    except Exception as e:
        logger.warning("qdrant_ann_add_many_error: %s", e)
        return 0


async def search(
    query_vector: list[float],
    k: int = 10,
    collection: str = WALLET_COLLECTION,
    min_similarity: float = 0.0,
) -> list[dict[str, Any]]:
    """Qdrant vector search returning the FAISS result shape.

    Returns list of ``{id, similarity, rank, content, metadata, source,
    severity}`` dicts (matching ``ANNIndex.search``) so callers can swap
    backends without changing downstream handling.
    """
    if not query_vector:
        return []
    client = await _get_client()
    # Search expects NamedVectorStruct: {"name": ..., "vector": [...]}.
    # The upsert map shape {"dense": [...]} is rejected on the search
    # endpoint ("data did not match any variant of untagged enum").
    vector_field: Any = {"name": DENSE_VECTOR_NAME, "vector": query_vector}
    payload: dict[str, Any] = {
        "vector": vector_field,
        "limit": max(1, k),
        "score_threshold": min_similarity if min_similarity > 0 else None,
        "with_payload": True,
    }
    try:
        r = await client.post(f"/collections/{collection}/points/search", json=payload)
        if r.status_code != 200:
            logger.warning("qdrant_ann_search_failed status=%d body=%s", r.status_code, r.text[:200])
            return []
        results: list[dict[str, Any]] = []
        for rank, point in enumerate(r.json().get("result", [])):
            p = point.get("payload", {}) or {}
            meta = p.get("metadata", {}) or {}
            results.append(
                {
                    "id": p.get("doc_id", str(point.get("id", ""))),
                    "similarity": round(float(point.get("score", 0.0) or 0.0), 4),
                    "rank": rank,
                    "content": p.get("content", ""),
                    "metadata": meta,
                    "source": meta.get("source", ""),
                    "severity": meta.get("severity", ""),
                }
            )
        return results
    except Exception as e:
        logger.warning("qdrant_ann_search_error: %s", e)
        return []


async def count(collection: str = WALLET_COLLECTION) -> int:
    """Return the number of points in *collection* (0 on failure)."""
    client = await _get_client()
    try:
        r = await client.get(f"/collections/{collection}")
        if r.status_code != 200:
            return 0
        return int(r.json().get("result", {}).get("points_count", 0) or 0)
    except Exception as e:
        logger.warning("qdrant_ann_count_error: %s", e)
        return 0


async def delete(doc_id: str, collection: str = WALLET_COLLECTION) -> bool:
    """Delete one point by doc id."""
    client = await _get_client()
    try:
        r = await client.post(
            f"/collections/{collection}/points/delete",
            json={"points": [_to_uuid(doc_id)]},
        )
        return r.status_code in (200, 201)
    except Exception as e:
        logger.warning("qdrant_ann_delete_error: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════════
# Class mirror of the FAISS ANNIndex (app.ann_index.ANNIndex) interface
# ══════════════════════════════════════════════════════════════════════


class QdrantANN:
    """Per-collection Qdrant ANN index.

    Mirrors the surface of the legacy FAISS ``ANNIndex`` (used by
    ``app.rag_service.retrieval``) so the two backends are drop-in
    interchangeable:

      - ``search(query_embedding, collection, limit, min_similarity)``
      - ``is_built(collection)`` / ``stats()`` / ``bump_version()``
    """

    _instance: QdrantANN | None = None

    def __init__(self) -> None:
        self._meta: dict[str, dict[str, Any]] = {}
        self._versions: dict[str, int] = {}

    @classmethod
    def get_instance(cls) -> QdrantANN:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def search(
        self,
        query_embedding: list[float],
        collection: str,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Qdrant search with the FAISS signature (Qdrant primary)."""
        return await search(
            query_vector=query_embedding,
            k=limit,
            collection=collection,
            min_similarity=min_similarity,
        )

    async def add(
        self,
        collection: str,
        doc_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
        text: str = "",
    ) -> bool:
        return await add(
            doc_id=doc_id,
            vector=vector,
            metadata=metadata,
            text=text,
            collection=collection,
        )

    def is_built(self, collection: str) -> bool:
        """Qdrant backends have no build step — always 'built'."""
        return True

    async def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for coll in {*self._meta.keys(), WALLET_COLLECTION}:
            out[coll] = {
                "built": True,
                "n_vectors": await count(coll),
                "dims": DEFAULT_DIM,
                "index_type": "qdrant",
                **self._meta.get(coll, {}),
            }
        return out

    async def bump_version(self, collection: str) -> int:
        """Qdrant index is always current — version counter is a no-op."""
        ver = self._versions.get(collection, 0) + 1
        self._versions[collection] = ver
        return ver

    def invalidate(self, collection: str) -> None:
        self._meta.pop(collection, None)

    async def build_index(self, collection: str, force: bool = False) -> dict[str, Any]:
        """No-op for Qdrant (vectors are upserted live)."""
        meta = {"status": "qdrant", "collection": collection, "built_at": 0}
        self._meta[collection] = meta
        return meta


# Singleton accessor matching ``app.ann_index.get_ann_index``
_qdrant_ann: QdrantANN | None = None


def get_qdrant_ann() -> QdrantANN:
    """Return the singleton QdrantANN instance."""
    global _qdrant_ann
    if _qdrant_ann is None:
        _qdrant_ann = QdrantANN()
    return _qdrant_ann
