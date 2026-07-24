"""
Entity Resolver - Cross-chain entity linking.
================================================================
Links wallets across chains using:
  1. Bridge mappings (wallet A on chain X → wallet B on chain Y via bridge tx)
  2. Same address heuristic (EVM addresses are portable across EVM chains)
  3. Gas payer networks (one wallet paying for deploys across chains)
  4. Deposit-withdraw patterns (CEX flow tracing)

Produces entity_id (deterministic hash) for each resolved entity group.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.core.logging import get_logger

from .storage import WalletStorage, get_storage

logger = get_logger("wallet_memory.entity_resolver")

def _mark_degraded(store: str) -> None:
    try:
        from app.core.degradation import get_registry

        get_registry().mark_down(store)
    except (AttributeError, ImportError):
        pass

class EntityResolver:
    """
    Cross-chain entity linking. Resolves wallets to entities.
    """

    def __init__(self, storage: WalletStorage | None = None) -> None:
        self.storage = storage or get_storage()
        self._entity_cache: dict[str, str] = {}  # addr:chain -> entity_id

    async def resolve(self, address: str, chain: str) -> dict[str, Any]:
        """
        Resolve a wallet address to its cross-chain entity.

        Win #14: Neo4j degradation → if entity storage is down (ClickHouse/Neo4j),
        returns a degraded single-entity result instead of 500.

        Returns:
            {
                "entity_id": str,
                "addresses": [{"address": str, "chain": str, "confidence": float, "method": str}],
                "primary_chain": str,
                "total_chains": int,
            }
        """
        addr = address.lower().strip()
        entity_id = self._make_entity_id(addr, chain)

        # Already resolved in storage?
        try:
            stored_entity = await self.storage.get_entity_for_wallet(addr, chain)
            if stored_entity and stored_entity.get("entity_id"):
                entity_id = stored_entity["entity_id"]
        except Exception as e:
            logger.warning(f"Entity storage lookup failed (degraded): {e}")
            _mark_degraded("neo4j")

        addresses = [{"address": addr, "chain": chain, "confidence": 1.0, "method": "direct"}]

        # ── Method 1: Same EVM address heuristic ──────────────────
        # EVM addresses are portable: same private key works on all EVM chains
        evm_chains = [
            "ethereum",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "base",
            "avalanche",
            "fantom",
        ]

        from app.chain_registry import is_evm

        if is_evm(chain):
            for evm_chain in evm_chains:
                if evm_chain != chain:
                    # Same address = same entity on all EVM chains
                    addresses.append(
                        {
                            "address": addr,
                            "chain": evm_chain,
                            "confidence": 0.90,
                            "method": "evm_address_portability",
                        }
                    )

        # ── Method 2: Bridge mappings ────────────────────────────
        try:
            bridges = await self.storage.get_bridge_links(addr, chain)
            for bridge in bridges:
                dest_addr = bridge.get("dest_address", "").lower()
                dest_chain = bridge.get("dest_chain", "")
                if dest_addr and dest_chain:
                    addresses.append(
                        {
                            "address": dest_addr,
                            "chain": dest_chain,
                            "confidence": bridge.get("confidence", 0.8),
                            "method": f"bridge_{bridge.get('bridge_name', 'unknown')}",
                        }
                    )
        except Exception as e:
            logger.debug(f"Bridge resolution failed for {addr}: {e}")

        # ── Method 3: Check existing entity membership ───────────
        try:
            if stored_entity and stored_entity.get("entity_id"):
                members = await self.storage.get_entity_wallets(stored_entity["entity_id"])
                for m in members:
                    m_addr = m.get("wallet_address", "").lower()
                    m_chain = m.get("chain_id", "")
                    existing = {(a["address"], a["chain"]) for a in addresses}
                    if (m_addr, m_chain) not in existing:
                        addresses.append(
                            {
                                "address": m_addr,
                                "chain": m_chain,
                                "confidence": m.get("confidence_score", 0.5),
                                "method": "entity_membership",
                            }
                        )
        except Exception as e:
            logger.debug(f"Entity membership lookup failed for {addr}: {e}")

        # Persist new entity links
        for addr_entry in addresses:
            if addr_entry["method"] != "direct":  # Don't re-save the original
                try:
                    await self.storage.save_entity_link(
                        entity_id=entity_id,
                        wallet_address=addr_entry["address"],
                        chain_id=addr_entry["chain"],
                        heuristic_type=addr_entry["method"],
                        confidence=addr_entry["confidence"],
                    )
                except Exception as e:
                    logger.debug(f"Entity link persist failed: {e}")

        # Determine primary chain (most active chain for this entity)
        chain_counts: dict[str, int] = {}
        for a in addresses:
            chain_counts[a["chain"]] = chain_counts.get(a["chain"], 0) + 1
        primary_chain = max(chain_counts, key=chain_counts.get) if chain_counts else chain

        # Deduplicate
        seen = set()
        unique_addresses = []
        for a in addresses:
            key = (a["address"], a["chain"])
            if key not in seen:
                seen.add(key)
                unique_addresses.append(a)

        return {
            "entity_id": entity_id,
            "addresses": unique_addresses,
            "primary_chain": primary_chain,
            "total_chains": len({a["chain"] for a in unique_addresses}),
        }

    async def link_via_bridge(
        self,
        source_addr: str,
        source_chain: str,
        dest_addr: str,
        dest_chain: str,
        bridge_name: str,
        tx_hash: str,
        confidence: float = 1.0,
    ) -> str:
        """Explicitly link two addresses via a bridge transaction."""
        # Save the bridge mapping
        await self.storage.save_bridge_link(
            source_addr, source_chain, dest_addr, dest_chain, bridge_name, tx_hash, confidence
        )

        # Create/merge entity
        entity_id = self._make_entity_id(source_addr, source_chain)

        # Link both addresses
        await self.storage.save_entity_link(
            entity_id, source_addr, source_chain, "bridge_mapping", confidence
        )
        await self.storage.save_entity_link(
            entity_id, dest_addr, dest_chain, "bridge_mapping", confidence
        )

        # Check if dest_addr already has an entity and merge
        dest_entity = await self.storage.get_entity_for_wallet(dest_addr, dest_chain)
        if dest_entity and dest_entity.get("entity_id") and dest_entity["entity_id"] != entity_id:
            # Merge entities: both are the same operator
            dest_members = await self.storage.get_entity_wallets(dest_entity["entity_id"])
            for m in dest_members:
                await self.storage.save_entity_link(
                    entity_id,
                    m["wallet_address"],
                    m["chain_id"],
                    "entity_merge",
                    m.get("confidence_score", 0.5),
                )
            logger.info(f"Merged entity {dest_entity['entity_id']} into {entity_id}")

        return entity_id

    @staticmethod
    def _make_entity_id(address: str, chain: str) -> str:
        """Deterministic entity ID from address + chain."""
        raw = f"{address.lower()}:{chain}"
        return "ent_" + hashlib.sha256(raw.encode()).hexdigest()[:16]

# ── Global singleton ────────────────────────────────────────────

_resolver: EntityResolver | None = None

def get_entity_resolver() -> EntityResolver:
    global _resolver
    if _resolver is None:
        _resolver = EntityResolver()
    return _resolver
