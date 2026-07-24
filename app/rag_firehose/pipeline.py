"""
RAG Firehose - Ingestion Pipeline
===================================
Filters, deduplicates, enriches, and stores documents into RAG.
Handles quality scoring, entity extraction, rate limiting, and batch ingest.
"""

from __future__ import annotations
import asyncio
import hashlib
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.rag_firehose.models import (
    HASH_TTL,
    MIN_QUALITY_SCORE,
    RAG_API,
    RATE_LIMITS,
)

logger = get_logger("rag.firehose")

class IngestionPipeline:
    """Filters, deduplicates, and enriches documents before RAG storage."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self._rate_trackers: dict[str, list[float]] = {}

    def _check_rate(self, collection: str) -> bool:
        """Return True if we're under the rate limit for this collection."""
        limit = RATE_LIMITS.get(collection, RATE_LIMITS["default"])
        now = time.monotonic()
        times = self._rate_trackers.setdefault(collection, [])
        times[:] = [t for t in times if now - t < 60]
        return len(times) < limit

    def _record_ingest(self, collection: str) -> None:
        """Record an ingestion for rate limiting."""
        self._rate_trackers.setdefault(collection, []).append(time.monotonic())

    def make_content_hash(self, content: str) -> str:
        """Deterministic hash for dedup."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    async def is_duplicate(self, content_hash: str, collection: str) -> bool:
        """Check if content hash already exists in Redis dedup set."""
        try:
            resp = await self.client.get(
                f"{RAG_API}/dedup-check",
                params={"hash": content_hash, "collection": collection},
                timeout=5,
            )
            if resp.status_code == 200:
                return resp.json().get("exists", False)
        except (ConnectionError, OSError, TimeoutError):
            pass
        return False

    def score_quality(self, content: str, metadata: dict) -> int:
        """Score document quality 0-100. Skip low-signal content."""
        score = 50  # Start neutral

        # Length bonus
        if len(content) > 500:
            score += 15
        elif len(content) > 200:
            score += 10
        elif len(content) < 50:
            score -= 20

        # Entity richness
        entity_count = 0
        for key in ("address", "chain", "token", "protocol"):
            if metadata.get(key):
                entity_count += 1
        score += entity_count * 5

        # Source authority
        high_authority = {"etherscan", "chainabuse", "rekt", "certik", "slowmist", "peckshield"}
        if metadata.get("source", "").lower() in high_authority:
            score += 20

        # Severity boost
        if metadata.get("severity") == "critical":
            score += 15
        elif metadata.get("severity") == "high":
            score += 10

        # Penalize variants
        if metadata.get("is_variant"):
            score -= 10

        return max(0, min(100, score))

    def extract_entities(self, content: str) -> dict:
        """Extract addresses, chains, tokens from content."""
        entities: dict[str, list[str]] = {"addresses": [], "chains": [], "tokens": [], "protocols": []}

        # EVM addresses
        evm_addrs = re.findall(r"0x[a-fA-F0-9]{40}", content)
        entities["addresses"].extend(evm_addrs[:10])

        # Solana addresses
        sol_addrs = re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", content)
        entities["addresses"].extend([a for a in sol_addrs[:10] if a not in entities["addresses"]])

        # Known chains
        known_chains = [
            "ethereum", "bsc", "polygon", "arbitrum", "optimism", "avalanche",
            "solana", "base", "fantom", "gnosis", "celo", "zksync", "linea",
            "scroll", "mantle", "sui", "aptos", "near", "tron", "bitcoin",
        ]
        for chain in known_chains:
            if chain.lower() in content.lower():
                entities["chains"].append(chain)

        # Token symbols ($TOKEN)
        tokens = re.findall(r"\$([A-Z]{2,10})", content)
        entities["tokens"].extend(tokens[:10])

        # Known protocols
        known_protocols = [
            "uniswap", "aave", "curve", "balancer", "sushi", "pancake",
            "raydium", "jupiter", "orca", "marinade", "lido", "eigenlayer",
            "compound", "maker", "yearn", "convex",
        ]
        for proto in known_protocols:
            if proto.lower() in content.lower():
                entities["protocols"].append(proto)

        return entities

    async def ingest(
        self, collection: str, content: str, metadata: dict, doc_id: str | None = None
    ) -> dict[str, Any]:
        """Run the full pipeline: dedup -> quality -> extract -> classify -> store."""

        # 1. Hash and dedup
        content_hash = self.make_content_hash(content)
        if await self.is_duplicate(content_hash, collection):
            return {"status": "duplicate", "hash": content_hash}

        # 2. Quality filter
        quality = self.score_quality(content, metadata)
        if quality < MIN_QUALITY_SCORE:
            return {"status": "skipped", "reason": "low_quality", "score": quality}

        # 3. Entity extraction
        entities = self.extract_entities(content)
        metadata.update(
            {
                "entities": entities,
                "quality_score": quality,
                "content_hash": content_hash,
                "ingested_at": datetime.now(UTC).isoformat(),
            }
        )

        # 4. Rate limit check
        if not self._check_rate(collection):
            return {"status": "rate_limited", "collection": collection}

        # 5. Ingest into RAG
        try:
            payload = {"collection": collection, "content": content, "metadata": metadata}
            if doc_id:
                payload["doc_id"] = doc_id

            resp = await self.client.post(f"{RAG_API}/ingest", json=payload, timeout=30)
            self._record_ingest(collection)

            if resp.status_code == 200:
                result = resp.json()
                asyncio.create_task(self._mark_ingested(content_hash, collection))
                return {
                    "status": "ingested",
                    "id": result.get("id"),
                    "quality": quality,
                    "entities": entities,
                }
            else:
                return {"status": "error", "code": resp.status_code, "detail": resp.text[:200]}
        except Exception as e:
            return {"status": "error", "detail": str(e)[:200]}

    async def _mark_ingested(self, content_hash: str, collection: str) -> None:
        """Mark content hash in Redis dedup set."""
        try:
            ttl = HASH_TTL.get(collection, HASH_TTL["default"])
            await self.client.post(
                f"{RAG_API}/dedup-mark",
                json={"hash": content_hash, "collection": collection, "ttl": ttl},
                timeout=5,
            )
        except (ConnectionError, OSError, TimeoutError):
            pass

    async def ingest_batch(self, docs: list[dict]) -> dict[str, int]:
        """Ingest multiple documents with rate limiting."""
        stats = {"ingested": 0, "duplicate": 0, "skipped": 0, "errors": 0}

        for doc in docs:
            collection = doc.get("collection", "general")
            content = doc.get("content", "")
            metadata = doc.get("metadata", {})
            doc_id = doc.get("doc_id")

            result = await self.ingest(collection, content, metadata, doc_id)
            status = result.get("status", "error")
            if status == "ingested":
                stats["ingested"] += 1
            elif status == "duplicate":
                stats["duplicate"] += 1
            elif status == "skipped":
                stats["skipped"] += 1
            else:
                stats["errors"] += 1

            await asyncio.sleep(0.05)

        return stats
