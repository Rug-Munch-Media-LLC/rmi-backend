"""RugMaps Phase 0→1 seeder — populate the ``rgm_wallets`` Qdrant collection.

Reads labeled wallets from ClickHouse (``wallet_memory.wallet_labels``),
embeds the label text via the Qwen3-Embedding-4B llama.cpp endpoint (
2560-dim), and upserts named ``dense`` vectors into the ``rgm_wallets``
collection.

Notes
-----
- The app's ClickHouse user (``rmi``) has SELECT grants on
  ``wallet_memory.*`` — that table is the label source of truth. Column
  mapping: ``address``, ``chain_id`` → chain, ``label_name`` → label,
  ``label_category`` → label_type. Override with ``RGM_SEED_TABLE`` env if
  a different table/view with the same columns should be used.
- Point IDs are deterministic UUIDs (``uuid5`` over ``chain:address``) so
  re-running the seeder is idempotent — the same point is replaced.
- Embeddings are batched through llama.cpp's OpenAI-compatible
  ``/embeddings`` endpoint (model ``qwen3-embedding-4b`` = 2560-dim).

Usage (CLI wrapper):
    python scripts/ops/rugmaps_seed_wallets.py --limit 10000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from qdrant_client import AsyncQdrantClient, models

from app.config import resolve_service_url
from app.core.logging import get_logger
from app.domains.rugmaps.qdrant import COLLECTIONS, DENSE_VECTOR_NAME

logger = get_logger(__name__)

WALLET_COLLECTION = "rgm_wallets"
EMBEDDING_MODEL = "qwen3-embedding-4b"
EMBED_BATCH = 64  # texts per Qwen3 request
UPSERT_BATCH = 128  # points per Qdrant upsert

# Rows whose label is the "Null …" placeholder are excluded because they
# embed as noise. Source-prefixed label names ("wallet_labels:…",
# "scanner:…") are internal pipeline artifacts, not real entity names, so
# they are excluded too. We fetch a multiple of the target limit and dedupe
# by (chain, address) in Python — a window function over the 39M-row table
# exceeds ClickHouse's memory budget, while ORDER BY … LIMIT is a light
# top-N sort.
SEED_QUERY = """
SELECT address, chain_id, label_name, label_category
FROM {table}
WHERE label_name != ''
  AND label_name NOT LIKE 'Null%'
  AND label_name NOT LIKE 'wallet_labels:%'
  AND label_name NOT LIKE 'scanner:%'
  AND label_category != ''
ORDER BY loaded_at DESC
LIMIT {limit}
"""

# Fetch this multiple of the requested limit so Python-side dedupe still
# yields exactly ``limit`` unique (chain, address) rows.
FETCH_FACTOR = 3


def _clickhouse_settings() -> dict[str, Any]:
    """Resolve ClickHouse connection settings from env (no secrets printed)."""
    return {
        "host": os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse"),
        "port": int(os.getenv("CLICKHOUSE_PORT", "9000")),
        "user": os.getenv("CLICKHOUSE_USER", "rmi"),
        "password": os.getenv("CLICKHOUSE_PASSWORD", ""),
        "database": os.getenv("CLICKHOUSE_DB", os.getenv("CLICKHOUSE_DATABASE", "default")),
    }


def _embed_text(label_type: str, label: str) -> str:
    """Compose the text that gets embedded for a wallet label row."""
    return f"{label_type}: {label}"


def _point_id(chain: str, address: str) -> str:
    """Deterministic point ID so re-seeding replaces rather than duplicates."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{chain}:{address.lower()}"))


async def _embed_batch(client: httpx.AsyncClient, embed_url: str, texts: list[str]) -> list[list[float]]:
    """Embed one batch via Qwen3-4B; returns 2560-dim vectors.

    Falls back to a zero vector per failed text so a single bad request
    does not abort the whole seed.
    """
    resp = await client.post(
        f"{embed_url}/embeddings",
        json={"model": EMBEDDING_MODEL, "input": texts},
    )
    if resp.status_code != 200:
        logger.warning("embed_status=%s body=%s", resp.status_code, resp.text[:200])
        return [[0.0] * COLLECTIONS[WALLET_COLLECTION] for _ in texts]
    data = resp.json().get("data", [])
    by_index = {item.get("index"): item.get("embedding", []) for item in data}
    vectors: list[list[float]] = []
    for i in range(len(texts)):
        vec = by_index.get(i, [])
        if len(vec) == COLLECTIONS[WALLET_COLLECTION]:
            vectors.append(vec)
        else:
            logger.warning("embed_missing_index index=%d", i)
            vectors.append([0.0] * COLLECTIONS[WALLET_COLLECTION])
    return vectors


async def seed_wallets(
    limit: int = 10_000,
    qdrant_url: str | None = None,
    embed_url: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Seed ``rgm_wallets`` from ClickHouse labeled wallets.

    Returns a stats dict: rows fetched, vectors embedded, points upserted,
    and skipped/failed counts.
    """
    qdrant_url = (
        qdrant_url
        if qdrant_url is not None
        else resolve_service_url("QDRANT_URL", docker="http://rmi-qdrant:6333", local="http://localhost:6333")
    ).rstrip("/")
    embed_url = (
        embed_url
        if embed_url is not None
        else resolve_service_url("EMBEDDER_URL", docker="http://172.22.0.1:8004/v1", local="http://127.0.0.1:8005/v1")
    ).rstrip("/")
    table = os.getenv("RGM_SEED_TABLE", "wallet_memory.wallet_labels")

    # 1) Pull labeled wallets from ClickHouse (fetch extra rows for dedupe)
    from clickhouse_driver import Client as ClickHouseClient

    ch = ClickHouseClient(**_clickhouse_settings())
    try:
        rows = ch.execute(SEED_QUERY.format(table=table, limit=int(limit) * FETCH_FACTOR))
    finally:
        ch.disconnect()

    # Dedupe by (chain, address) — first occurrence is the most recent
    # (ORDER BY loaded_at DESC). Duplicate pairs would otherwise collide on
    # the deterministic point ID and silently overwrite each other.
    seen: set[tuple[str, str]] = set()
    unique_rows: list[tuple[str, str, str, str]] = []
    duplicates_skipped = 0
    for row in rows:
        key = (row[1].lower(), row[0].lower())
        if key in seen:
            duplicates_skipped += 1
            continue
        seen.add(key)
        unique_rows.append(row)
        if len(unique_rows) >= int(limit):
            break
    rows = unique_rows

    logger.info("clickhouse_rows=%d limit=%d duplicates_skipped=%d", len(rows), limit, duplicates_skipped)

    embedded_at = datetime.now(UTC).isoformat()
    points: list[models.PointStruct] = []
    stats: dict[str, Any] = {
        "collection": WALLET_COLLECTION,
        "rows_fetched": len(rows),
        "duplicates_skipped": duplicates_skipped,
        "embedded": 0,
        "zero_vectors": 0,
        "upserted": 0,
        "failed": 0,
        "dry_run": dry_run,
    }

    async with httpx.AsyncClient(timeout=60) as http:
        for start in range(0, len(rows), EMBED_BATCH):
            batch = rows[start : start + EMBED_BATCH]
            batch_points = []
            try:
                vectors = await _embed_batch(http, embed_url, [_embed_text(r[3], r[2]) for r in batch])
            except Exception as e:
                logger.warning("embed_batch_error offset=%d err=%s", start, e)
                stats["failed"] += len(batch)
                continue

            for row, vector in zip(batch, vectors, strict=False):
                address, chain, label, label_type = row[0], row[1], row[2], row[3]
                if vector and any(vector):
                    stats["embedded"] += 1
                else:
                    stats["zero_vectors"] += 1
                batch_points.append(
                    models.PointStruct(
                        id=_point_id(chain, address),
                        vector={DENSE_VECTOR_NAME: vector},
                        payload={
                            "address": address,
                            "chain": chain,
                            "label": label,
                            "label_type": label_type or "",
                            "embedded_at": embedded_at,
                        },
                    )
                )

            points.extend(batch_points)
            if len(points) >= UPSERT_BATCH:
                if dry_run:
                    points.clear()
                    stats["upserted"] += len(batch_points)
                    continue
                stats["upserted"] += await _upsert_points(qdrant_url, points)
                points.clear()

        # Flush remainder
        if points and not dry_run:
            stats["upserted"] += await _upsert_points(qdrant_url, points)
        elif points and dry_run:
            stats["upserted"] += len(points)

    return stats


async def _upsert_points(qdrant_url: str, points: list[models.PointStruct]) -> int:
    """Upsert a batch of points into ``rgm_wallets``; returns upserted count."""
    client = AsyncQdrantClient(url=qdrant_url)
    try:
        result = await client.upsert(
            collection_name=WALLET_COLLECTION,
            points=points,
            wait=True,
        )
        return len(points) if result.status == models.UpdateStatus.COMPLETED else 0
    except Exception as e:
        logger.warning("qdrant_upsert_error batch=%d err=%s", len(points), e)
        return 0
    finally:
        await client.close()


def main() -> int:
    """CLI entrypoint for ``scripts/ops/rugmaps_seed_wallets.py``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10_000, help="Max wallets to seed (default 10000)")
    parser.add_argument("--qdrant-url", default=None, help="Qdrant base URL (default: QDRANT_URL env)")
    parser.add_argument("--embed-url", default=None, help="Qwen3 /v1/embeddings base URL (default: EMBEDDER_URL env)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + embed but do not upsert")
    args = parser.parse_args()

    stats = asyncio.run(
        seed_wallets(
            limit=args.limit,
            qdrant_url=args.qdrant_url,
            embed_url=args.embed_url,
            dry_run=args.dry_run,
        )
    )
    print(json.dumps(stats, indent=2, default=str))
    return 0 if stats.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
