#!/usr/bin/env python3
"""#10 - DataBus Public API Gateway Router. Free tier + x402 paid tier."""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.databus_gateway import (
    SUPPORTED_CHAINS,
    TIER_LIMITS,
    _check_rate,
    databus_cross_chain,
    databus_get,
    verify_x402_payment,
)

router = APIRouter(prefix="/api/v1/databus", tags=["databus-gateway"])


class ChainQuery(BaseModel):
    chain: str
    endpoint: str = "overview"
    params: dict | None = None


class CrossChainQuery(BaseModel):
    query: str
    chains: list[str] | None = None


@router.get("/chains")
async def list_chains():
    """List all supported chains with tier availability."""
    return {"chains": SUPPORTED_CHAINS, "total": len(SUPPORTED_CHAINS)}


@router.get("/{chain}/{endpoint:path}")
async def query_chain(chain: str, endpoint: str, x_rmi_key: str = Header(None), x_x402_sig: str = Header(None)):
    """Query a single DataBus chain. Free tier with rate limiting, paid tier via x402 header."""
    tier = "free"
    if x_x402_sig:
        valid = await verify_x402_payment(x_x402_sig, f"{chain}/{endpoint}")
        if valid:
            tier = "pro"
        else:
            raise HTTPException(402, "Invalid x402 payment signature")

    limits = TIER_LIMITS[tier]
    client_id = x_rmi_key or "anonymous"
    rate_key = f"databus:{client_id}:{tier}"

    if not _check_rate(rate_key, limits["requests_per_hour"]):
        raise HTTPException(429, f"Rate limit exceeded ({limits['requests_per_hour']}/hr for {tier} tier)")

    if chain not in SUPPORTED_CHAINS:
        raise HTTPException(404, f"Chain '{chain}' not supported. Use /api/v1/databus/chains for full list.")

    result = await databus_get(chain, endpoint, tier=tier)
    return {
        "chain": result.chain,
        "data": result.data,
        "source": result.source,
        "cached": result.cached,
        "latency_ms": result.latency_ms,
        "tier": tier,
    }


@router.post("/cross-chain")
async def cross_chain_query(query: CrossChainQuery):
    """Query multiple DataBus chains in parallel."""
    results = await databus_cross_chain(query.query, query.chains)
    return {
        "query": query.query,
        "chains_queried": list(results.keys()),
        "results": {c: {"data": r.data, "cached": r.cached} for c, r in results.items()},
    }
