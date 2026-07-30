"""V1 cross-chain endpoints — /api/v1/cross-chain/*.

Cross-chain wallet tracking, deployer matching, and fund flow tracing.
Powered by app.cross_chain_tracking and app.cross_chain_correlator.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.logging import get_logger
from app.cross_chain import get_cross_chain_profile, get_multi_chain_deployers
from app.cross_chain_tracking.deployer_matcher import find_and_persist_multi_chain_deployers

logger = get_logger(__name__)
router = APIRouter(prefix="/cross-chain", tags=["cross-chain-v1"])


@router.get("/{address}")
async def cross_chain_profile(
    address: str,
    chain: str = Query(default="ethereum", description="Chain to resolve from"),
) -> dict:
    """Get cross-chain profile for a wallet address.

    Resolves the wallet across all known chains using:
    - Same-deployer matching (chain-indexer ClickHouse)
    - EVM address portability
    - Bridge mappings
    - Known entity labels

    Returns linked addresses on other chains with confidence scores.
    """
    try:
        profile = await get_cross_chain_profile(address, chain)
        return profile
    except Exception as e:
        logger.warning("Cross-chain profile failed for %s: %s", address[:12], e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/deployers/discover")
async def discover_multi_chain_deployers(
    min_chains: int = Query(default=2, ge=2, le=10),
    chain_filter: str = Query(default="", description="Comma-separated chain names"),
    limit: int = Query(default=500, ge=1, le=10000),
) -> dict[str, Any]:
    """Discover deployers active on multiple chains.

    Queries chain-indexer ClickHouse to find wallets that deployed tokens
    on 2+ chains — strong evidence they're the same entity.

    Returns multi-chain deployers with chain activity summaries.
    """
    chains = [c.strip() for c in chain_filter.split(",") if c.strip()] if chain_filter else None
    deployers = await get_multi_chain_deployers(
        min_chains=min_chains,
        chain_filter=chains,
        limit=limit,
    )
    return {
        "count": len(deployers),
        "min_chains": min_chains,
        "deployers": deployers,
    }


@router.post("/persist")
async def persist_cross_chain_entities(
    min_chains: int = Query(default=2, ge=2, le=10),
    chain_filter: str = Query(default="", description="Comma-separated chain names"),
    limit: int = Query(default=200, ge=1, le=5000),
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    """Run batch cross-chain entity persistence.

    Finds multi-chain deployers and creates:
    - Neo4j Entity nodes
    - Neo4j SAME_AS edges linking same-entity wallets across chains
    - ClickHouse wallet_entity links

    Use dry_run=True to preview without writing.
    """
    chains = [c.strip() for c in chain_filter.split(",") if c.strip()] if chain_filter else None
    result = await find_and_persist_multi_chain_deployers(
        min_chains=min_chains,
        chain_filter=chains,
        limit=limit,
        dry_run=dry_run,
    )
    return result


@router.post("/trace", summary="Cross Chain Trace",
             description="Trace fund flows across multiple blockchains. "
                         "Follows transactions through bridges, mixers, and CEX deposits.")
async def cross_chain_trace(addresses: list[str]) -> dict:
    """Trace fund flows across chains.

    NOTE: Full fund-flow tracing is in development (W2-P4 enhancement).
    Currently returns deployer-based cross-chain links instead.
    """
    try:
        # For now, resolve each address to cross-chain profile
        profiles = {}
        for addr in addresses[:10]:
            profile = await get_cross_chain_profile(addr, "ethereum")
            profiles[addr] = profile
        return {
            "traced": len(addresses),
            "profiles": profiles,
            "note": "Cross-chain fund tracing — full implementation in progress",
        }
    except Exception as e:
        logger.warning("Cross-chain trace failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e
