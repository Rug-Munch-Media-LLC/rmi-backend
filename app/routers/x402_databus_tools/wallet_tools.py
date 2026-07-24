"""Wallet, entity, and enterprise endpoints for x402 DataBus tools.

Wallet balance, labels, profiling, PnL, smart money tracking,
funding source tracing, cross-chain correlation, cluster analysis,
and elite institutional endpoints (Arkham, Nansen, portfolio).
Mixed BASIC, PREMIUM, and ELITE tier pricing.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.logging import get_logger
from app.databus import databus
from app.routers.x402_databus_tools.auth import _clean_response, verify_x402_payment
from app.routers.x402_databus_tools.models import AddressRequest, QueryRequest
from app.routers.x402_databus_tools.router import router

logger = get_logger("x402_databus_tools")

# ── BASIC TIER — Wallet Tools ────────────────────────────────────

@router.post("/wallet-balance")
async def x402_wallet_balance(req: AddressRequest, request: Request) -> Any:
    """Wallet balance check. $0.01/call."""
    await verify_x402_payment(request, "wallet_balance", 0.01)
    result = await databus.fetch(
        "wallet_balance", address=req.address, consumer_type="x402_paid", x402_tier="basic"
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/wallet-labels")
async def x402_wallet_labels(req: AddressRequest, request: Request) -> Any:
    """Wallet entity lookup. $0.02/call."""
    await verify_x402_payment(request, "wallet_labels", 0.02)
    result = await databus.fetch(
        "wallet_labels",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/socialfi-resolve")
async def x402_socialfi_resolve(req: AddressRequest, request: Request) -> Any:
    """Social identity resolver. $0.01/call."""
    await verify_x402_payment(request, "socialfi_resolve", 0.01)
    result = await databus.fetch(
        "socialfi_resolve", address=req.address, consumer_type="x402_paid", x402_tier="basic"
    )
    return _clean_response(result) or {"error": "no_data"}

# ── PREMIUM TIER — Wallet Intelligence ───────────────────────────

@router.post("/wallet-profile")
async def x402_wallet_profile(req: AddressRequest, request: Request) -> Any:
    """Wallet intelligence profiler. $0.05/call."""
    payment = await verify_x402_payment(request, "wallet_profile", 0.05)
    result = await databus.fetch(
        "wallet_profile",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/smart-money")
async def x402_smart_money(req: QueryRequest, request: Request) -> Any:
    """Smart money tracker. $0.05/call."""
    payment = await verify_x402_payment(request, "smart_money", 0.05)
    result = await databus.fetch(
        "smart_money", consumer_type="x402_paid", x402_tier="premium", **payment
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/funding-source")
async def x402_funding_source(req: AddressRequest, request: Request) -> Any:
    """Funding source tracer. $0.08/call."""
    payment = await verify_x402_payment(request, "funding_source", 0.08)
    chain_id = {
        "solana": 0,
        "ethereum": 1,
        "bsc": 56,
        "base": 8453,
        "arbitrum": 42161,
        "optimism": 10,
        "polygon": 137,
    }.get(req.chain, 1)
    result = await databus.fetch(
        "funding_source",
        address=req.address,
        chain_id=chain_id,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/cross-chain")
async def x402_cross_chain(req: AddressRequest, request: Request) -> Any:
    """Cross-chain correlator. $0.08/call."""
    payment = await verify_x402_payment(request, "cross_chain", 0.08)
    result = await databus.fetch(
        "cross_chain",
        address=req.address,
        chains=req.chain,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/wallet-cluster")
async def x402_wallet_cluster(req: AddressRequest, request: Request) -> Any:
    """Syndicate network mapper. $0.08/call."""
    payment = await verify_x402_payment(request, "wallet_cluster", 0.08)
    result = await databus.fetch(
        "wallet_cluster",
        address=req.address,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/bundle-detect")
async def x402_bundle_detect(req: AddressRequest, request: Request) -> Any:
    """Bot activity detector. $0.08/call."""
    payment = await verify_x402_payment(request, "bundle_detect", 0.08)
    result = await databus.fetch(
        "bundle_detect",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/wallet-tokens")
async def x402_wallet_tokens(req: AddressRequest, request: Request) -> Any:
    """Wallet token holdings. $0.05/call."""
    payment = await verify_x402_payment(request, "wallet_tokens", 0.05)
    result = await databus.fetch(
        "wallet_tokens",
        address=req.address,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/wallet-pnl")
async def x402_wallet_pnl(req: AddressRequest, request: Request) -> Any:
    """Wallet PnL tracker. $0.05/call."""
    payment = await verify_x402_payment(request, "wallet_pnl", 0.05)
    result = await databus.fetch(
        "wallet_pnl", address=req.address, consumer_type="x402_paid", x402_tier="premium", **payment
    )
    return _clean_response(result) or {"error": "no_data"}

# ── ELITE TIER — Institutional Forensics ($0.15-$0.40) ───────────

@router.post("/arkham-portfolio")
async def x402_arkham_portfolio(req: AddressRequest, request: Request) -> Any:
    """Arkham portfolio intel. $0.25/call. Requires admin key."""
    payment = await verify_x402_payment(request, "arkham_portfolio", 0.25)
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch(
        "arkham_portfolio",
        address=req.address,
        admin_key=admin_key,
        consumer_type="x402_paid",
        x402_tier="enterprise",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/arkham-transfers")
async def x402_arkham_transfers(req: AddressRequest, request: Request) -> Any:
    """Arkham transfer tracer. $0.20/call."""
    payment = await verify_x402_payment(request, "arkham_transfers", 0.20)
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch(
        "arkham_transfers",
        address=req.address,
        chain=req.chain,
        admin_key=admin_key,
        consumer_type="x402_paid",
        x402_tier="enterprise",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/nansen-labels")
async def x402_nansen_labels(req: AddressRequest, request: Request) -> Any:
    """Nansen label intelligence. $0.15/call."""
    payment = await verify_x402_payment(request, "nansen_labels", 0.15)
    result = await databus.fetch(
        "nansen_labels",
        address=req.address,
        consumer_type="x402_paid",
        x402_tier="enterprise",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/portfolio")
async def x402_portfolio(req: AddressRequest, request: Request) -> Any:
    """Multi-wallet portfolio tracker. $0.15/call."""
    payment = await verify_x402_payment(request, "portfolio", 0.15)
    result = await databus.fetch(
        "portfolio",
        wallet=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="enterprise",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}
