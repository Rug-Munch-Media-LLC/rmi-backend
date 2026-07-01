#!/usr/bin/env python3
"""
Post-migration: Create HNSW index on rag_vectors table.

Run AFTER the migration completes. The index is dropped during migration
for faster inserts and must be recreated afterward.

Usage:
  python3 /app/scripts/create_hnsw_index.py
"""

import asyncio
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv("/app/.env", override=True)


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
    }


async def check_row_count(client: httpx.AsyncClient) -> int:
    """Check total row count in rag_vectors."""
    url = f"{_get_url()}/rest/v1/rag_vectors"
    params = {"select": "id", "limit": "1"}
    headers = _get_headers()
    headers["Prefer"] = "count=exact"

    resp = await client.get(url, params=params, headers=headers, timeout=30)
    content_range = resp.headers.get("content-range", "0")
    try:
        total = int(content_range.split("/")[1])
    except (IndexError, ValueError):
        total = 0
    return total


async def create_hnsw_index():
    """Create HNSW index on rag_vectors.embedding column."""
    url = _get_url()
    key = _get_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=300) as client:
        # Check row count first
        total = await check_row_count(client)
        print(f"rag_vectors row count: {total}")

        if total < 100:
            print("WARNING: Very few rows in rag_vectors. HNSW index may not be worthwhile.")
            print("Proceeding anyway...")

        # Drop existing index if any
        print("Dropping any existing HNSW index...")
        drop_sql = "DROP INDEX IF EXISTS public.idx_rag_vectors_hnsw;"
        try:
            resp = await client.post(
                f"{url}/rest/v1/rpc/exec_sql_returning",
                json={"query": drop_sql},
                headers=headers,
                timeout=60,
            )
            print(f"Drop result: {resp.status_code}")
        except Exception as e:
            print(f"Drop index: {e} (may not exist, that's OK)")

        # Create HNSW index
        # m=16: number of bidirectional links per node (higher = more accurate, slower build)
        # ef_construction=64: build-time search depth (higher = more accurate, slower build)
        print(f"Creating HNSW index on {total} rows...")
        print("This will take several minutes for large tables...")
        start = time.time()

        create_sql = """
        CREATE INDEX CONCURRENTLY idx_rag_vectors_hnsw
        ON public.rag_vectors
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
        """

        try:
            resp = await client.post(
                f"{url}/rest/v1/rpc/exec_sql_returning",
                json={"query": create_sql},
                headers=headers,
                timeout=600,  # 10 minutes for index creation
            )
            elapsed = time.time() - start
            print(f"Create index result: {resp.status_code} in {elapsed:.1f}s")
            if resp.status_code in (200, 201, 204):
                print(f"HNSW index created successfully! ({elapsed:.1f}s for {total} rows)")
            else:
                print(f"Response: {resp.text[:500]}")
        except httpx.ReadTimeout:
            elapsed = time.time() - start
            print(f"Index creation timed out after {elapsed:.1f}s")
            print("Index may still be building in the background. Check pg_stat_progress_create_index.")
            print("Query: SELECT * FROM pg_stat_progress_create_index;")
        except Exception as e:
            print(f"Error creating index: {e}")
            print("You may need to create it manually via the Supabase SQL editor.")

        # Verify
        print("\nVerifying index...")
        verify_sql = "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'rag_vectors';"
        try:
            resp = await client.post(
                f"{url}/rest/v1/rpc/exec_sql_returning",
                json={"query": verify_sql},
                headers=headers,
                timeout=30,
            )
            if resp.status_code in (200, 201):
                indexes = resp.json()
                print("Indexes on rag_vectors:")
                for idx in indexes:
                    print(f"  {idx.get('indexname', '?')}: {idx.get('indexdef', '?')[:100]}...")
            else:
                print(f"Verify query returned: {resp.status_code}")
        except Exception as e:
            print(f"Could not verify indexes: {e}")
            print("Check manually: SELECT indexname FROM pg_indexes WHERE tablename = 'rag_vectors';")


if __name__ == "__main__":
    asyncio.run(create_hnsw_index())
