"""
Deployer Matcher — Same-Deployer Cross-Chain Detection
========================================================
Identifies deployers active on 2+ chains by querying chain-indexer's
ClickHouse deployer_events table.

Pipeline:
  1. Query ClickHouse for deployers with activity on N+ chains
  2. For each multi-chain deployer, create Entity + SAME_AS Neo4j edges
  3. Record entity links in wallet_memory's ClickHouse wallet_entities
  4. Return structured CrossChainProfile

This is the quickest win: same EVM address deploying on Ethereum + BSC +
Polygon + Arbitrum + Base is clearly the same entity. For Solana/Tron,
we need other signals (the address format is different).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from app.core.logging import get_logger

from .clickhouse_reader import (
    fetch_deployments_for_deployer,
    fetch_multi_chain_deployers,
)
from .models import (
    CrossChainMatch,
    CrossChainProfile,
    DeployerEvent,
    MultiChainDeployer,
)

logger = get_logger(__name__)

# EVM chains (address-portable) — same address format across all
EVM_CHAINS = {
    "ethereum", "bsc", "polygon", "arbitrum", "base",
    "optimism", "avalanche", "fantom", "gnosis", "linea",
    "celo", "scroll", "zksync", "blast", "mantle",
}

# Non-EVM chains (different address format, need different signals)
NON_EVM_CHAINS = {"solana", "tron", "bitcoin"}

# Confidence levels for different match types
CONFIDENCE_SAME_EVM_DEPLOYER = 0.95  # Same address deploying on multiple EVM chains
CONFIDENCE_SAME_DEPLOYER_MIXED = 0.80  # Same address deploying on EVM + non-EVM (rare)
CONFIDENCE_SAME_DEPLOYER_SOLANA = 0.70  # Solana deployer matched via shared deploy pattern


class DeployerMatcher:
    """Matches deployers across chains using chain-indexer data."""

    def __init__(self) -> None:
        self._wallet_storage: Any = None

    @property
    def _storage(self) -> Any:
        if self._wallet_storage is None:
            from app.wallet_memory.storage import get_storage
            self._wallet_storage = get_storage()
        return self._wallet_storage

    async def find_multi_chain_deployers(
        self,
        min_chains: int = 2,
        chain_filter: list[str] | None = None,
        limit: int = 5000,
    ) -> list[MultiChainDeployer]:
        """Find deployers active on N+ chains.

        Args:
            min_chains: Minimum chains (default 2)
            chain_filter: Restrict to specific chains
            limit: Max results

        Returns:
            List of MultiChainDeployer with chain activity summary
        """
        raw = await fetch_multi_chain_deployers(
            min_chains=min_chains,
            chain_filter=chain_filter,
            limit=limit,
        )

        results: list[MultiChainDeployer] = []
        for r in raw:
            chains = r.get("chains", [])
            chain_count = r.get("chain_count", len(chains))
            total = r.get("total_deployments", 0)

            # Determine confidence based on chain types
            evm_count = sum(1 for c in chains if c in EVM_CHAINS)
            non_evm_count = sum(1 for c in chains if c in NON_EVM_CHAINS)

            if non_evm_count == 0:
                # All EVM chains — same address = very high confidence
                confidence = CONFIDENCE_SAME_EVM_DEPLOYER
                evidence = f"Same EVM deployer on {chain_count} chains: {', '.join(chains)}"
            elif evm_count > 0 and non_evm_count > 0:
                # Mixed — need to verify via different signals
                confidence = CONFIDENCE_SAME_DEPLOYER_MIXED
                evidence = (
                    f"Deployer active on EVM ({', '.join(c for c in chains if c in EVM_CHAINS)}) "
                    f"and non-EVM ({', '.join(c for c in chains if c in NON_EVM_CHAINS)}) chains"
                )
            else:
                # Non-EVM only (Solana, Tron) — address can't be same, different signal
                confidence = 0.60
                evidence = f"Same-name deployer on non-EVM chains: {', '.join(chains)}"

            # Fetch per-chain deployment counts
            deployments_by_chain: dict[str, int] = {}
            try:
                events = await fetch_deployments_for_deployer(
                    r["deployer"],
                    chain_filter=chain_filter,
                    limit=1000,
                )
                for ev in events:
                    deployments_by_chain[ev.chain] = deployments_by_chain.get(ev.chain, 0) + 1
            except Exception:
                # Fallback: even distribution estimate
                per_chain = total // max(chain_count, 1)
                for c in chains:
                    deployments_by_chain[c] = per_chain

            results.append(MultiChainDeployer(
                deployer_address=r["deployer"],
                chains=chains,
                chain_count=chain_count,
                total_deployments=total,
                deployments_by_chain=deployments_by_chain,
                first_seen=self._parse_ts(r.get("first_seen")),
                last_seen=self._parse_ts(r.get("last_seen")),
                confidence=confidence,
                evidence=evidence,
            ))

        return results

    async def resolve_deployer(
        self,
        deployer: str,
        chain: str = "ethereum",
    ) -> CrossChainProfile:
        """Resolve a single deployer address to its full cross-chain profile.

        Queries chain-indexer for all deployments across chains, then
        builds a CrossChainProfile with all known addresses.
        """
        events = await fetch_deployments_for_deployer(deployer, limit=500)

        if not events:
            addr = deployer.lower()
            return CrossChainProfile(
                entity_id=self._entity_id(deployer, chain),
                primary_address=addr,
                primary_chain=chain,
                addresses=[CrossChainMatch(
                    address=addr,
                    chain=chain,
                    confidence=1.0,
                    method="same_deployer",
                    evidence="Direct lookup",
                )],
                total_addresses=1,
                total_chains=1,
                chains=[chain],
            )

        # Group by chain
        chain_map: dict[str, list[DeployerEvent]] = {}
        for ev in events:
            chain_map.setdefault(ev.chain, []).append(ev)

        # Build address list
        addresses: list[CrossChainMatch] = []
        for ev_chain, ev_list in chain_map.items():
            is_evm = ev_chain in EVM_CHAINS
            conf = 0.95 if is_evm else 0.75
            addresses.append(CrossChainMatch(
                address=deployer.lower(),
                chain=ev_chain,
                confidence=conf,
                method="same_deployer",
                evidence=f"Deployed {len(ev_list)} token(s) on {ev_chain}",
            ))

        # Primary chain is the one with most deployments
        primary_chain = max(chain_map, key=lambda c: len(chain_map[c]))

        first_seen = min(
            (ev.block_timestamp for ev in events if ev.block_timestamp),
            default="",
        )
        last_seen = max(
            (ev.block_timestamp for ev in events if ev.block_timestamp),
            default="",
        )

        return CrossChainProfile(
            entity_id=self._entity_id(deployer, primary_chain),
            primary_address=deployer.lower(),
            primary_chain=primary_chain,
            addresses=addresses,
            total_addresses=len(addresses),
            total_chains=len(chain_map),
            chains=list(chain_map.keys()),
            deployments=events,
            total_deployments=len(events),
            first_seen=first_seen,
            last_seen=last_seen,
        )

    async def persist_multi_chain_deployer(
        self,
        mcd: MultiChainDeployer,
    ) -> dict[str, Any]:
        """Persist a multi-chain deployer to Neo4j + wallet_memory.

        1. Create Entity node in Neo4j
        2. Create/update Wallet nodes for each chain
        3. Create SAME_AS edges between wallets
        4. Save entity links to ClickHouse wallet_entities

        Returns:
            {entity_id, wallets_created, edges_created}
        """
        from .neo4j_writer import (
            create_same_as_edge,
            ensure_schema,
            generate_entity_id,
            upsert_entity,
            upsert_wallet,
        )

        # Ensure Schema exists
        await ensure_schema()

        # Generate entity ID
        entity_id = generate_entity_id()
        chain_str = ", ".join(mcd.chains)
        label = f"Multi-chain deployer: {mcd.deployer_address[:10]}... on {chain_str}"

        # Create Entity node
        await upsert_entity(
            entity_id=entity_id,
            label=label,
            tags=["multi_chain_deployer", *[f"deployer:{c}" for c in mcd.chains]],
        )

        # Create Wallet nodes and collect wallet_ids
        wallet_ids: list[str] = []
        for chain in mcd.chains:
            wid = await upsert_wallet(
                address=mcd.deployer_address,
                chain=chain,
                entity_id=entity_id,
                is_deployer=True,
            )
            wallet_ids.append(wid)

        # Create SAME_AS edges between all pairs
        edges_created = 0
        for i in range(len(wallet_ids)):
            for j in range(i + 1, len(wallet_ids)):
                ok = await create_same_as_edge(
                    wallet_ids[i],
                    wallet_ids[j],
                    confidence=mcd.confidence,
                    method="same_deployer",
                )
                if ok:
                    edges_created += 1

        # Save to ClickHouse wallet_entities
        wallet_links_saved = 0
        storage = self._storage
        for chain in mcd.chains:
            ok = await storage.save_entity_link(
                entity_id=entity_id,
                wallet_address=mcd.deployer_address,
                chain_id=chain,
                heuristic_type="same_deployer",
                confidence=mcd.confidence,
                label=label,
                category="multi_chain_deployer",
            )
            if ok:
                wallet_links_saved += 1

        logger.info(
            "Persisted multi-chain deployer %s: %s chains, %d edges, %d links",
            mcd.deployer_address[:12],
            mcd.chain_count,
            edges_created,
            wallet_links_saved,
        )

        return {
            "entity_id": entity_id,
            "deployer": mcd.deployer_address,
            "chains": mcd.chains,
            "wallets_created": len(wallet_ids),
            "edges_created": edges_created,
            "wallet_links_saved": wallet_links_saved,
        }

    async def batch_persist(
        self,
        min_chains: int = 2,
        chain_filter: list[str] | None = None,
        limit: int = 500,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Find all multi-chain deployers and persist them.
        
        This is the main batch processing method.
        
        Args:
            min_chains: Minimum chains for multi-chain qualification
            chain_filter: Optional chain restriction
            limit: Max deployers to process
            dry_run: If True, only print what would be done
            
        Returns:
            Summary dict with counts
        """
        from datetime import UTC, datetime

        start = datetime.now(UTC)

        multi_chain = await self.find_multi_chain_deployers(
            min_chains=min_chains,
            chain_filter=chain_filter,
            limit=limit,
        )

        logger.info(
            "Found %d multi-chain deployers (min_chains=%d)",
            len(multi_chain),
            min_chains,
        )

        if not multi_chain:
            return {
                "total_deployers_scanned": 0,
                "multi_chain_deployers_found": 0,
                "entities_created": 0,
                "edges_created": 0,
                "wallet_links_created": 0,
                "duration_ms": (datetime.now(UTC) - start).total_seconds() * 1000,
                "dry_run": dry_run,
            }

        total_entities = 0
        total_edges = 0
        total_links = 0
        new_ids: list[str] = []
        errors: list[str] = []

        for mcd in multi_chain:
            if dry_run:
                logger.info(
                    "[DRY RUN] Would persist %s (chains=%s, conf=%.2f)",
                    mcd.deployer_address[:16],
                    mcd.chains,
                    mcd.confidence,
                )
                continue

            try:
                result = await self.persist_multi_chain_deployer(mcd)
                total_entities += 1
                total_edges += result["edges_created"]
                total_links += result["wallet_links_saved"]
                new_ids.append(result["entity_id"])
            except Exception as e:
                logger.error(
                    "Failed to persist deployer %s: %s",
                    mcd.deployer_address[:16],
                    e,
                )
                errors.append(f"{mcd.deployer_address[:16]}: {e}")

        duration = (datetime.now(UTC) - start).total_seconds() * 1000

        return {
            "total_deployers_scanned": len(multi_chain),
            "multi_chain_deployers_found": len(multi_chain),
            "entities_created": total_entities,
            "edges_created": total_edges,
            "wallet_links_created": total_links,
            "new_entity_ids": new_ids,
            "duration_ms": round(duration, 2),
            "dry_run": dry_run,
            "errors": errors,
        }

    @staticmethod
    def _entity_id(address: str, chain: str) -> str:
        """Deterministic entity ID from address + chain."""
        raw = f"{address.lower()}:{chain}:deployer"
        return "ent_" + hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _parse_ts(ts_str: str | None) -> datetime | None:
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None


# ── Quick helpers ─────────────────────────────────────────────────────────

async def find_and_persist_multi_chain_deployers(
    min_chains: int = 2,
    chain_filter: list[str] | None = None,
    limit: int = 500,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One-shot: find multi-chain deployers and persist to Neo4j + ClickHouse."""
    matcher = DeployerMatcher()
    return await matcher.batch_persist(
        min_chains=min_chains,
        chain_filter=chain_filter,
        limit=limit,
        dry_run=dry_run,
    )
