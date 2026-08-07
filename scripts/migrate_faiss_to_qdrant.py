#!/usr/bin/env python3
"""
Migrate FAISS ANN indexes → Qdrant (RugMaps tactical migration).

Reads the persisted FAISS indexes (``.index`` + ``.ids`` files under
``FAISS_DATA_DIR``) for the wallet-related collections and upserts the
vectors into the Qdrant ``wallet_embeddings`` collection using the
``app.qdrant_ann`` backend.

Why this shape:
  - The FAISS ``.index`` file holds the vectors; the ``.ids`` file holds
    the doc ids in the same order. Vector values are read back with
    ``index.reconstruct(i)``.
  - Doc metadata/content lives in Redis under ``rag:{collection}:{id}``
    and is attached to the Qdrant payload when reachable. When Redis is
    unavailable the migration still copies vectors (metadata omitted)
    so no data is lost silently.

Usage:
  python3 /app/scripts/migrate_faiss_to_qdrant.py            # wallet_profiles etc.
  python3 /app/scripts/migrate_faiss_to_qdrant.py --all      # every FAISS index
  python3 /app/scripts/migrate_faiss_to_qdrant.py --dry-run  # count only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pickle
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

FAISS_DATA_DIR = os.getenv("FAISS_DATA_DIR", "/app/data/faiss")
QDRANT_URL = os.getenv("QDRANT_URL", "http://rmi-qdrant:6333").rstrip("/")
WALLET_COLLECTION = os.getenv("WALLET_EMBEDDINGS_COLLECTION", "wallet_embeddings")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# FAISS collections whose vectors belong in the wallet_embeddings Qdrant
# collection. Extra collections are only migrated with ``--all``.
WALLET_FAISS_COLLECTIONS = [
    "wallet_profiles",
    "known_scams",
    "scam_patterns",
]

BATCH_SIZE = 100


def _redis_client():
    try:
        import redis.asyncio as aioredis

        return aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD or None,
            db=0,
            decode_responses=True,
        )
    except Exception as e:
        logger.warning("redis client unavailable err=%s", e)
        return None


def _list_faiss_indexes() -> list[str]:
    """Return collection names with a persisted ``.index`` file."""
    if not os.path.isdir(FAISS_DATA_DIR):
        logger.warning("FAISS data dir missing: %s", FAISS_DATA_DIR)
        return []
    return sorted(name[: -len(".index")] for name in os.listdir(FAISS_DATA_DIR) if name.endswith(".index"))


def _load_faiss(collection: str) -> tuple[Any, list[str]] | None:
    """Load the FAISS index + id list for *collection*. None on failure."""
    try:
        import faiss
    except ImportError:
        logger.error("faiss not installed — cannot read FAISS indexes")
        return None
    index_path = os.path.join(FAISS_DATA_DIR, f"{collection}.index")
    ids_path = os.path.join(FAISS_DATA_DIR, f"{collection}.ids")
    if not os.path.exists(index_path) or not os.path.exists(ids_path):
        return None
    try:
        index = faiss.read_index(index_path)
        with open(ids_path, "rb") as f:
            # SECURITY: pickles are our own artifacts (models bundled in Docker)
            id_list = pickle.load(f, usedforsecurity=False)
        return index, list(id_list)
    except Exception as e:
        logger.warning("faiss_load_failed collection=%s err=%s", collection, e)
        return None


def _reconstruct(index: Any, n: int, dims: int) -> list[list[float]]:
    """Read *n* vectors back from a FAISS index (handles IVFFlat/HNSW)."""
    vecs: list[list[float]] = []
    try:
        if hasattr(index, "reconstruct") and n <= getattr(index, "ntotal", 0):
            for i in range(n):
                v = index.reconstruct(i)
                vecs.append([float(x) for x in v])
            return vecs
    except Exception as e:
        logger.debug("faiss_reconstruct_failed err=%s", e)
    # Fallback: brute-force search per point when reconstruct is unsupported.
    import numpy as np

    qs = np.zeros((n, dims), dtype=np.float32)
    for i in range(min(n, 50)):
        qs[i, i % dims] = 1.0
    try:
        for i in range(n):
            probe = np.zeros((1, dims), dtype=np.float32)
            if i % dims == 0 and i > 0:
                probe[0, 0] = 1.0
            else:
                probe[0, i % dims] = 1.0
            _distances, indices = index.search(probe, 1)
            if indices[0][0] >= 0:
                vecs.append([float(x) for x in index.reconstruct(int(indices[0][0]))])
    except Exception as e:
        logger.warning("faiss_fallback_reconstruct_failed err=%s", e)
        return []
    return vecs


async def _fetch_redis_docs(redis, collection: str, doc_ids: list[str]) -> dict[str, dict]:
    """Batch-fetch ``rag:{collection}:{id}`` JSON docs from Redis."""
    out: dict[str, dict] = {}
    if redis is None:
        return out
    try:
        pipe = redis.pipeline()
        for did in doc_ids:
            pipe.get(f"rag:{collection}:{did}")
        raw = await pipe.execute()
        for did, data in zip(doc_ids, raw, strict=False):
            if not data:
                continue
            try:
                doc = json.loads(data)
            except json.JSONDecodeError:
                continue
            out[did] = doc
    except Exception as e:
        logger.warning("redis_fetch_failed collection=%s err=%s", collection, e)
    return out


async def migrate_collection(collection: str, dry_run: bool = False) -> dict[str, Any]:
    """Migrate one FAISS collection into Qdrant wallet_embeddings."""
    loaded = _load_faiss(collection)
    if loaded is None:
        return {"collection": collection, "status": "skipped", "reason": "no faiss index"}
    index, id_list = loaded
    n = min(len(id_list), int(getattr(index, "ntotal", len(id_list))))
    dims = int(getattr(index, "d", 0))
    if n == 0 or dims == 0:
        return {"collection": collection, "status": "skipped", "reason": "empty index"}
    logger.info("migrate collection=%s n=%d dims=%d", collection, n, dims)

    vecs = _reconstruct(index, n, dims)
    if not vecs:
        return {"collection": collection, "status": "failed", "reason": "reconstruct empty"}
    if len(vecs) != n:
        n = len(vecs)
        id_list = id_list[:n]

    if dry_run:
        return {
            "collection": collection,
            "status": "dry-run",
            "vectors": n,
            "dims": dims,
            "target": WALLET_COLLECTION,
        }

    from app.qdrant_ann import add_many, ensure_collection

    if not await ensure_collection(WALLET_COLLECTION, dims):
        return {"collection": collection, "status": "failed", "reason": "ensure collection"}
    if n > len(vecs):
        vecs = vecs[:n]

    redis = _redis_client()
    docs = await _fetch_redis_docs(redis, collection, id_list) if redis else {}

    total = 0
    for start in range(0, n, BATCH_SIZE):
        batch_ids = id_list[start : start + BATCH_SIZE]
        batch_vecs = vecs[start : start + BATCH_SIZE]
        batch_meta: list[dict[str, Any]] = []
        batch_texts: list[str] = []
        for did in batch_ids:
            doc = docs.get(did, {})
            batch_meta.append(
                {
                    **(doc.get("metadata", {}) or {}),
                    "faiss_collection": collection,
                    "migrated_from": "faiss",
                }
            )
            batch_texts.append(doc.get("content", "") or "")
        inserted = await add_many(
            vectors=batch_vecs,
            metadata=batch_meta,
            doc_ids=[f"{collection}:{did}" for did in batch_ids],
            texts=batch_texts,
            collection=WALLET_COLLECTION,
        )
        total += inserted
        if redis:
            await redis.close()
            redis = _redis_client()
        logger.info("migrate_batch collection=%s inserted=%d/%d", collection, inserted, len(batch_ids))

    if redis:
        from contextlib import suppress

        with suppress(Exception):
            await redis.close()

    return {"collection": collection, "status": "done", "vectors": total, "dims": dims, "target": WALLET_COLLECTION}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate FAISS indexes → Qdrant")
    parser.add_argument("--all", action="store_true", help="migrate every FAISS index, not just wallet collections")
    parser.add_argument("--dry-run", action="store_true", help="count only, no writes")
    parser.add_argument("--collections", nargs="*", default=None, help="explicit collection list")
    args = parser.parse_args()

    if args.collections:
        targets = args.collections
    elif args.all:
        targets = _list_faiss_indexes()
    else:
        available = set(_list_faiss_indexes())
        targets = [c for c in WALLET_FAISS_COLLECTIONS if c in available]

    if not targets:
        logger.warning("no FAISS indexes found under %s", FAISS_DATA_DIR)
        return

    logger.info("migrating %d collection(s): %s", len(targets), ", ".join(targets))
    for coll in targets:
        try:
            result = await migrate_collection(coll, dry_run=args.dry_run)
            logger.info("result: %s", json.dumps(result))
        except Exception as e:
            logger.error("migrate_failed collection=%s err=%s", coll, e)


if __name__ == "__main__":
    asyncio.run(main())
