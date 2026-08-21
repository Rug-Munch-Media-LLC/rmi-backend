"""DataBus Router sub-module. Convenience endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.logging import get_logger
from app.databus.core import databus

logger = get_logger("databus.router.convenience")
router = APIRouter(tags=["databus"])

# Hard per-call timeout for wallet lookups — never let a request hang indefinitely.
_WALLET_TIMEOUT = 10.0

# Provider source names for each wallet data type (used when returning an honest
# "no_data" payload so consumers can see which upstreams were present in the chain).
_WALLET_SOURCES: dict[str, list[str]] = {
    "wallet_tokens": ["alchemy_balances", "moralis_tokens"],
    "wallet_pnl": ["moralis_net_worth", "arkham_portfolio"],
}


def _wallet_no_data(address: str, data_type: str) -> dict[str, Any]:
    """Honest-error contract: upstream chain returned nothing.

    Returns a 200 payload with an empty shape instead of a bare 502 so consumers that
    treat non-200 as "no data" (bot portfolio/security/trace helpers fail open) get a
    well-typed empty result with the reason and the sources that were in the chain.
    """
    return {
        "data": None,
        "reason": "no_data",
        "sources_tried": _WALLET_SOURCES.get(data_type, []),
        "address": address,
        "data_type": data_type,
    }


async def _wallet_fetch(data_type: str, address: str) -> Any:
    """Fetch a wallet data type with a hard timeout.

    Returns the provider result on success, ``None`` when upstream returned no data,
    and raises HTTPException(503) on real upstream failure / timeout. Only the 200/503
    contract is decided here — never a bare 502.

    Structured failure logging is emitted with the address and data type so the failure
    is attributable without leaking the address into noisy unstructured logs.
    """
    try:
        return await asyncio.wait_for(databus.fetch(data_type, address=address), timeout=_WALLET_TIMEOUT)
    except TimeoutError as exc:
        logger.warning(
            "databus_wallet_timeout",
            data_type=data_type,
            address=address,
            timeout=_WALLET_TIMEOUT,
            error=str(exc),
        )
        raise HTTPException(
            503, detail=f"wallet {data_type} upstream timed out for {address} after {_WALLET_TIMEOUT}s"
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # gateway must not leak a 500 for a lookup failure
        logger.warning(
            "databus_wallet_upstream_failure",
            data_type=data_type,
            address=address,
            error=str(exc),
        )
        raise HTTPException(503, detail=f"wallet {data_type} upstream unavailable for {address}: {exc}") from exc


@router.get("/arkham/entity/{address}")
async def arkham_entity(address: str, request: Request) -> Any:
    """Resolve a wallet address to an Arkham entity. Admin key required."""
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch("entity_intel", address=address, admin_key=admin_key)
    if result is None:
        raise HTTPException(502, f"Could not resolve entity for {address}")
    return result


@router.get("/arkham/labels")
async def arkham_labels(request: Request) -> Any:
    """Look up labels for addresses. Admin key required for Arkham, public for local."""
    admin_key = request.headers.get("X-Admin-Key", "")
    address = request.query_params.get("address", "")
    result = await databus.fetch("wallet_labels", address=address, admin_key=admin_key)
    if result is None:
        raise HTTPException(502, "No labels found")
    return result


@router.get("/arkham/portfolio/{address}")
async def arkham_portfolio(address: str, request: Request) -> Any:
    """Get portfolio for an entity. Admin key required."""
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch("arkham_portfolio", address=address, admin_key=admin_key)
    if result is None:
        raise HTTPException(502, f"Could not get portfolio for {address}")
    return result


@router.get("/token/{mint}")
async def token_detail(mint: str, request: Request) -> Any:
    """Rich token detail (metadata + price + security)."""
    result = await databus.fetch("token_detail", mint=mint)
    if result is None:
        raise HTTPException(502, f"No data for token {mint}")
    return result


@router.get("/wallet/{address}/tokens")
async def wallet_tokens(address: str, request: Request) -> Any:
    """Wallet token holdings."""
    result = await _wallet_fetch("wallet_tokens", address)
    if result is None:
        return _wallet_no_data(address, "wallet_tokens")
    return result


@router.get("/wallet/{address}/pnl")
async def wallet_pnl(address: str, request: Request) -> Any:
    """Wallet profit/loss."""
    result = await _wallet_fetch("wallet_pnl", address)
    if result is None:
        return _wallet_no_data(address, "wallet_pnl")
    return result


@router.get("/rugmap/{address}")
async def rugmap(address: str, request: Request, chain: str = "solana", depth: int = 2) -> Any:
    """Interactive holder RugMap."""
    result = await databus.fetch("rugmap", address=address, chain=chain, depth=depth)
    if result is None:
        raise HTTPException(502, f"No RugMap data for {address}")
    return result


@router.get("/rugmaps/{address}")
async def rugmaps_analysis(address: str, request: Request, chain: str = "solana") -> Any:
    """RugMaps AI analysis + similar scam search."""
    result = await databus.fetch("rugmaps_analysis", address=address, chain=chain)
    if result is None:
        raise HTTPException(502, f"No RugMaps analysis for {address}")
    return result


@router.get("/threat/{address}")
async def threat_check(address: str, request: Request, chain_id: str = "1") -> Any:
    """Threat intelligence check (CryptoScamDB + GoPlus + Januus)."""
    result = await databus.fetch("threat_check", address=address, chain_id=chain_id)
    if result is None:
        raise HTTPException(502, f"No threat data for {address}")
    return result


@router.get("/contract-scan/{address}")
async def contract_scan(address: str, request: Request, chain: str = "base") -> Any:
    """Smart contract deep scan (Slither + Mythril)."""
    result = await databus.fetch("contract_scan", address=address, chain=chain)
    if result is None:
        raise HTTPException(502, f"No contract scan data for {address}")
    return result


@router.get("/sentinel-deep/{address}")
async def sentinel_deep(address: str, request: Request, chain: str = "solana") -> Any:
    """Deep SENTINEL scan + threat feeds + contract analysis."""
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch("sentinel_deep", address=address, chain=chain, admin_key=admin_key)
    if result is None:
        raise HTTPException(502, f"No deep scan data for {address}")
    return result


@router.get("/prediction-markets")
async def prediction_markets(request: Request, query: str = "", category: str = "") -> Any:
    """Prediction market intel (Polymarket + Kalshi)."""
    result = await databus.fetch("prediction_markets", query=query, category=category)
    if result is None:
        raise HTTPException(502, "No prediction market data available")
    return result


@router.get("/prediction-signals")
async def prediction_signals(request: Request) -> Any:
    """Auto-detected trading signals from prediction markets."""
    result = await databus.fetch("prediction_signals")
    if result is None:
        raise HTTPException(502, "No prediction signals available")
    return result


@router.get("/market/prediction-markets")
async def market_prediction_markets() -> Any:
    """Prediction markets from Polymarket. Free, no key needed."""
    return await databus.fetch("prediction_markets")
