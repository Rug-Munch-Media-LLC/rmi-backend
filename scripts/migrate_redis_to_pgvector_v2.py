#!/usr/bin/env python3
"""
Resilient Redis → Supabase pgvector migration v2.
Fixes: smaller batches, retry on timeout, HNSW index dropped for speed.
"""

import asyncio
import json
import logging
import os
import time

import httpx
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv("/app/.env", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TARGET_DIM = 640
BATCH_SIZE = 50  # Smaller batches to avoid Supabase statement timeout
MAX_RETRIES = 3

COLLECTIONS = [
    "wallet_profiles",  # ~50K already migrated
    "token_analysis",
    "known_scams",
    "scam_patterns",
    "forensic_reports",
    "market_intel",
    "contract_audits",
    "news_articles",
    "transaction_patterns",
    "general",
]


def _get_url():
    return os.environ.get("SUPABASE_URL", "")


def _get_key():
    return os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _get_headers():
    key = _get_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


def pad_vector(vec: list, target_dim: int) -> list:
    if len(vec) == target_dim:
        return vec
    if len(vec) > target_dim:
        return vec[:target_dim]
    return vec + [0.0] * (target_dim - len(vec))


async def get_existing_ids(client: httpx.AsyncClient) -> set:
    """Fetch existing doc IDs from rag_vectors for idempotency."""
    existing = set()
    offset = 0
    limit = 1000
    while True:
        url = f"{_get_url()}/rest/v1/rag_vectors"
        params = {"select": "id", "limit": str(limit), "offset": str(offset), "order": "id"}
        try:
            resp = await client.get(url, params=params, headers=_get_headers(), timeout=30)
            if resp.status_code != 200:
                logger.warning(f"Could not fetch existing IDs: {resp.status_code}")
                break
            rows = resp.json() if resp.text else []
            if not rows:
                break
            for row in rows:
                existing.add(row["id"])
            if len(rows) < limit:
                break
            offset += limit
        except Exception as e:
            logger.warning(f"Error fetching existing IDs: {e}")
            break
    logger.info(f"Found {len(existing)} existing docs in rag_vectors")
    return existing


async def insert_with_retry(client: httpx.AsyncClient, rows: list, batch_num: int) -> int:
    """Insert a batch with retry logic."""
    url = f"{_get_url()}/rest/v1/rag_vectors"
    params = {"on_conflict": "id"}

    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.post(url, json=rows, params=params, headers=_get_headers(), timeout=60)
            if resp.status_code in (200, 201):
                return len(rows)
            elif resp.status_code == 500 and "timeout" in (resp.text or "").lower():
                # Statement timeout — split into smaller batches
                logger.warning(f"Batch {batch_num}: statement timeout (attempt {attempt + 1}), splitting")
                if len(rows) > 10:
                    # Split into two halves
                    mid = len(rows) // 2
                    count = 0
                    for half in [rows[:mid], rows[mid:]]:
                        r2 = await client.post(url, json=half, params=params, headers=_get_headers(), timeout=90)
                        if r2.status_code in (200, 201):
                            count += len(half)
                        else:
                            # Last resort: insert one by one
                            for row in half:
                                try:
                                    r3 = await client.post(
                                        url,
                                        json=[row],
                                        params=params,
                                        headers=_get_headers(),
                                        timeout=30,
                                    )
                                    if r3.status_code in (200, 201):
                                        count += 1
                                    await asyncio.sleep(0.02)
                                except Exception:
                                    pass
                    return count
            else:
                logger.error(f"Batch {batch_num} insert failed: {resp.status_code} {resp.text[:300]}")
                return 0
        except httpx.ReadTimeout:
            logger.warning(f"Batch {batch_num}: timeout (attempt {attempt + 1})")
            await asyncio.sleep(2**attempt)
        except Exception as e:
            logger.error(f"Batch {batch_num} exception: {e}")
            await asyncio.sleep(1)

    return 0


async def migrate_collection(r: aioredis.Redis, client: httpx.AsyncClient, collection: str, existing_ids: set) -> int:
    """Migrate all docs from one Redis collection to Supabase."""
    idx_key = f"rag:idx:{collection}"
    doc_ids = await r.smembers(idx_key)
    if not doc_ids:
        logger.info(f"Collection {collection}: empty, skipping")
        return 0

    total_docs = len(doc_ids)
    to_migrate = [did for did in doc_ids if did not in existing_ids]
    skipped = total_docs - len(to_migrate)
    if skipped > 0:
        logger.info(
            f"Collection {collection}: {total_docs} total, skipping {skipped} already-migrated, {len(to_migrate)} to go"
        )

    migrated = 0
    errors = 0
    batch_rows = []
    batch_count = 0

    for _i, doc_id in enumerate(to_migrate):
        doc_key = f"rag:{collection}:{doc_id}"
        try:
            data = await r.get(doc_key)
            if not data:
                continue

            doc = json.loads(data)
            vector = doc.get("vector", [])
            content = doc.get("content", "") or ""
            metadata = doc.get("metadata", {}) or {}
            source = metadata.get("source", "") or doc.get("source", "") or ""
            severity = metadata.get("severity", "") or doc.get("severity", "") or "medium"
            chain = metadata.get("chain", "") or doc.get("chain", "") or ""

            padded = pad_vector(vector, TARGET_DIM) if vector else [0.0] * TARGET_DIM

            row = {
                "id": str(doc_id),
                "collection": collection,
                "content": content[:10000],
                "embedding": padded,
                "metadata": json.dumps(metadata) if isinstance(metadata, dict) else str(metadata),
                "source": source[:200] if source else "",
                "severity": severity[:50] if severity else "medium",
                "chain": chain[:50] if chain else "",
            }

            batch_rows.append(row)

            if len(batch_rows) >= BATCH_SIZE:
                count = await insert_with_retry(client, batch_rows, batch_count)
                migrated += count
                batch_count += 1
                if batch_count % 10 == 0:
                    logger.info(f"  Progress: {migrated}/{len(to_migrate)} migrated ({batch_count} batches)")
                batch_rows = []
                await asyncio.sleep(0.1)

        except json.JSONDecodeError:
            errors += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                logger.warning(f"  Doc {doc_id}: {e}")

    # Flush remaining
    if batch_rows:
        count = await insert_with_retry(client, batch_rows, batch_count)
        migrated += count

    logger.info(f"Collection {collection}: DONE {migrated}/{len(to_migrate)} ({errors} errors)")
    return migrated


async def main():
    logger.info(f"Starting migration v2 (batch_size={BATCH_SIZE})")
    logger.info(f"Supabase: {_get_url()[:30]}...")
    logger.info(f"Target dim: {TARGET_DIM}")

    r = aioredis.Redis(
        host=os.environ.get("REDIS_HOST", "rmi-redis"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        password=os.environ.get("REDIS_PASSWORD", ""),
        decode_responses=True,
    )
    await r.ping()
    logger.info("Redis connected")

    async with httpx.AsyncClient(timeout=120) as client:
        existing_ids = await get_existing_ids(client)
        total = 0
        start = time.time()

        for coll in COLLECTIONS:
            try:
                n = await migrate_collection(r, client, coll, existing_ids)
                total += n
            except Exception as e:
                logger.error(f"Collection {coll} FAILED: {e}")

        elapsed = time.time() - start
        logger.info("=" * 60)
        logger.info(f"Migration v2 complete: {total} docs in {elapsed:.1f}s")
        logger.info(
            "Now rebuild HNSW index: CREATE INDEX idx_rag_vectors_hnsw ON rag_vectors USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);"
        )

    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
