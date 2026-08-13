"""
x402 Enrichment — Address Extraction and Label Lookup
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.logging import get_logger
from app.routers.x402_enrichment.models import MAX_BATCH_SIZE, SIMILARITY_THRESHOLD, WalletLabel

logger = get_logger("x402.enrich")


def extract_addresses(result: dict[str, Any], tool_name: str) -> list[str]:
    """
    Extract wallet/token addresses from a tool result for enrichment lookup.
    Handles common response patterns across all x402 tools.
    """
    addresses = set()
    if not isinstance(result, dict):
        return []

    # Direct fields
    for key in (
        "address",
        "wallet",
        "token_address",
        "contract_address",
        "pair_address",
        "creator",
        "owner",
        "payTo",
    ):
        val = result.get(key)
        if isinstance(val, str) and len(val) >= 32:
            addresses.add(val)

    # Nested lists (holder breakdown, top wallets, cluster members)
    for list_key in (
        "holders",
        "top_wallets",
        "top_holders",
        "cluster_members",
        "whale_wallets",
        "suspicious_wallets",
        "linked_addresses",
        "audits",
        "results",
    ):
        items = result.get(list_key, [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    addr = item.get("address") or item.get("wallet") or item.get("token_address")
                    if addr and isinstance(addr, str) and len(addr) >= 32:
                        addresses.add(addr)
                elif isinstance(item, str) and len(item) >= 32:
                    addresses.add(item)

    # Token comparisons have numbered entries
    for _key, val in result.items():
        if isinstance(val, dict):
            addr = val.get("address") or val.get("token_address")
            if addr and isinstance(addr, str) and len(addr) >= 32:
                addresses.add(addr)

    return list(addresses)[:MAX_BATCH_SIZE]


def lookup_labels(addresses: list[str]) -> tuple[dict[str, list[WalletLabel]], float]:
    """
    Batch lookup wallet labels from ClickHouse.
    Returns: {address: [WalletLabel, ...]}, query_time_ms
    """
    if not addresses:
        return {}, 0

    t0 = time.time()
    results: dict[str, list[WalletLabel]] = {a: [] for a in addresses}

    try:
        from app.routers.x402_enrichment.utils import _ch

        ch = _ch()
        placeholders = ", ".join(["(a)" for _a in addresses])
        query = f"""
        SELECT address, label_name, label_category, label_subtype, source, is_sanctioned
        FROM wallet_memory.wallet_labels
        WHERE address IN ({placeholders})
        ORDER BY
            CASE label_category
                WHEN 'sanctioned' THEN 0
                WHEN 'phish-hack' THEN 1
                WHEN 'scam' THEN 2
                WHEN 'etherscan-phish-hack-list' THEN 3
                WHEN 'exploit' THEN 4
                WHEN 'heist' THEN 5
                ELSE 99
            END,
            loaded_at DESC
        LIMIT {MAX_BATCH_SIZE * 5}
        """
        rows = ch.execute(query, addresses)

        for row in rows:
            addr = row[0]
            label = WalletLabel(
                address=addr,
                label_name=row[1] or "",
                label_category=row[2] or "",
                label_subtype=row[3] or "",
                source=row[4] or "",
                is_sanctioned=bool(row[5]),
            )
            results[addr].append(label)

    except (ConnectionError, TimeoutError, OSError) as e:
        logger.warning("CH label lookup failed: %s", e)
        return {}, (time.time() - t0) * 1000

    return results, (time.time() - t0) * 1000


async def search_similar_patterns(
    addresses: list[str],
    r: Any = None,
) -> tuple[list[dict[str, Any]], float]:
    """
    Search for similar scam/wallet patterns using RAG embeddings.
    Falls back gracefully if RAG is unavailable.
    """
    t0 = time.time()
    patterns = []

    try:
        from app.rag.compat import get_embedder, search_multi_collection

        embedder = await get_embedder()
        if not embedder:
            return [], 0

        for addr in addresses[:10]:
            cache_key = f"x402:enrich:rag:{addr}"
            if r:
                cached = r.get(cache_key)
                if cached:
                    patterns.append(json.loads(cached))
                    continue

            try:
                emb = await embedder.embed_wallet(addr)
                if not emb or not emb.vector:
                    continue

                hits = await search_multi_collection(  # type: ignore[arg-type] — compat layer types query as str but accepts vectors at runtime
                    emb.vector,
                    collections=["wallet_profiles", "known_scams"],
                    limit=3,
                    min_similarity=SIMILARITY_THRESHOLD,
                )
                if hits:
                    pattern = {
                        "address": addr,
                        "matches": [
                            {
                                "collection": h.get("collection", ""),
                                "score": round(h.get("score", 0), 3),
                                "type": h.get("metadata", {}).get("type", ""),
                                "description": h.get("metadata", {}).get("description", "")[:200],
                            }
                            for h in hits[:2]
                        ],
                    }
                    patterns.append(pattern)
                    if r:
                        from app.routers.x402_enrichment.models import ENRICH_CACHE_TTL

                        r.setex(cache_key, ENRICH_CACHE_TTL, json.dumps(pattern))
            except (ConnectionError, TimeoutError, OSError) as e:
                logger.debug("RAG search failed for %s: %s", addr[:10], e)
                continue

    except (ConnectionError, TimeoutError, OSError) as e:
        logger.debug("RAG enrichment unavailable: %s", e)

    return patterns, (time.time() - t0) * 1000


def build_risk_summary(
    labels: dict[str, list[WalletLabel]],
    sanctioned: list[str],
    similar_patterns: list[dict[str, Any]],
) -> str:
    """Build a concise human-readable risk summary."""
    parts = []

    total_addrs = len(labels)
    labeled = sum(1 for v in labels.values() if v)
    if labeled:
        parts.append(f"{labeled}/{total_addrs} addresses have known labels")

    if sanctioned:
        parts.append(f"{len(sanctioned)} OFAC-sanctioned wallet(s)")

    scam_count = sum(
        1
        for v in labels.values()
        for line in v
        if line.label_category in ("phish-hack", "scam", "etherscan-phish-hack-list")
    )
    if scam_count:
        parts.append(f"{scam_count} known phishing/scam address(es) detected")

    cex_count = sum(1 for v in labels.values() for line in v if line.label_category == "cex")
    if cex_count:
        parts.append(f"{cex_count} exchange wallet(s) identified")

    if similar_patterns:
        high_matches = [p for p in similar_patterns if p.get("matches")]
        if high_matches:
            parts.append(f"{len(high_matches)} address(es) match known scam patterns")

    return ". ".join(parts) + "." if parts else ""
