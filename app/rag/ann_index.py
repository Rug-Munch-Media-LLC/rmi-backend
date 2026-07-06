"""M3 RAG ANN Index - numpy-based cosine similarity with Redis persistence.

Why numpy + Redis (not FAISS):
- 13 collections x ~5K docs = ~65K total - small enough for in-process numpy  # noqa: RUF002
- No FAISS native dep, no rebuild on container restart
- Vector + metadata stored as JSON in Redis; loaded into a numpy matrix
  on first search, then kept in process memory for fast repeated queries
- Cosine sim = dot product on L2-normalized vectors (we normalize on insert)

Persistence shape (per collection):
  rag:ann:{collection}:meta   → JSON {count, dim}
  rag:ann:{collection}:docs   → HASH doc_id → {"vector": [...], "metadata": {...}, "text": "..."}
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.rag.embeddings import EMBEDDING_DIM

log = logging.getLogger(__name__)

# Lazy import - Redis client is created on first use, after app.core.redis is ready
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        from app.core.redis import get_redis

        _redis_client = get_redis()
    return _redis_client


@dataclass
class Hit:
    """One search result."""

    doc_id: str
    score: float
    text: str
    metadata: dict[str, Any]


class ANNIndex:
    """Per-collection vector index. Holds vectors in memory, persists to Redis.

    Usage:
        idx = ANNIndex("scam_intel")
        idx.add("doc1", vector, {"source": "x"}, text="...")
        hits = idx.search(query_vec, top_k=5)
    """

    def __init__(self, collection: str, dim: int = EMBEDDING_DIM):
        self.collection = collection
        self.dim = dim
        self._ids: list[str] = []
        self._matrix: np.ndarray | None = None  # shape (N, dim), L2-normalized
        self._meta: dict[str, dict] = {}  # doc_id → {text, metadata}
        self._loaded = False

    # ── Redis keys ────────────────────────────────────────────────────
    def _key_docs(self) -> str:
        return f"rag:ann:{self.collection}:docs"

    def _key_meta(self) -> str:
        return f"rag:ann:{self.collection}:meta"

    # ── Persistence ───────────────────────────────────────────────────
    def _persist(self) -> None:
        r = _get_redis()
        pipe = r.pipeline()
        for doc_id in self._ids:
            entry = {
                "vector": (self._matrix[self._ids.index(doc_id)] if self._matrix is not None else []).tolist(),
                "metadata": self._meta[doc_id].get("metadata", {}),
                "text": self._meta[doc_id].get("text", ""),
            }
            pipe.hset(self._key_docs(), doc_id, json.dumps(entry))
        pipe.set(
            self._key_meta(),
            json.dumps({"count": len(self._ids), "dim": self.dim}),
        )
        pipe.execute()

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            r = _get_redis()
            raw = r.hgetall(self._key_docs())
        except Exception as e:
            log.warning("ann_load_failed collection=%s err=%s", self.collection, e)
            raw = {}

        if not raw:
            self._loaded = True
            return

        ids: list[str] = []
        vecs: list[list[float]] = []
        meta: dict[str, dict] = {}
        for doc_id, blob in raw.items():
            try:
                entry = json.loads(blob)
                ids.append(doc_id)
                vecs.append(entry.get("vector", []))
                meta[doc_id] = {
                    "metadata": entry.get("metadata", {}),
                    "text": entry.get("text", ""),
                }
            except Exception as e:
                log.debug("ann_skip doc=%s err=%s", doc_id, e)
        if vecs:
            self._matrix = np.asarray(vecs, dtype=np.float32)
        self._ids = ids
        self._meta = meta
        self._loaded = True
        log.info("ann_loaded collection=%s count=%d", self.collection, len(ids))

    # ── Mutations ─────────────────────────────────────────────────────
    def add(
        self,
        doc_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self._load()
        arr = np.asarray(vector, dtype=np.float32)
        if arr.shape[0] != self.dim:
            # Pad or truncate
            if arr.shape[0] >= self.dim:
                arr = arr[: self.dim]
            else:
                arr = np.concatenate(
                    [arr, np.zeros(self.dim - arr.shape[0], dtype=np.float32)]
                )
        # L2 normalize
        n = float(np.linalg.norm(arr))
        if n > 0:
            arr = arr / n

        if doc_id in self._ids:
            # Update - replace vector
            idx = self._ids.index(doc_id)
            self._matrix[idx] = arr
        else:
            if self._matrix is None:
                self._matrix = arr.reshape(1, -1)
            else:
                self._matrix = np.vstack([self._matrix, arr.reshape(1, -1)])
            self._ids.append(doc_id)

        self._meta[doc_id] = {"metadata": metadata or {}, "text": text}
        self._persist()

    def delete(self, doc_id: str) -> bool:
        self._load()
        if doc_id not in self._ids:
            return False
        idx = self._ids.index(doc_id)
        self._ids.pop(idx)
        if self._matrix is not None and self._matrix.shape[0] > 1:
            self._matrix = np.delete(self._matrix, idx, axis=0)
        else:
            self._matrix = None
        self._meta.pop(doc_id, None)
        self._persist()
        return True

    def clear(self) -> None:
        self._ids = []
        self._matrix = None
        self._meta = {}
        self._loaded = True
        try:
            r = _get_redis()
            r.delete(self._key_docs(), self._key_meta())
        except Exception as e:
            log.warning("ann_clear_failed: %s", e)

    # ── Search ────────────────────────────────────────────────────────
    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        min_similarity: float = 0.0,
    ) -> list[Hit]:
        self._load()
        if self._matrix is None or len(self._ids) == 0:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        n = float(np.linalg.norm(q))
        if n > 0:
            q = q / n
        # Cosine sim = dot product on normalized vectors
        scores = self._matrix @ q
        # Top-k by score
        k = min(top_k, len(self._ids))
        # argpartition is faster than full argsort for small top_k
        idx = np.argpartition(-scores, k - 1)[:k] if k < len(scores) else np.arange(len(scores))
        idx = idx[np.argsort(-scores[idx])]
        hits: list[Hit] = []
        for i in idx:
            s = float(scores[i])
            if s < min_similarity:
                continue
            doc_id = self._ids[int(i)]
            meta = self._meta.get(doc_id, {})
            hits.append(
                Hit(
                    doc_id=doc_id,
                    score=s,
                    text=meta.get("text", ""),
                    metadata=meta.get("metadata", {}),
                )
            )
        return hits

    # ── Stats ─────────────────────────────────────────────────────────
    def count(self) -> int:
        self._load()
        return len(self._ids)


# ── Per-collection cache ─────────────────────────────────────────────
_index_cache: dict[str, ANNIndex] = {}


def get_index(collection: str) -> ANNIndex:
    """Get or create the ANNIndex for a collection. Cached per process."""
    if collection not in _index_cache:
        _index_cache[collection] = ANNIndex(collection)
    return _index_cache[collection]
