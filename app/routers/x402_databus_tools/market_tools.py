"""Market data endpoints for x402 DataBus tools.

Catalog discovery, token pricing, trending, market overview,
DeFi TVL, news, social feeds, DEX data, and prediction markets.
All BASIC tier ($0.01-0.02/call).
"""

from typing import Any

from fastapi import Request  # runtime: FastAPI dependency injection

from app.core.logging import get_logger
from app.databus import databus
from app.routers.x402_databus_tools.auth import _clean_response, verify_x402_payment

# Runtime import: FastAPI resolves these body models at schema generation time.
from app.routers.x402_databus_tools.models import MintRequest, QueryRequest
from app.routers.x402_databus_tools.pricing import X402_TOOL_PRICING
from app.routers.x402_databus_tools.rate_limit import TRIAL_WINDOW_SECONDS, check_trial
from app.routers.x402_databus_tools.router import router

logger = get_logger("x402_databus_tools")

# ── x402 Catalog & Discovery ─────────────────────────────────────


@router.get("/catalog")
async def x402_catalog(request: Request) -> dict[str, Any]:
    """Full catalog of available x402 DataBus tools with pricing."""
    catalog = []
    for tool_id, info in sorted(X402_TOOL_PRICING.items(), key=lambda x: (x[1]["category"], x[1]["price_usd"])):
        from app.databus.access_control import access_controller

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
async def x402_access_matrix() -> dict[str, Any]:
    """Show which data types each x402 tier can access."""
    from app.databus.access_control import access_controller

    return {
        "free": access_controller.get_x402_allowed_types("free"),
        "basic": access_controller.get_x402_allowed_types("basic"),
        "premium": access_controller.get_x402_allowed_types("premium"),
        "enterprise": "all (full access)",
    }


@router.get("/trials/{identifier}")
async def x402_trial_status(identifier: str) -> dict[str, Any]:
    """Check remaining free trials for a wallet or fingerprint."""
    trials = {}
    for tool_id, info in X402_TOOL_PRICING.items():
        max_trials = info.get("trial_free", 0)
        if max_trials > 0:
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
    result = await databus.fetch("news", category=req.category, consumer_type="x402_paid", x402_tier="basic")
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
    result = await databus.fetch("dex_data", mint=req.mint, consumer_type="x402_paid", x402_tier="basic")
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
