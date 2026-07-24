"""Analysis, risk, and security endpoints for x402 DataBus tools.

RugMap, contract scanning, threat intelligence, entity resolution,
RAG knowledge search, and the universal /fetch endpoint.
Mixed BASIC, PREMIUM, and ELITE tier pricing.
"""

from __future__ import annotations
from typing import Any

from fastapi import Request

from app.core.errors import UpstreamError, ValidationError

from app.core.logging import get_logger
from app.databus import databus
from app.routers.x402_databus_tools.pricing import X402_TOOL_PRICING
from app.routers.x402_databus_tools.models import (
    AddressRequest,
    AddressDepthRequest,
    QueryRequest,
    GenericDataRequest,
)
from app.routers.x402_databus_tools.auth import _clean_response, verify_x402_payment
from app.routers.x402_databus_tools.router import router

logger = get_logger("x402_databus_tools")

# ── BASIC TIER — Analysis ────────────────────────────────────────

@router.post("/rugmap")
async def x402_rugmap(req: AddressDepthRequest, request: Request) -> Any:
    """Holder RugMap. $0.02/call."""
    await verify_x402_payment(request, "rugmap", 0.02)
    result = await databus.fetch(
        "rugmap",
        address=req.address,
        chain=req.chain,
        depth=req.depth,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/rugmaps-analysis")
async def x402_rugmaps_analysis(req: AddressRequest, request: Request) -> Any:
    """RugMaps AI holder analysis. $0.02/call."""
    await verify_x402_payment(request, "rugmaps_analysis", 0.02)
    result = await databus.fetch(
        "rugmaps_analysis",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/risk-scan")
async def x402_risk_scan(req: AddressRequest, request: Request) -> Any:
    """RugShield quick scan. $0.02/call."""
    await verify_x402_payment(request, "risk_scan", 0.02)
    result = await databus.fetch(
        "risk_scan",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/threat-check")
async def x402_threat_check(req: AddressRequest, request: Request) -> Any:
    """Threat intel check. $0.02/call."""
    await verify_x402_payment(request, "threat_check", 0.02)
    result = await databus.fetch(
        "threat_check",
        address=req.address,
        chain_id=req.chain,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}

# ── PREMIUM TIER — Deep Analysis ─────────────────────────────────

@router.post("/contract-scan")
async def x402_contract_scan(req: AddressRequest, request: Request) -> Any:
    """Deep contract auditor. $0.08/call."""
    payment = await verify_x402_payment(request, "contract_scan", 0.08)
    result = await databus.fetch(
        "contract_scan",
        address=req.address,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/sentinel-deep")
async def x402_sentinel_deep(req: AddressRequest, request: Request) -> Any:
    """SENTINEL deep scan. $0.10/call."""
    payment = await verify_x402_payment(request, "sentinel_deep", 0.10)
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch(
        "sentinel_deep",
        address=req.address,
        chain=req.chain,
        admin_key=admin_key,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/entity-intel")
async def x402_entity_intel(req: AddressRequest, request: Request) -> Any:
    """Entity intelligence lookup. $0.10/call."""
    payment = await verify_x402_payment(request, "entity_intel", 0.10)
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch(
        "entity_intel",
        address=req.address,
        admin_key=admin_key,
        consumer_type="x402_paid",
        x402_tier="premium",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}

@router.post("/rag-search")
async def x402_rag_search(req: QueryRequest, request: Request) -> Any:
    """Knowledge search engine. $0.05/call."""
    payment = await verify_x402_payment(request, "rag_search", 0.05)
    result = await databus.fetch(
        "rag_search", query=req.query, consumer_type="x402_paid", x402_tier="premium", **payment
    )
    return _clean_response(result) or {"error": "no_data"}

# ── UNIVERSAL DATA ENDPOINT ───────────────────────────────────────
# Single endpoint that accepts any data_type. Payment based on tier pricing.

@router.post("/fetch")
async def x402_universal_fetch(req: GenericDataRequest, request: Request) -> Any:
    """
    Universal DataBus fetch through x402 paywall.
    Accepts any data_type. Pricing is automatic based on X402_TOOL_PRICING.
    """
    data_type = req.data_type
    if data_type not in X402_TOOL_PRICING:
        raise ValidationError(
            f"Unknown data_type: {data_type}. Available: {', '.join(sorted(X402_TOOL_PRICING.keys()))}",
        )

    pricing = X402_TOOL_PRICING[data_type]
    payment = await verify_x402_payment(request, data_type, pricing["price_usd"])

    x402_tier = "enterprise" if pricing["category"] == "elite" else pricing["category"]
    if payment.get("trial"):
        x402_tier = "free" if pricing["category"] == "basic" else x402_tier

    result = await databus.fetch(
        data_type=data_type,
        address=req.address,
        mint=req.mint,
        chain=req.chain,
        query=req.query,
        category=req.category,
        chain_id=req.chain_id,
        admin_key=req.admin_key,
        force_fresh=req.force_fresh,
        consumer_type="x402_paid",
        x402_tier=x402_tier,
    )

    if result is None:
        raise UpstreamError(f"No data available for {data_type}")

    # Add payment metadata
    result["x402"] = {
        "tool_id": data_type,
        "price_usd": pricing["price_usd"],
        "price_atoms": pricing["price_atoms"],
        "category": pricing["category"],
        "trial": payment.get("trial", False),
        "description": pricing["description"],
    }

    return _clean_response(result)
