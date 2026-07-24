"""DataBus Router sub-module. Bitquery endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.logging import get_logger
from app.databus.core import databus

logger = get_logger("databus.router.bitquery")
router = APIRouter(tags=["databus"])

@router.get("/bitquery/health")
async def bitquery_health() -> Any:
    """Check Bitquery provider health and billing status."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    return await provider.health()

@router.get("/bitquery/token-price/{network}/{token_address}")
async def bitquery_token_price(network: str, token_address: str) -> dict[str, str]:
    """Get DEX token price from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_token_price(network, token_address)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "network": network, "token": token_address, "data": result}

@router.get("/bitquery/holders/{network}/{token_address}")
async def bitquery_holders(network: str, token_address: str, limit: int = Query(100, ge=1, le=500)) -> dict[str, str]:
    """Get token holder distribution from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_holder_distribution(network, token_address, limit)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "network": network, "token": token_address, "data": result}

@router.get("/bitquery/tx-trace/{network}/{tx_hash}")
async def bitquery_tx_trace(network: str, tx_hash: str) -> dict[str, str]:
    """Get full transaction trace from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_transaction_trace(network, tx_hash)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "network": network, "tx": tx_hash, "data": result}

@router.get("/bitquery/dex-volume/{network}")
async def bitquery_dex_volume(network: str, pool: str = Query(None), timeframe: str = Query("24h")) -> dict[str, str]:
    """Get DEX trading volume from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_dex_volume(network, pool, timeframe)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {
        "source": "bitquery",
        "network": network,
        "pool": pool,
        "timeframe": timeframe,
        "data": result,
    }

@router.get("/bitquery/balance/{network}/{address}")
async def bitquery_balance(network: str, address: str) -> dict[str, str]:
    """Get address token balances from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_address_balance(network, address)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "network": network, "address": address, "data": result}

@router.get("/bitquery/cross-chain/{address}")
async def bitquery_cross_chain(address: str, networks: str = Query("ethereum,bsc,solana")) -> dict[str, str]:
    """Track token transfers across chains from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    net_list = [n.strip() for n in networks.split(",")]
    result = await provider.get_cross_chain_transfers(address, net_list)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "address": address, "networks": net_list, "data": result}

@router.get("/bitquery/contract-events/{network}/{contract}")
async def bitquery_contract_events(
    network: str, contract: str, event: str = Query(None), limit: int = Query(50, ge=1, le=200)
) -> dict[str, str]:
    """Get smart contract events from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_smart_contract_events(network, contract, event, limit)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "network": network, "contract": contract, "data": result}

