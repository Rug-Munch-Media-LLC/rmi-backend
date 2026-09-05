"""Fund flow / transfer routes: /fund-flows."""

from __future__ import annotations

from typing import Any

from fastapi import Query

from app.core.errors import ValidationError
from app.routers.rugmaps_router._helpers import sub_router

router = sub_router()


@router.get("/fund-flows")
async def rugmaps_fund_flows(
    token: str = Query(..., description="Token address the map is centered on"),
    address: str = Query(..., description="Wallet address to trace"),
    chain: str = Query("solana"),
    hops: int = Query(1, ge=1, le=2),
) -> dict[str, Any]:
    """Fund-flow arrows for a clicked wallet node.

    Returns inbound (funds received by the wallet) and outbound (funds sent)
    transfers from our ClickHouse wallet_flow engine, plus a cross-chain
    profile from the cross-chain aggregator.
    """
    if not address.strip():
        raise ValidationError("address is required")

    from app.rugmaps_wallet import lookup_fund_flows

    return await lookup_fund_flows(token=token, address=address, chain=chain, hops=hops)
