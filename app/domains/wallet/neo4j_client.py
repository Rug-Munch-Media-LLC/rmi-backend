"""
Neo4j graph client for wallet entity resolution.

Connects to the on-premises Neo4j v5.26.28 instance populated with
wallet labels from ClickHouse (wallet_memory.wallet_labels).

Schema:
    (Wallet {address, chain, last_seen})
    (Label {name, category, source})
    (Wallet)-[:LABELED_AS {source, confidence, is_sanctioned}]->(Label)

Usage:
    from app.domains.wallet.neo4j_client import get_neo4j_driver, query_wallet_labels
    labels = await query_wallet_labels("0x...")
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "rugmunch-neo4j-2026")

_driver: Any = None


def get_neo4j_driver() -> Any:
    """Lazy-init Neo4j Bolt driver. Returns None if unavailable."""
    global _driver
    if _driver is not None:
        return _driver

    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        # Verify connectivity
        with driver.session() as s:
            result = s.run("RETURN 1 AS ok").single()
            if result and result["ok"] == 1:
                log.info("Neo4j connected: %s", NEO4J_URI)
                _driver = driver
                return _driver
    except ImportError:
        log.warning("neo4j driver not installed — entity resolution degraded")
    except Exception as e:
        log.warning("Neo4j connection failed: %s", e)

    _driver = None
    return None


async def query_wallet_labels(address: str) -> list[dict[str, Any]]:
    """Query Neo4j for all labels associated with a wallet address.

    Returns list of dicts with keys: name, category, source, confidence, is_sanctioned.
    Empty list if address not found or Neo4j unavailable.

    This is the primary entity resolution query for rugmaps.
    """
    driver = get_neo4j_driver()
    if driver is None:
        return []

    def _query() -> list[dict[str, Any]]:
        with driver.session() as s:
            result = s.run(
                """
                MATCH (w:Wallet {address: $address})-[r:LABELED_AS]->(l:Label)
                RETURN l.name AS name,
                       l.category AS category,
                       r.source AS source,
                       r.confidence AS confidence,
                       r.is_sanctioned AS is_sanctioned,
                       w.chain AS chain
                ORDER BY r.confidence DESC
                """,
                address=address.lower(),
            )
            return [dict(record) for record in result]

    try:
        import asyncio

        return await asyncio.to_thread(_query)
    except Exception as e:
        log.debug("Neo4j query_wallet_labels failed for %s: %s", address[:12], e)
        return []


async def query_wallet_entity(address: str) -> dict[str, Any] | None:
    """Get consolidated entity info for a wallet address from Neo4j.

    Returns structured dict or None if no labels found.

    This is what the rugmaps engine calls for entity enrichment.
    """
    labels = await query_wallet_labels(address)
    if not labels:
        return None

    # Find the best (highest confidence) label
    best = max(labels, key=lambda lb: lb.get("confidence", 0) or 0)

    # Collect all categories as tags
    categories = list({lb.get("category", "") for lb in labels if lb.get("category")})
    sources = list({lb.get("source", "") for lb in labels if lb.get("source")})

    # Determine wallet type from labels
    wallet_type = _infer_wallet_type(labels)

    return {
        "name": best.get("name"),
        "label": best.get("name"),
        "type": wallet_type,
        "entity_type": wallet_type,
        "categories": categories,
        "tags": categories,
        "sources": sources,
        "is_sanctioned": any(lb.get("is_sanctioned") for lb in labels),
        "confidence": best.get("confidence", 0),
        "chain": best.get("chain", "unknown"),
        "label_count": len(labels),
    }


async def query_wallet_type(address: str) -> str | None:
    """Quick wallet type classification from Neo4j labels.

    Returns: exchange, scammer, defi, bridge, whale, or None if unknown.
    This is used by the rugmaps classify_wallet function.
    """
    labels = await query_wallet_labels(address)
    if not labels:
        return None

    return _infer_wallet_type(labels)


def _infer_wallet_type(labels: list[dict[str, Any]]) -> str:
    """Infer wallet type from Neo4j labels."""
    categories = {lb.get("category", "").lower().strip() for lb in labels if lb.get("category")}
    names = {lb.get("name", "").lower().strip() for lb in labels if lb.get("name")}

    # Priority order: scam > exchange > defi > bridge
    scam_cats = {"scam", "phishing", "hack", "exploit", "fraud", "malicious", "heist"}
    if categories & scam_cats:
        return "scammer"

    wallet_cats = {"exchange", "cex", "centralized"}
    if categories & wallet_cats:
        return "exchange"

    if "defi" in categories or "protocol" in categories:
        return "defi"

    if "bridge" in categories or "cross-chain" in categories:
        return "bridge"

    if "mev" in categories or "bot" in categories:
        return "bot"

    # Check if any label name matches known exchange/defi patterns
    exchange_keywords = {
        "binance",
        "coinbase",
        "kraken",
        "okx",
        "bybit",
        "kucoin",
        "crypto.com",
        "gate.io",
        "huobi",
        "bitfinex",
        "gemini",
    }
    if any(any(kw in name for kw in exchange_keywords) for name in names):
        return "exchange"

    return "defi" if categories else None


async def find_related_wallets(
    address: str,
    max_depth: int = 2,
    min_confidence: float = 0.5,
) -> list[dict[str, Any]]:
    """Find wallets that share labels with the given address (entity resolution).

    This enables cross-address entity clustering via shared labels.
    E.g., if address A and B both have label "Binance Hot Wallet", they're related.

    Args:
        address: Wallet address to start from
        max_depth: How many hops through shared labels
        min_confidence: Minimum relationship confidence threshold

    Returns:
        List of related wallet dicts with address, chain, shared_labels, confidence
    """
    driver = get_neo4j_driver()
    if driver is None:
        return []

    def _query() -> list[dict[str, Any]]:
        with driver.session() as s:
            result = s.run(
                """
                MATCH (w:Wallet {address: $address})-[r:LABELED_AS]->(l:Label)<-[r2:LABELED_AS]-(related:Wallet)
                WHERE related.address <> $address
                  AND r.confidence >= $min_conf
                  AND r2.confidence >= $min_conf
                WITH related, collect(DISTINCT l.name) AS shared_labels,
                     count(DISTINCT l) AS label_overlap,
                     max(r2.confidence) AS max_conf
                RETURN related.address AS address,
                       related.chain AS chain,
                       shared_labels,
                       label_overlap,
                       max_conf AS confidence
                ORDER BY label_overlap DESC, max_conf DESC
                LIMIT 50
                """,
                address=address.lower(),
                min_conf=min_confidence,
            )
            return [dict(record) for record in result]

    try:
        import asyncio

        return await asyncio.to_thread(_query)
    except Exception as e:
        log.debug("Neo4j find_related_wallets failed for %s: %s", address[:12], e)
        return []


async def get_graph_stats() -> dict[str, int]:
    """Get Neo4j graph statistics."""
    driver = get_neo4j_driver()
    if driver is None:
        return {"wallets": 0, "labels": 0, "relationships": 0}

    def _query() -> dict[str, int]:
        with driver.session() as s:
            wallets = s.run("MATCH (w:Wallet) RETURN count(w) AS c").single()["c"]
            labels = s.run("MATCH (l:Label) RETURN count(l) AS c").single()["c"]
            rels = s.run("MATCH ()-[r:LABELED_AS]->() RETURN count(r) AS c").single()["c"]
            return {"wallets": wallets, "labels": labels, "relationships": rels}

    try:
        import asyncio

        return await asyncio.to_thread(_query)
    except Exception as e:
        log.debug("Neo4j get_graph_stats failed: %s", e)
        return {"wallets": 0, "labels": 0, "relationships": 0}


__all__ = [
    "find_related_wallets",
    "get_graph_stats",
    "get_neo4j_driver",
    "query_wallet_entity",
    "query_wallet_labels",
    "query_wallet_type",
]
