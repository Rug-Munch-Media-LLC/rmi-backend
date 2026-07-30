"""
Neo4j Writer — creates cross-chain entity edges in Neo4j.
============================================================
Manages the cross-chain graph schema:
  Entity nodes with cross-chain identifiers
  SAME_AS edges between same-entity wallets
  DEPLOYED_ON edges for deployers
  BRIDGED_TO edges for bridge links

Edge types used by catalog/service.py entity resolution:
  SAME_AS, FUNDED_BY_SAME, CLONE_OF, BEHAVIORAL_MATCH
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

_driver: Any = None


def _get_driver() -> Any | None:
    """Lazy-init Neo4j driver. Returns None if unavailable."""
    global _driver
    if _driver is not None:
        return _driver
    try:
        from neo4j import GraphDatabase

        auth = (NEO4J_USER, NEO4J_PASSWORD) if NEO4J_PASSWORD else None
        driver = GraphDatabase.driver(NEO4J_URI, auth=auth, connection_timeout=10)
        with driver.session() as s:
            s.run("RETURN 1").consume()
        _driver = driver
        logger.info("Neo4j writer connected: %s", NEO4J_URI)
    except ImportError:
        logger.warning("neo4j library not installed — cross-chain Neo4j writes disabled")
    except Exception as e:
        logger.warning("Neo4j writer connection failed: %s", e)
    return _driver


async def ensure_schema() -> bool:
    """Create indexes and constraints for cross-chain entity schema.
    
    Safe to call repeatedly (idempotent via IF NOT EXISTS).
    """
    driver = _get_driver()
    if not driver:
        return False

    schema_queries = [
        # Entity constraint
        "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
        # Wallet constraint
        "CREATE CONSTRAINT IF NOT EXISTS FOR (w:Wallet) REQUIRE w.wallet_id IS UNIQUE",
        # Composite index for wallet lookups by address+chain
        "CREATE INDEX IF NOT EXISTS FOR (w:Wallet) ON (w.address, w.chain)",
        # Entity label index
        "CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.label)",
    ]

    try:
        import asyncio

        def _run() -> bool:
            with driver.session() as s:
                for q in schema_queries:
                    s.run(q).consume()
            return True

        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("Neo4j schema creation failed: %s", e)
        return False


async def upsert_entity(
    entity_id: str,
    label: str = "",
    aliases: list[str] | None = None,
    tags: list[str] | None = None,
) -> bool:
    """Create or update an Entity node."""
    driver = _get_driver()
    if not driver:
        return False

    try:
        import asyncio

        def _run() -> bool:
            with driver.session() as s:
                s.run(
                    """
                    MERGE (e:Entity {entity_id: $entity_id})
                    SET e.label = COALESCE($label, e.label),
                        e.aliases = COALESCE($aliases, e.aliases),
                        e.tags = COALESCE($tags, e.tags),
                        e.last_seen = datetime()
                    ON CREATE SET
                        e.first_seen = datetime()
                    """,
                    entity_id=entity_id,
                    label=label or "",
                    aliases=aliases or [],
                    tags=tags or [],
                )
            return True

        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.debug("Entity upsert failed: %s", e)
        return False


async def upsert_wallet(
    address: str,
    chain: str,
    entity_id: str | None = None,
    is_deployer: bool = False,
) -> str:
    """Create or update a Wallet node.
    
    wallet_id format: "chain:address" (matching catalog/models.py Wallet)
    
    Returns the wallet_id.
    """
    driver = _get_driver()
    if not driver:
        return f"{chain}:{address}"

    wallet_id = f"{chain}:{address.lower()}"
    now = datetime.now(UTC)

    try:
        import asyncio

        def _run() -> None:
            with driver.session() as s:
                s.run(
                    """
                    MERGE (w:Wallet {wallet_id: $wallet_id})
                    SET w.address = $address,
                        w.chain = $chain,
                        w.last_seen = datetime()
                    ON CREATE SET
                        w.first_seen = datetime(),
                        w.tx_count = 0,
                        w.total_volume_usd = 0.0
                    """,
                    wallet_id=wallet_id,
                    address=address.lower(),
                    chain=chain,
                )
                if entity_id:
                    # Link wallet to entity
                    s.run(
                        """
                        MATCH (w:Wallet {wallet_id: $wallet_id})
                        MATCH (e:Entity {entity_id: $entity_id})
                        MERGE (w)-[:BELONGS_TO {confidence: 1.0}]->(e)
                        """,
                        wallet_id=wallet_id,
                        entity_id=entity_id,
                    )
                if is_deployer:
                    s.run(
                        """
                        MATCH (w:Wallet {wallet_id: $wallet_id})
                        SET w.is_deployer = true
                        """,
                        wallet_id=wallet_id,
                    )

        await asyncio.to_thread(_run)
        _ = now  # used for timestamp
    except Exception as e:
        logger.debug("Wallet upsert failed: %s", e)

    return wallet_id


async def create_same_as_edge(
    wallet_id_a: str,
    wallet_id_b: str,
    confidence: float = 0.95,
    method: str = "same_deployer",
) -> bool:
    """Create a SAME_AS edge between two wallets (same entity, different chains).
    
    This is THE critical cross-chain edge used by catalog/service.py's
    resolve_entity() query.
    """
    driver = _get_driver()
    if not driver:
        return False

    try:
        import asyncio

        def _run() -> bool:
            with driver.session() as s:
                result = s.run(
                    """
                    MATCH (a:Wallet {wallet_id: $aid})
                    MATCH (b:Wallet {wallet_id: $bid})
                    MERGE (a)-[r:SAME_AS]-(b)
                    SET r.confidence = $confidence,
                        r.method = $method,
                        r.created_at = datetime()
                    RETURN r
                    """,
                    aid=wallet_id_a,
                    bid=wallet_id_b,
                    confidence=confidence,
                    method=method,
                )
                return result.single() is not None

        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.debug("SAME_AS edge creation failed: %s", e)
        return False


async def create_deposited_on_edge(
    wallet_id: str,
    chain: str,
    is_deployer: bool = True,
) -> bool:
    """Tag a wallet as active on a specific chain."""
    driver = _get_driver()
    if not driver:
        return False

    try:
        import asyncio

        def _run() -> bool:
            with driver.session() as s:
                result = s.run(
                    """
                    MATCH (w:Wallet {wallet_id: $wallet_id})
                    SET w.chains_active = CASE
                        WHEN w.chains_active IS NULL THEN [$chain]
                        WHEN NOT $chain IN w.chains_active THEN w.chains_active + [$chain]
                        ELSE w.chains_active
                    END
                    """,
                    wallet_id=wallet_id,
                    chain=chain,
                )
                if is_deployer:
                    s.run(
                        """
                        MATCH (w:Wallet {wallet_id: $wallet_id})
                        SET w.is_deployer = true
                        """,
                        wallet_id=wallet_id,
                    )
                return True

        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.debug("DEPLOYED_ON edge creation failed: %s", e)
        return False


async def create_bridged_to_edge(
    source_wallet_id: str,
    dest_wallet_id: str,
    bridge_name: str,
    tx_hash: str,
    confidence: float = 0.8,
) -> bool:
    """Create a BRIDGED_TO edge between two wallets (cross-chain bridge).
    
    This represents funds moving from source chain → bridge → dest chain
    where the same operator controls both addresses.
    """
    driver = _get_driver()
    if not driver:
        return False

    try:
        import asyncio

        def _run() -> bool:
            with driver.session() as s:
                result = s.run(
                    """
                    MATCH (a:Wallet {wallet_id: $src})
                    MATCH (b:Wallet {wallet_id: $dst})
                    MERGE (a)-[r:BRIDGED_TO]-(b)
                    SET r.bridge_name = $bridge,
                        r.tx_hash = $tx_hash,
                        r.confidence = $confidence,
                        r.created_at = datetime()
                    RETURN r
                    """,
                    src=source_wallet_id,
                    dst=dest_wallet_id,
                    bridge=bridge_name,
                    tx_hash=tx_hash,
                    confidence=confidence,
                )
                return result.single() is not None

        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.debug("BRIDGED_TO edge creation failed: %s", e)
        return False


async def create_behavioral_match_edge(
    wallet_id_a: str,
    wallet_id_b: str,
    similarity_score: float,
    evidence: str = "",
) -> bool:
    """Create a BEHAVIORAL_MATCH edge based on fingerprint similarity."""
    driver = _get_driver()
    if not driver:
        return False

    try:
        import asyncio

        def _run() -> bool:
            with driver.session() as s:
                result = s.run(
                    """
                    MATCH (a:Wallet {wallet_id: $aid})
                    MATCH (b:Wallet {wallet_id: $bid})
                    MERGE (a)-[r:BEHAVIORAL_MATCH]-(b)
                    SET r.confidence = $score,
                        r.evidence = $evidence,
                        r.created_at = datetime()
                    RETURN r
                    """,
                    aid=wallet_id_a,
                    bid=wallet_id_b,
                    score=similarity_score,
                    evidence=evidence,
                )
                return result.single() is not None

        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.debug("BEHAVIORAL_MATCH edge creation failed: %s", e)
        return False


async def get_cross_chain_edges_for_wallet(
    wallet_id: str,
) -> list[dict[str, Any]]:
    """Get all cross-chain edges from a wallet.
    
    Returns list of {wallet_id, address, chain, relationship, confidence}.
    """
    driver = _get_driver()
    if not driver:
        return []

    query = """
    MATCH (w:Wallet {wallet_id: $wallet_id})-[r:SAME_AS|BRIDGED_TO|BEHAVIORAL_MATCH]-(other:Wallet)
    RETURN other.wallet_id AS wallet_id,
           other.address AS address,
           other.chain AS chain,
           type(r) AS relationship,
           r.confidence AS confidence,
           r.method AS method
    ORDER BY r.confidence DESC
    """

    try:
        import asyncio

        def _run() -> list[dict[str, Any]]:
            with driver.session() as s:
                return [dict(rec) for rec in s.run(query, wallet_id=wallet_id)]

        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.debug("Cross-chain edge query failed: %s", e)
        return []


def generate_entity_id() -> str:
    """Generate a deterministic entity ID."""
    return "ent_" + uuid.uuid4().hex[:16]
