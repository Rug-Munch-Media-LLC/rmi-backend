#!/usr/bin/env python3
"""
Migrate RAG data from Redis to Supabase rag_vectors table.
Reads all documents from 10 Redis collections and upserts into pgvector.
Handles mixed embedding dimensions by padding to 640.
Processes in batches of 100. Idempotent (skips already-migrated docs).
"""

import asyncio
import json
import logging
import os
import sys
import time

import httpx
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv("/app/.env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Target dimension — max across all collections is 640 (token_analysis)
TARGET_DIM = 640

COLLECTIONS = [
    "wallet_profiles",
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

BATCH_SIZE = 100


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


def _get_redis_password():
    return os.environ.get("REDIS_PASSWORD", "")


def pad_vector(vec: list[float], target_dim: int) -> list[float]:
    """Pad or truncate embedding vector to target dimension."""
    if len(vec) == target_dim:
        return vec
    if len(vec) > target_dim:
        return vec[:target_dim]
    return vec + [0.0] * (target_dim - len(vec))


async def get_existing_ids(client: httpx.AsyncClient) -> set:
    """Fetch all existing doc IDs from rag_vectors to skip already-migrated docs."""
    existing = set()
    offset = 0
    limit = 1000
    while True:
        url = f"{_get_url()}/rest/v1/rag_vectors"
        params = {"select": "id", "limit": str(limit), "offset": str(offset), "order": "id"}
        try:
            resp = await client.get(url, params=params, headers=_get_headers(), timeout=30)
            if resp.status_code != 200:
                logger.warning(f"Could not fetch existing IDs: {resp.status_code} {resp.text[:300]}")
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


async def insert_batch(client: httpx.AsyncClient, rows: list[dict]) -> int:
    """Insert a batch of rows into rag_vectors via Supabase REST API."""
    url = f"{_get_url()}/rest/v1/rag_vectors"
    params = {"on_conflict": "id"}
    try:
        resp = await client.post(url, json=rows, params=params, headers=_get_headers(), timeout=120)
        if resp.status_code in (200, 201):
            return len(rows)
        else:
            logger.error(f"Batch insert failed: {resp.status_code} {resp.text[:500]}")
            # Fallback: try one by one
            success = 0
            for row in rows:
                try:
                    r2 = await client.post(url, json=[row], params=params, headers=_get_headers(), timeout=60)
                    if r2.status_code in (200, 201):
                        success += 1
                    else:
                        if r2.status_code == 400 and "embedding" in (r2.text or ""):
                            logger.warning(f"Skipping {row['id']} due to embedding error")
                        else:
                            logger.warning(f"Single insert failed for {row['id']}: {r2.status_code} {r2.text[:200]}")
                except Exception as e:
                    logger.warning(f"Single insert error for {row['id']}: {e}")
            return success
    except Exception as e:
        logger.error(f"Batch insert exception: {e}")
        return 0


async def migrate_collection(r: aioredis.Redis, client: httpx.AsyncClient, collection: str, existing_ids: set) -> int:
    """Migrate all docs from one Redis collection to Supabase."""
    idx_key = f"rag:idx:{collection}"
    doc_ids = await r.smembers(idx_key)
    if not doc_ids:
        logger.info(f"Collection {collection}: empty, skipping")
        return 0

    total_docs = len(doc_ids)
    logger.info(f"Collection {collection}: {total_docs} docs to process")

    # Filter out already-migrated docs
    to_migrate = [did for did in doc_ids if did not in existing_ids]
    skipped = total_docs - len(to_migrate)
    if skipped > 0:
        logger.info(f"  Skipping {skipped} already-migrated docs")

    migrated = 0
    batch_rows = []
    batch_count = 0
    errors = 0

    for i, doc_id in enumerate(to_migrate):
        doc_key = f"rag:{collection}:{doc_id}"
        try:
            data = await r.get(doc_key)
            if not data:
                logger.debug(f"  Doc {doc_id}: not found in Redis")
                continue

            doc = json.loads(data)

            # Extract fields
            vector = doc.get("vector", [])
            content = doc.get("content", "") or ""
            metadata = doc.get("metadata", {}) or {}

            source = metadata.get("source", "") or doc.get("source", "") or ""
            severity = metadata.get("severity", "") or doc.get("severity", "") or "medium"
            chain = metadata.get("chain", "") or doc.get("chain", "") or ""

            # Pad vector to target dimension
            if vector:
                padded = pad_vector(vector, TARGET_DIM)
            else:
                padded = [0.0] * TARGET_DIM
                logger.debug(f"  Doc {doc_id}: no embedding, using zero vector")

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
                count = await insert_batch(client, batch_rows)
                migrated += count
                batch_count += 1
                logger.info(
                    f"  Batch {batch_count}: inserted {count}/{len(batch_rows)} ({migrated}/{len(to_migrate)} total)"
                )
                batch_rows = []
                await asyncio.sleep(0.05)

        except json.JSONDecodeError as e:
            errors += 1
            if errors <= 5:
                logger.warning(f"  Doc {doc_id}: JSON decode error: {e}")
        except Exception as e:
            errors += 1
            if errors <= 5:
                logger.warning(f"  Doc {doc_id}: error: {e}")

        # Progress every 500 docs
        if (i + 1) % 500 == 0:
            logger.info(f"  Progress: processed {i + 1}/{len(to_migrate)}, migrated {migrated}, errors {errors}")

    # Flush remaining batch
    if batch_rows:
        count = await insert_batch(client, batch_rows)
        migrated += count
        batch_count += 1
        logger.info(f"  Final batch {batch_count}: inserted {count}/{len(batch_rows)}")

    logger.info(
        f"Collection {collection}: migrated {migrated}/{len(to_migrate)} docs ({skipped} skipped, {errors} errors)"
    )
    return migrated


async def main():
    logger.info("Starting Redis -> Supabase rag_vectors migration")
    logger.info(f"Supabase URL: {_get_url()}")
    logger.info(f"Redis: {os.environ.get('REDIS_HOST', 'rmi-redis')}:{os.environ.get('REDIS_PORT', '6379')}")
    logger.info(f"Target vector dimension: {TARGET_DIM}")
    logger.info(f"Collections: {COLLECTIONS}")

    if not _get_url() or not _get_key():
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        sys.exit(1)

    redis_password = _get_redis_password()
    redis_host = os.environ.get("REDIS_HOST", "rmi-redis")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))

    # Connect to Redis
    r = aioredis.Redis(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        decode_responses=True,
    )
    await r.ping()
    logger.info("Redis connected")

    # HTTP client for Supabase
    async with httpx.AsyncClient(timeout=120) as client:
        # Get existing IDs for idempotency
        existing_ids = await get_existing_ids(client)

        total_migrated = 0
        start_time = time.time()

        for collection in COLLECTIONS:
            try:
                count = await migrate_collection(r, client, collection, existing_ids)
                total_migrated += count
            except Exception as e:
                logger.error(f"Collection {collection} failed: {e}")

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"Migration complete: {total_migrated} docs migrated in {elapsed:.1f}s")

    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
