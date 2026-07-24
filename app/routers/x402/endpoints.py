"""
x402 DataBus Tools - API endpoints.
"""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.errors import UpstreamError, ValidationError
from app.core.logging import get_logger
from app.routers.x402.pricing import X402_TOOL_PRICING, check_trial, TRIAL_WINDOW_SECONDS
from app.routers.x402.payment import verify_x402_payment, _clean_response
from app.routers.x402.payment import (
    AddressRequest,
    MintRequest,
    QueryRequest,
    AddressDepthRequest,
    GenericDataRequest,
)

logger = get_logger("x402_databus_endpoints")

router = APIRouter(prefix="/api/v1/x402-databus", tags=["x402-databus-tools"])

# ── x402 Catalog & Discovery ─────────────────────────────────────


@router.get("/catalog")
async def x402_catalog(request: Request) -> dict[str, str]:
    """Full catalog of available x402 DataBus tools with pricing."""
    catalog = []
    for tool_id, info in sorted(
        X402_TOOL_PRICING.items(), key=lambda x: (x[1]["category"], x[1]["price_usd"])
    ):
        allowed_free = access_controller.get_x402_allowed_types("free")
        allowed_basic = access_controller.get_x402_allowed_types("basic")
        allowed_premium = access_controller.get_x402_allowed_types("premium")

        catalog.append(
            {
                "tool_id": tool_id,
                "name": info["description"].split("-")[0].strip()
                if "-" in info["description"]
                else info["description"][:40],
                "price_usd": info["price_usd"],
                "price_atoms": info["price_atoms"],
                "category": info["category"],
                "trial_free": info["trial_free"],
                "description": info["description"],
                "data_type": tool_id,
                "x402_tier_access": {
                    "free": tool_id in allowed_free,
                    "basic": tool_id in allowed_basic,
                    "premium": tool_id in allowed_premium,
                    "enterprise": True,
                },
            }
        )

    return {
        "server": "RMI DataBus x402 Gateway",
        "version": "2.0",
        "total_tools": len(catalog),
        "categories": {
            cat: sum(1 for t in X402_TOOL_PRICING.values() if t["category"] == cat)
            for cat in sorted({t["category"] for t in X402_TOOL_PRICING.values()})
        },
        "tiers": {
            "basic": {
                "count": sum(1 for t in X402_TOOL_PRICING.values() if t["category"] == "basic"),
                "price_range": "$0.01-$0.02",
                "trial_free": "3-5 calls",
            },
            "premium": {
                "count": sum(1 for t in X402_TOOL_PRICING.values() if t["category"] == "premium"),
                "price_range": "$0.05-$0.10",
                "trial_free": "1-2 calls",
            },
            "elite": {
                "count": sum(1 for t in X402_TOOL_PRICING.values() if t["category"] == "elite"),
                "price_range": "$0.15-$0.25",
                "trial_free": "0 calls",
            },
        },
        "tools": catalog,
        "accepted_networks": [
            "base",
            "solana",
            "ethereum",
            "bsc",
            "arbitrum",
            "optimism",
            "polygon",
            "avalanche",
            "fantom",
            "gnosis",
        ],
        "accepted_tokens": {"USDC": "All networks", "EUR": "SEPA"},
    }


@router.get("/access-matrix")
async def x402_access_matrix() -> dict:
    """Show which data types each x402 tier can access."""

    return {
        "free": access_controller.get_x402_allowed_types("free"),
        "basic": access_controller.get_x402_allowed_types("basic"),
        "premium": access_controller.get_x402_allowed_types("premium"),
        "enterprise": "all (full access)",
    }


@router.get("/trials/{identifier}")
async def x402_trial_status(identifier: str) -> dict:
    """Check remaining free trials for a wallet or fingerprint.

    For humans: pass wallet address → see full allotment.
    For bots: pass fingerprint → see limited (1) trial count.
    """
    trials = {}
    for tool_id, info in X402_TOOL_PRICING.items():
        max_trials = info.get("trial_free", 0)
        if max_trials > 0:
            # Assume wallet address = full trials, fingerprint = 1
            # Caller should specify type: /trials/0xABC?type=wallet or ?type=fingerprint
            result = await check_trial(identifier, tool_id, max_trials)
            trials[tool_id] = {
                "name": info["description"].split("-")[0].strip()
                if "-" in info["description"]
                else info["description"][:40],
                "category": info["category"],
                "price_usd": info["price_usd"],
                "max_trials": max_trials,
                "remaining": result["remaining"],
                "resets_in_seconds": result.get("resets_in_seconds", TRIAL_WINDOW_SECONDS),
            }
    return {"identifier": identifier, "trials": trials}


# ── BASIC TIER ENDPOINTS ($0.01-$0.02) ───────────────────────────


@router.post("/token-price")
async def x402_token_price(req: MintRequest, request: Request) -> Any:
    """Token price from multi-source consensus. $0.01/call."""
    payment = await verify_x402_payment(request, "token_price", 0.01)
    result = await databus.fetch(
        "token_price",
        mint=req.mint,
        consumer_type="x402_paid",
        x402_tier="basic" if not payment.get("trial") else "free",
        **payment,
    )
    return _clean_response(result) or {
        "error": "no_data",
        "message": "Price unavailable for this token",
    }


@router.post("/token-detail")
async def x402_token_detail(req: MintRequest, request: Request) -> Any:
    """Rich token intelligence report. $0.02/call."""
    payment = await verify_x402_payment(request, "token_detail", 0.02)
    result = await databus.fetch(
        "token_detail",
        mint=req.mint,
        chain=req.chain,
        consumer_type="x402_paid",
        x402_tier="basic" if not payment.get("trial") else "free",
        **payment,
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/trending")
async def x402_trending(req: QueryRequest, request: Request) -> Any:
    """Trending tokens feed. $0.01/call."""
    await verify_x402_payment(request, "trending", 0.01)
    result = await databus.fetch("trending", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/market-overview")
async def x402_market_overview(request: Request) -> Any:
    """Market landscape overview. $0.02/call."""
    await verify_x402_payment(request, "market_overview", 0.02)
    result = await databus.fetch("market_overview", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/market-movers")
async def x402_market_movers(request: Request) -> Any:
    """Top market movers. $0.01/call."""
    await verify_x402_payment(request, "market_movers", 0.01)
    result = await databus.fetch("market_movers", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/tvl")
async def x402_tvl(req: QueryRequest, request: Request) -> Any:
    """DeFi TVL tracker. $0.01/call."""
    await verify_x402_payment(request, "tvl", 0.01)
    result = await databus.fetch(
        "tvl",
        chain=req.chain if req.chain != "solana" else "",
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/news")
async def x402_news(req: QueryRequest, request: Request) -> Any:
    """Crypto news feed. $0.01/call."""
    await verify_x402_payment(request, "news", 0.01)
    result = await databus.fetch(
        "news", category=req.category, consumer_type="x402_paid", x402_tier="basic"
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/social-feed")
async def x402_social_feed(request: Request) -> Any:
    """Social intelligence feed. $0.01/call."""
    await verify_x402_payment(request, "social_feed", 0.01)
    result = await databus.fetch("social_feed", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/dex-data")
async def x402_dex_data(req: MintRequest, request: Request) -> Any:
    """DEX liquidity data. $0.01/call."""
    await verify_x402_payment(request, "dex_data", 0.01)
    result = await databus.fetch(
        "dex_data", mint=req.mint, consumer_type="x402_paid", x402_tier="basic"
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/defi-protocols")
async def x402_defi_protocols(request: Request) -> Any:
    """DeFi protocol tracker. $0.01/call."""
    await verify_x402_payment(request, "defi_protocols", 0.01)
    result = await databus.fetch("defi_protocols", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


@router.post("/prediction-markets")
async def x402_prediction_markets(req: QueryRequest, request: Request) -> Any:
    """Prediction market intel. $0.02/call."""
    await verify_x402_payment(request, "prediction_markets", 0.02)
    result = await databus.fetch(
        "prediction_markets",
        query=req.query,
        category=req.category,
        consumer_type="x402_paid",
        x402_tier="basic",
    )
    return _clean_response(result) or {"error": "no_data"}


@router.post("/prediction-signals")
async def x402_prediction_signals(request: Request) -> Any:
    """Automated trading signals. $0.02/call."""
    await verify_x402_payment(request, "prediction_signals", 0.02)
    result = await databus.fetch("prediction_signals", consumer_type="x402_paid", x402_tier="basic")
    return _clean_response(result) or {"error": "no_data"}


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


@router.post("/socialfi-resolve")
async def x402_socialfi_resolve(req: AddressRequest, request: Request) -> Any:
    """Social identity resolver. $0.01/call."""
    await verify_x402_payment(request, "socialfi_resolve", 0.01)
    result = await databus.fetch(
        "socialfi_resolve", address=req.address, consumer_type="x402_paid", x402_tier="basic"
    )
    return _clean_response(result) or {"error": "no_data"}


# ── PREMIUM TIER ENDPOINTS ($0.05-$0.10) ─────────────────────────


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


# ── ELITE TIER ENDPOINTS ($0.15-$0.40) ───────────────────────────


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
