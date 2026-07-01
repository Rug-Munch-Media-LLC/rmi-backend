"""
DataBus Router — Unified API Endpoints
========================================

Replaces the fractured caching_shield/router, cache_manager stats,
and all connector-specific endpoints with a single clean interface.

POST /api/v1/databus/fetch         — Fetch data (any type)
GET  /api/v1/databus/health        — System health + cache stats
GET  /api/v1/databus/capacity      — Credit report + recommendations
GET  /api/v1/databus/chains         — List all provider chains
POST /api/v1/databus/invalidate     — Clear cache (admin)
GET  /api/v1/databus/vault/status   — Key pool status (admin, no key values)
GET  /api/v1/databus/vault/reload   — Reload keys from vault (admin)
GET  /api/v1/databus/fetch/{type}   — GET convenience for simple queries
"""

import logging
import os

from fastapi import APIRouter, HTTPException, Query, Request

from app.databus.core import databus

logger = logging.getLogger("databus.router")

router = APIRouter(prefix="/api/v1/databus", tags=["databus"])


def _verify_admin(request: Request) -> bool:
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if not admin_key:
        return True
    provided = request.headers.get("X-Admin-Key", "")
    return provided == admin_key


@router.post("/fetch")
async def databus_fetch(request: Request):
    """
    Universal data fetch endpoint.
    Every response is packaged based on who's asking.
    No raw data leaks — consumers only get what they're authorized for.

    Body: {
        "data_type": "token_price",         # required
        "admin_key": "",                    # for admin data
        "force_fresh": false,              # skip cache
        "rag_index": false,                # index in RAG
        "consumer_type": "",                # public_web/authenticated/premium/admin/mcp_tool/x402_paid
        "tool_id": "",                      # MCP tool ID for scoping
        "x402_tier": "",                    # x402 pricing tier: free/basic/premium/enterprise
        "mint": "So111...",                 # type-specific params
        "address": "0x...",                 # type-specific params
        ...
    }
    """
    body = await request.json()
    data_type = body.pop("data_type", None)
    if not data_type:
        raise HTTPException(400, "data_type is required")

    admin_key = body.pop("admin_key", "")
    force_fresh = body.pop("force_fresh", False)
    rag_index = body.pop("rag_index", False)
    consumer_type = body.pop("consumer_type", "")
    tool_id = body.pop("tool_id", "")
    x402_tier = body.pop("x402_tier", "")

    result = await databus.fetch(
        data_type=data_type,
        admin_key=admin_key,
        force_fresh=force_fresh,
        rag_index=rag_index,
        consumer_type=consumer_type,
        tool_id=tool_id,
        x402_tier=x402_tier,
        request=request,
        **body,
    )
    if result is None:
        raise HTTPException(502, f"No data available for {data_type}")
    return result


@router.get("/fetch/{data_type}")
async def databus_fetch_get(
    data_type: str,
    request: Request,
    admin_key: str = Query(default=""),
    force_fresh: bool = Query(default=False),
):
    """GET convenience for simple queries. Params passed as query args."""
    # Collect all query params except the known ones
    kwargs = {}
    for key, value in request.query_params.items():
        if key not in ("admin_key", "force_fresh"):
            kwargs[key] = value

    result = await databus.fetch(data_type=data_type, admin_key=admin_key, force_fresh=force_fresh, **kwargs)
    if result is None:
        raise HTTPException(502, f"No data available for {data_type}")
    return result


@router.get("/health")
async def databus_health():
    """Full system health: cache, vault, chains, stats."""
    return await databus.health()


@router.get("/capacity")
async def databus_capacity():
    """Credit report and recommendations for free tier expansion."""
    if not databus._initialized:
        await databus.initialize()
    return databus.capacity_report()


@router.get("/chains")
async def databus_chains():
    """List all available data types and their provider fallback chains."""
    if not databus._initialized:
        await databus.initialize()
    return databus.list_chains()


@router.get("/access-matrix")
async def databus_access_matrix(request: Request):
    """View the full access control matrix. Admin key required."""
    if not _verify_admin(request):
        from app.databus.access_control import access_controller

        # Non-admin sees a limited view — only their tier's access
        return {"note": "Full matrix requires admin key. Your access depends on your consumer type."}
    from app.databus.access_control import access_controller

    return access_controller.list_access_matrix()


@router.get("/mcp-scope/{tool_id}")
async def databus_mcp_scope(tool_id: str):
    """Get the data types an MCP tool is authorized to access."""
    from app.databus.access_control import access_controller

    allowed = access_controller.get_mcp_allowed_types(tool_id)
    if not allowed:
        return {"tool_id": tool_id, "allowed_types": [], "note": "Unknown tool or no scope defined"}
    return {"tool_id": tool_id, "allowed_types": allowed}


@router.get("/x402-scope/{tier}")
async def databus_x402_scope(tier: str):
    """Get the data types an x402 pricing tier can access."""
    from app.databus.access_control import access_controller

    allowed = access_controller.get_x402_allowed_types(tier)
    return {"tier": tier, "allowed_types": allowed}


@router.post("/invalidate")
async def databus_invalidate(request: Request):
    """Clear cache. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    data_type = body.get("data_type")

    if data_type:
        await databus.invalidate(data_type, **{k: v for k, v in body.items() if k != "data_type"})
        return {"status": "invalidated", "data_type": data_type}
    else:
        await databus.invalidate_all()
        return {"status": "all_cache_cleared"}


@router.get("/vault/status")
async def databus_vault_status(request: Request):
    """Key pool status. NEVER exposes key values. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    if not databus.vault:
        await databus.initialize()
    return databus.vault.status()


@router.get("/vault/capacity")
async def databus_vault_capacity(request: Request):
    """Detailed capacity report with free tier recommendations. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    if not databus.vault:
        await databus.initialize()
    return databus.vault.capacity_report()


@router.post("/vault/reload")
async def databus_vault_reload(request: Request):
    """Reload keys from vault without restart. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    await databus.vault.reload()
    return {"status": "reloaded", "providers": len(databus.vault.pools)}


@router.post("/vault/add-key")
async def databus_vault_add_key(request: Request):
    """Hot-add an API key to a provider pool. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    body = await request.json()
    provider = body.get("provider")
    key_name = body.get("key_name")
    key_value = body.get("key_value")

    if not all([provider, key_name, key_value]):
        raise HTTPException(400, "provider, key_name, key_value required")

    await databus.vault.add_key(provider, key_name, key_value)
    return {"status": "added", "provider": provider, "key_name": key_name}


@router.post("/vault/reset-monthly")
async def databus_vault_reset_monthly(request: Request):
    """Reset monthly call counters. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")

    databus.vault.reset_monthly_counters()
    return {"status": "monthly_counters_reset"}


# ── Convenience endpoints for Arkham Intelligence ──


@router.get("/arkham/entity/{address}")
async def arkham_entity(address: str, request: Request):
    """Resolve a wallet address to an Arkham entity. Admin key required."""
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch("entity_intel", address=address, admin_key=admin_key)
    if result is None:
        raise HTTPException(502, f"Could not resolve entity for {address}")
    return result


@router.get("/arkham/labels")
async def arkham_labels(request: Request):
    """Look up labels for addresses. Admin key required for Arkham, public for local."""
    admin_key = request.headers.get("X-Admin-Key", "")
    address = request.query_params.get("address", "")
    result = await databus.fetch("wallet_labels", address=address, admin_key=admin_key)
    if result is None:
        raise HTTPException(502, "No labels found")
    return result


@router.get("/arkham/portfolio/{address}")
async def arkham_portfolio(address: str, request: Request):
    """Get portfolio for an entity. Admin key required."""
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch("arkham_portfolio", address=address, admin_key=admin_key)
    if result is None:
        raise HTTPException(502, f"Could not get portfolio for {address}")
    return result


# ── New convenience endpoints for expanded data types ──


@router.get("/token/{mint}")
async def token_detail(mint: str, request: Request):
    """Rich token detail (metadata + price + security)."""
    result = await databus.fetch("token_detail", mint=mint)
    if result is None:
        raise HTTPException(502, f"No data for token {mint}")
    return result


@router.get("/wallet/{address}/tokens")
async def wallet_tokens(address: str, request: Request):
    """Wallet token holdings."""
    result = await databus.fetch("wallet_tokens", address=address)
    if result is None:
        raise HTTPException(502, f"No token data for wallet {address}")
    return result


@router.get("/wallet/{address}/pnl")
async def wallet_pnl(address: str, request: Request):
    """Wallet profit/loss."""
    result = await databus.fetch("wallet_pnl", address=address)
    if result is None:
        raise HTTPException(502, f"No PnL data for wallet {address}")
    return result


@router.get("/bubble-map/{address}")
async def bubble_map(address: str, request: Request, chain: str = "solana", depth: int = 2):
    """Interactive holder bubble map."""
    result = await databus.fetch("bubble_map", address=address, chain=chain, depth=depth)
    if result is None:
        raise HTTPException(502, f"No bubble map data for {address}")
    return result


@router.get("/rugmaps/{address}")
async def rugmaps_analysis(address: str, request: Request, chain: str = "solana"):
    """RugMaps AI analysis + similar scam search."""
    result = await databus.fetch("rugmaps_analysis", address=address, chain=chain)
    if result is None:
        raise HTTPException(502, f"No RugMaps analysis for {address}")
    return result


@router.get("/threat/{address}")
async def threat_check(address: str, request: Request, chain_id: str = "1"):
    """Threat intelligence check (CryptoScamDB + GoPlus + Januus)."""
    result = await databus.fetch("threat_check", address=address, chain_id=chain_id)
    if result is None:
        raise HTTPException(502, f"No threat data for {address}")
    return result


@router.get("/contract-scan/{address}")
async def contract_scan(address: str, request: Request, chain: str = "base"):
    """Smart contract deep scan (Slither + Mythril)."""
    result = await databus.fetch("contract_scan", address=address, chain=chain)
    if result is None:
        raise HTTPException(502, f"No contract scan data for {address}")
    return result


@router.get("/sentinel-deep/{address}")
async def sentinel_deep(address: str, request: Request, chain: str = "solana"):
    """Deep SENTINEL scan + threat feeds + contract analysis."""
    admin_key = request.headers.get("X-Admin-Key", "")
    result = await databus.fetch("sentinel_deep", address=address, chain=chain, admin_key=admin_key)
    if result is None:
        raise HTTPException(502, f"No deep scan data for {address}")
    return result


@router.get("/prediction-markets")
async def prediction_markets(request: Request, query: str = "", category: str = ""):
    """Prediction market intel (Polymarket + Kalshi)."""
    result = await databus.fetch("prediction_markets", query=query, category=category)
    if result is None:
        raise HTTPException(502, "No prediction market data available")
    return result


@router.get("/prediction-signals")
async def prediction_signals(request: Request):
    """Auto-detected trading signals from prediction markets."""
    result = await databus.fetch("prediction_signals")
    if result is None:
        raise HTTPException(502, "No prediction signals available")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# NEW: Batch, Cache Control, Provider Health, Schema Validation (#7,#8,#9)
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/batch")
async def databus_batch(request: Request):
    """#7 Batch DataBus — fetch multiple data types in one call.

    Body: {
        "requests": [
            {"data_type": "token_price", "mint": "So111..."},
            {"data_type": "wallet_labels", "address": "0x..."},
            ...
        ],
        "admin_key": "",       # optional, applies to all
        "consumer_type": "",   # optional, applies to all
    }
    Returns: array of results in same order as requests.
    """
    body = await request.json()
    requests_list = body.get("requests", [])
    if not requests_list:
        raise HTTPException(400, "requests array is required")
    if len(requests_list) > 20:
        raise HTTPException(400, "Maximum 20 requests per batch")

    admin_key = body.get("admin_key", "")
    consumer_type = body.get("consumer_type", "")

    # Inject shared params into each request
    for req in requests_list:
        if admin_key and "admin_key" not in req:
            req["admin_key"] = admin_key
        if consumer_type and "consumer_type" not in req:
            req["consumer_type"] = consumer_type
        req["request"] = request

    results = await databus.fetch_batch(requests_list)
    return {"results": results, "count": len(results)}


@router.post("/warm/start")
async def databus_warm_start(request: Request):
    """Start cache warm background task. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    interval = body.get("interval_seconds", 30)
    await databus.start_cache_warm(interval_seconds=interval)
    return {"status": "started", "interval_seconds": interval}


@router.post("/warm/stop")
async def databus_warm_stop(request: Request):
    """Stop cache warm background task. Admin key required."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")
    await databus.stop_cache_warm()
    return {"status": "stopped"}


@router.get("/providers/health")
async def databus_providers_health(request: Request):
    """#5 Provider Health Dashboard — per-provider success/failure/latency/circuit status."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")
    if not databus._initialized:
        await databus.initialize()
    return {
        "provider_health": databus._provider_health.all_health(),
        "deduplication": databus._dedup.stats(),
    }


@router.get("/cache/stats")
async def databus_cache_stats(request: Request):
    """#10 Per-type cache stats with TTL tuning suggestions."""
    if not _verify_admin(request):
        raise HTTPException(401, "Admin key required")
    return {
        "per_type": databus.cache.type_stats(),
        "tuning_alerts": [
            {"data_type": dt, **stats} for dt, stats in databus.cache.type_stats().items() if stats.get("suggestion")
        ],
    }


@router.post("/schema/validate")
async def databus_schema_validate(request: Request):
    """#15 Validate a DataBus response against expected schema.

    Body: {"data_type": "token_price", "data": {"price_usd": 1.23, ...}}
    Returns: {valid: bool, missing_fields: [...]}
    """
    body = await request.json()
    data_type = body.get("data_type", "")
    data = body.get("data", {})
    from app.databus.response_schema import schema_validator

    is_valid, missing = schema_validator.validate(data_type, data)
    return {"data_type": data_type, "valid": is_valid, "missing_fields": missing}


# ── Social Data Endpoints ─────────────────────────────────────────


@router.get("/social/x/profile/{username}")
async def social_x_profile(username: str):
    """Get X/Twitter user profile — cached 24h."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    result = await agg.x.get_user(username)
    if not result:
        raise HTTPException(404, f"User @{username} not found or API unavailable")
    return {"source": "x_twitter", "username": username, "data": result}


@router.get("/social/x/tweets/{username}")
async def social_x_tweets(username: str, count: int = Query(20, ge=1, le=100)):
    """Get recent tweets from user — cached 15min."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    result = await agg.get_our_tweets(count=count) if username.lower() == "cryptorugmunch" else None
    if result is None:
        # Generic user lookup
        profile = await agg.x.get_user(username)
        if profile:
            result = await agg.x.get_user_tweets(profile["id"], max_results=count)
    if not result:
        raise HTTPException(404, f"Tweets for @{username} not available")
    return {"source": "x_twitter", "username": username, "count": len(result), "data": result}


@router.get("/social/x/mentions")
async def social_x_mentions(count: int = Query(20, ge=1, le=100)):
    """Get mentions of @CryptoRugMunch — cached 15min."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    result = await agg.get_our_mentions(count=count)
    if not result:
        raise HTTPException(404, "Mentions not available (API budget exhausted)")
    return {"source": "x_twitter", "count": len(result), "data": result}


@router.get("/social/x/engagement")
async def social_x_engagement(tweet_ids: str = Query(..., description="Comma-separated tweet IDs")):
    """Get engagement metrics for tweets — cached 1h."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    ids = [tid.strip() for tid in tweet_ids.split(",")[:100]]
    result = await agg.x.get_engagement_metrics(ids)
    return {"source": "x_twitter", "count": len(result), "data": result}


@router.get("/social/x/search")
async def social_x_search(q: str = Query(..., description="Search query"), count: int = Query(10, ge=1, le=100)):
    """Search X/Twitter — VERY expensive, cached 24h. Use sparingly."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    result = await agg.search_mentions(q, count=count)
    if not result:
        raise HTTPException(404, "Search results not available (API budget exhausted or no results)")
    return {"source": "x_twitter", "query": q, "count": len(result), "data": result}


@router.get("/social/kol/{username}")
async def social_kol_reputation(username: str):
    """KOL reputation score — cached 24h."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    return await agg.get_kol_reputation(username)


@router.get("/social/sentiment")
async def social_sentiment(username: str = Query("CryptoRugMunch")):
    """Brand sentiment analysis — cached 1h."""
    from app.databus.social import SocialDataAggregator

    agg = SocialDataAggregator(databus.cache)
    return await agg.get_sentiment(username)


@router.get("/social/x/budget")
async def social_x_budget():
    """Current X API read budget status."""
    from app.databus.social import XTwitterProvider

    provider = XTwitterProvider(databus.cache)
    return {
        "daily_reads_used": provider._daily_reads,
        "daily_reads_remaining": X_DAILY_READ_BUDGET - provider._daily_reads,
        "monthly_free_limit": X_FREE_MONTHLY_READ_LIMIT,
        "daily_budget": X_DAILY_READ_BUDGET,
        "note": "X Free tier: 10k reads/month. Cache aggressively.",
    }


X_HANDLE = "CryptoRugMunch"


@router.get("/social/x/discover")
async def social_x_discover(handle: str = Query(X_HANDLE), limit: int = Query(50, ge=1, le=100)):
    """Discover tweets via web search — no API needed, works for free."""
    from app.databus.social_scraper import XWebScraper

    scraper = XWebScraper(databus.cache)
    tweets = await scraper.discover_tweets(handle=handle, limit=limit)
    return {"source": "web_search", "handle": handle, "count": len(tweets), "data": tweets}


@router.get("/social/x/engagement-report")
async def social_x_engagement_report(handle: str = Query(X_HANDLE)):
    """Get engagement report for a handle — avg likes, best tweets, posting frequency."""
    from app.databus.social_scraper import XWebScraper

    scraper = XWebScraper(databus.cache)
    report = await scraper.get_engagement_report(handle=handle)
    return report


@router.get("/social/x/mentions-discover")
async def social_x_mentions_discover(handle: str = Query(X_HANDLE), limit: int = Query(20, ge=1, le=50)):
    """Find tweets mentioning a handle — via web search."""
    from app.databus.social_scraper import XWebScraper

    scraper = XWebScraper(databus.cache)
    mentions = await scraper.get_mentions(handle=handle, limit=limit)
    return {"source": "web_search", "handle": handle, "count": len(mentions), "data": mentions}


@router.get("/social/x/trending")
async def social_x_trending():
    """Get current crypto trending topics from web search."""
    from app.databus.social_scraper import XWebScraper

    scraper = XWebScraper(databus.cache)
    topics = await scraper.get_trending_topics()
    return {"source": "web_search", "count": len(topics), "data": topics}


# ── Bitquery Blockchain Data Endpoints ───────────────────────────


@router.get("/bitquery/health")
async def bitquery_health():
    """Check Bitquery provider health and billing status."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    return await provider.health()


@router.get("/bitquery/token-price/{network}/{token_address}")
async def bitquery_token_price(network: str, token_address: str):
    """Get DEX token price from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_token_price(network, token_address)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "network": network, "token": token_address, "data": result}


@router.get("/bitquery/holders/{network}/{token_address}")
async def bitquery_holders(network: str, token_address: str, limit: int = Query(100, ge=1, le=500)):
    """Get token holder distribution from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_holder_distribution(network, token_address, limit)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "network": network, "token": token_address, "data": result}


@router.get("/bitquery/tx-trace/{network}/{tx_hash}")
async def bitquery_tx_trace(network: str, tx_hash: str):
    """Get full transaction trace from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_transaction_trace(network, tx_hash)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "network": network, "tx": tx_hash, "data": result}


@router.get("/bitquery/dex-volume/{network}")
async def bitquery_dex_volume(network: str, pool: str = Query(None), timeframe: str = Query("24h")):
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
async def bitquery_balance(network: str, address: str):
    """Get address token balances from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_address_balance(network, address)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "network": network, "address": address, "data": result}


@router.get("/bitquery/cross-chain/{address}")
async def bitquery_cross_chain(address: str, networks: str = Query("ethereum,bsc,solana")):
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
):
    """Get smart contract events from Bitquery."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(databus.cache)
    result = await provider.get_smart_contract_events(network, contract, event, limit)
    if result and "error" in result:
        raise HTTPException(status_code=503, detail=result)
    return {"source": "bitquery", "network": network, "contract": contract, "data": result}


# ═══════════════════════════════════════════════════════════════════════════════
# INTELLIGENT WEBHOOK SYSTEM — Arkham, Helius, Moralis, Alchemy + custom
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/webhooks/{service}")
async def receive_webhook(service: str, request: Request):
    """Universal webhook receiver. Routes to correct handler based on service.

    Supported: arkham, helius, moralis, alchemy, custom

    Auto-validates signatures, deduplicates, caches in DataBus,
    indexes in RAG, triggers premium scanner, pushes alerts.
    """
    try:
        from app.databus.webhooks import handle_webhook
    except ImportError:
        raise HTTPException(501, "Webhook system not available")

    raw_body = await request.body()
    headers = dict(request.headers)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    result = await handle_webhook(service, payload, headers, raw_body)

    if "error" in result and result.get("status") != "duplicate":
        raise HTTPException(400, result["error"])

    return result


@router.get("/webhooks")
async def list_webhooks_endpoint():
    """List all recent webhook events."""
    try:
        from app.databus.webhooks import list_webhooks

        return await list_webhooks()
    except ImportError:
        raise HTTPException(501, "Webhook system not available")


@router.post("/webhooks/setup/{service}")
async def setup_webhook_endpoint(service: str, request: Request):
    """Programmatically register a webhook with a service.

    Body: {
        "webhook_url": "https://rugmunch.io/api/v1/databus/webhooks/helius",
        "events": ["transaction", "token_transfer"],
        "addresses": ["0x..."],
        "api_key": "optional-override"
    }
    """
    try:
        from app.databus.webhooks import setup_webhook
    except ImportError:
        raise HTTPException(501, "Webhook system not available")

    body = await request.json()
    result = await setup_webhook(
        service=service,
        webhook_url=body.get("webhook_url", ""),
        events=body.get("events"),
        api_key=body.get("api_key", ""),
        addresses=body.get("addresses", []),
        chains=body.get("chains", []),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# PREMIUM SCANNER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/premium/bundles/{address}")
async def premium_bundle_detect(address: str, chain: str = "solana", request: Request = None):
    """Bubblemaps-style bundle detection. Premium tier."""
    return await databus.fetch("bundle_detect", address=address, chain=chain)


@router.get("/premium/clusters/{address}")
async def premium_cluster_map(address: str, chain: str = "solana", depth: int = 3, request: Request = None):
    """Full wallet cluster mapping with graph-ready nodes/edges. Premium tier."""
    return await databus.fetch("cluster_map", address=address, chain=chain, depth=depth)


@router.get("/premium/dev-finder/{token}")
async def premium_dev_finder(token: str, chain: str = "solana", request: Request = None):
    """Find developer/creator wallets behind a token. Premium tier."""
    return await databus.fetch("dev_finder", token_address=token, chain=chain)


@router.get("/premium/snipers/{address}")
async def premium_sniper_detect(address: str, chain: str = "solana", request: Request = None):
    """Detect snipers — first-block buyers with fast dumps. Premium tier."""
    return await databus.fetch("sniper_detect", address=address, chain=chain)


@router.get("/premium/bot-farms/{address}")
async def premium_bot_farms(address: str, chain: str = "solana", request: Request = None):
    """Detect bot farms — identical behavior patterns. Premium tier."""
    return await databus.fetch("bot_farm_detect", address=address, chain=chain)


@router.get("/premium/copy-trading/{address}")
async def premium_copy_trading(address: str, chain: str = "solana", request: Request = None):
    """Detect copy trading patterns. Premium tier."""
    return await databus.fetch("copy_trade_detect", address=address, chain=chain)


@router.get("/premium/insider-signals/{address}")
async def premium_insider(address: str, chain: str = "solana", request: Request = None):
    """Detect insider trading signals. Premium tier."""
    return await databus.fetch("insider_detect", address=address, chain=chain)


@router.get("/premium/wash-trading/{address}")
async def premium_wash_trading(address: str, chain: str = "solana", request: Request = None):
    """Detect wash trading patterns. Premium tier."""
    return await databus.fetch("wash_trade_detect", address=address, chain=chain)


@router.get("/premium/mev/{address}")
async def premium_mev(address: str, chain: str = "solana", request: Request = None):
    """Detect MEV sandwich attacks. Premium tier."""
    return await databus.fetch("mev_detect", address=address, chain=chain)


@router.get("/premium/fresh-wallets/{address}")
async def premium_fresh_wallets(address: str, chain: str = "solana", request: Request = None):
    """Analyze fresh wallet concentration — high new-wallet % = rug risk. Premium tier."""
    return await databus.fetch("fresh_wallet_analysis", address=address, chain=chain)


# ═══════════════════════════════════════════════════════════════════════════════
# RUGCHARTS — Volume Authenticity, OHLCV, Token Security
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/premium/volume-authenticity/{address}")
async def premium_volume_auth(address: str, chain: str = "ethereum", request: Request = None):
    """Fake volume % with bootstrap CI. The RugCharts moat — no one else does this."""
    return await databus.fetch(
        "volume_authenticity",
        address=address,
        chain=chain,
        volume_24h=request.query_params.get("volume_24h", "0"),
        liquidity_usd=request.query_params.get("liquidity_usd", "0"),
        unique_wallets=request.query_params.get("unique_wallets", "0"),
        buy_count=request.query_params.get("buy_count", "0"),
        sell_count=request.query_params.get("sell_count", "0"),
        tx_count=request.query_params.get("tx_count", "0"),
    )


@router.get("/ohlcv/{address}")
async def ohlcv_candles(
    address: str,
    chain: str = "ethereum",
    timeframe: str = "1h",
    limit: int = 100,
    request: Request = None,
):
    """OHLCV candlestick data with authenticity scoring baked in."""
    return await databus.fetch("ohlcv", token=address, chain=chain, timeframe=timeframe, limit=limit)


@router.get("/premium/security-scan/{address}")
async def premium_security_scan(address: str, chain: str = "ethereum", request: Request = None):
    """37+ security checks — GoPlus, honeypot, contract, liquidity, holders, rug pull indicators."""
    return await databus.fetch("token_security", address=address, chain=chain)


@router.get("/security-checks")
async def security_check_matrix():
    """Full list of all 37+ security checks with weights and descriptions."""
    return await databus.fetch("token_security", action="matrix")


# ═══════════════════════════════════════════════════════════════════════════════
# RUGCHARTS INTELLIGENCE — 10 Premium Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/premium/smart-money")
async def smart_money_endpoint(chain: str = "solana", limit: int = 20):
    """What profitable wallets are buying right now — with entity labels."""
    return await databus.fetch("smart_money", chain=chain, limit=limit)


@router.get("/premium/whale-alerts/{address}")
async def whale_alerts_endpoint(address: str, chain: str = "solana", min_value: float = 100000):
    """Real-time large transaction detection for a token or wallet."""
    return await databus.fetch("whale_alerts", address=address, chain=chain, min_value_usd=min_value)


@router.get("/premium/token-launches")
async def token_launches_endpoint(chain: str = "solana", limit: int = 50):
    """Newly launched tokens with instant risk scoring by age."""
    return await databus.fetch("token_launches", chain=chain, limit=limit)


@router.get("/premium/insider-detection/{address}")
async def insider_detection_endpoint(address: str, chain: str = "solana"):
    """Detect pre-pump accumulation — volume spikes before major price moves."""
    return await databus.fetch("insider_detection", address=address, chain=chain)


@router.get("/premium/liquidity-risk/{address}")
async def liquidity_risk_endpoint(address: str, chain: str = "solana"):
    """LP health: concentration risk, lock status, holder entities via Arkham."""
    return await databus.fetch("liquidity_risk", address=address, chain=chain)


@router.get("/premium/holder-health/{address}")
async def holder_health_endpoint(address: str, chain: str = "solana"):
    """Holder distribution: Gini coefficient, top concentration, decentralization score."""
    return await databus.fetch("holder_health", address=address, chain=chain)


@router.get("/premium/cross-chain/{address}")
async def cross_chain_endpoint(address: str):
    """Trace entity across all chains via Arkham. Discover all related addresses."""
    return await databus.fetch("cross_chain_entity", address=address)


@router.get("/premium/rug-patterns/{address}")
async def rug_patterns_endpoint(address: str, chain: str = "solana"):
    """Match token against 10 known rug pull patterns. Similarity scoring."""
    return await databus.fetch("rug_patterns", address=address, chain=chain)


@router.get("/premium/dev-reputation/{address}")
async def dev_reputation_endpoint(address: str, chain: str = "solana"):
    """Deployer wallet history: token count, lifespan, entity resolution."""
    return await databus.fetch("dev_reputation", address=address, chain=chain)


@router.get("/premium/token-report/{address}")
async def token_report_endpoint(address: str, chain: str = "solana"):
    """ONE-CALL enhanced token report. Smart verdicts, entity enrichment,
    trust adjustments, tier-aware. THE endpoint powering RugCharts."""
    return await databus.fetch("token_report", address=address, chain=chain)


@router.get("/tiers")
async def tier_comparison_endpoint():
    """Competitive tier comparison: RugCharts vs DexScreener vs Nansen vs GMGN."""
    return await databus.fetch("tier_comparison")


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET DATA — Free APIs: CoinGecko, Fear & Greed, Polymarket
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/market/prices")
async def market_prices(coins: str = "bitcoin,ethereum,solana"):
    """Live prices from CoinGecko. Free, no key needed."""
    return await databus.fetch("live_prices", coins=coins)


@router.get("/market/fear-greed")
async def fear_greed():
    """Crypto Fear & Greed Index. Free, no key needed."""
    return await databus.fetch("fear_greed")


@router.get("/market/trending")
async def market_trending():
    """Trending coins from CoinGecko."""
    return await databus.fetch("trending_coins")


@router.get("/market/prediction-markets")
async def prediction_markets():
    """Prediction markets from Polymarket. Free, no key needed."""
    return await databus.fetch("prediction_markets")


@router.get("/market/brief")
async def market_brief():
    """One-call market overview: prices + fear/greed + trending + predictions."""
    return await databus.fetch("market_brief")


@router.get("/news/full")
async def full_news(limit: int = 15):
    """Complete news feed: headlines + market data + fear/greed + polymarket."""
    return await databus.fetch("full_news", limit=limit)


# ═══════════════════════════════════════════════════════════════════════════════
# NEWS INTELLIGENCE — Multi-source, quality-scored, social-enabled
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/news/intel")
async def news_intel(limit: int = 30):
    """Complete news intelligence — 10+ sources, quality-scored, deduped, sentiment-tagged."""
    return await databus.fetch("news_intel", limit=limit)


@router.get("/news/weekly-best")
async def weekly_best(limit: int = 20):
    """Curated weekly best — highest quality crypto journalism."""
    return await databus.fetch("weekly_best", limit=limit)


@router.get("/news/academic")
async def academic_papers(limit: int = 10):
    """Academic crypto/blockchain research papers from arXiv."""
    return await databus.fetch("academic_papers", limit=limit)


@router.get("/news/social")
async def social_feed(limit: int = 30):
    """Crypto social feed — X/Twitter + CryptoPanic sentiment."""
    return await databus.fetch("social_feed", limit=limit)


@router.post("/news/react/{content_hash}")
async def react_article(content_hash: str, request: Request):
    """React to article: 🔥🐂🐻💎🧠🤡🚀💀"""
    body = await request.json()
    return await databus.fetch(
        "article_reactions",
        content_hash=content_hash,
        reaction=body.get("reaction", "🔥"),
        user=body.get("user", "anon"),
    )


@router.get("/news/reactions/{content_hash}")
async def get_article_reactions(content_hash: str):
    """Get reactions and comments for an article."""
    return await databus.fetch("article_reactions", content_hash=content_hash)


@router.post("/news/comment/{content_hash}")
async def comment_article(content_hash: str, request: Request):
    """Comment on an article."""
    body = await request.json()
    return await databus.fetch(
        "article_comments",
        content_hash=content_hash,
        user=body.get("user", "anon"),
        text=body.get("text", ""),
    )


@router.post("/news/bb-post/{content_hash}")
async def create_bb_from_article(content_hash: str, request: Request):
    """Turn article into Bulletin Board post."""
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    return await databus.fetch("bb_post", content_hash=content_hash, user=body.get("user", "system"))


# ═══════════════════════════════════════════════════════════════════════════════
# CT RUNDOWN — Crypto Twitter Intelligence
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/news/ct-rundown")
async def ct_rundown(limit: int = 20):
    """CT Rundown — top 20 Crypto Twitter stories, AI-summarized, category-diverse."""
    return await databus.fetch("ct_rundown", limit=limit)


@router.get("/news/ct-accounts")
async def ct_accounts():
    """Curated CT account list — 150+ top accounts across 6 tiers."""
    return await databus.fetch("ct_accounts")


# ═══════════════════════════════════════════════════════════════════════════════
# SOCIAL INTELLIGENCE — KOL tracking, shill detection, Daily Intel
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/social/kol/{handle}")
async def kol_profile(handle: str):
    """KOL performance profile — trust score, call history, win rate."""
    return await databus.fetch("kol_profile", handle=handle)


@router.get("/social/kol-leaderboard")
async def kol_leaderboard(limit: int = 20):
    """KOL leaderboard — ranked by trust score and accuracy."""
    return await databus.fetch("kol_leaderboard", limit=limit)


@router.get("/social/shill-alerts")
async def shill_alerts():
    """Active shill campaigns — coordinated promotion, pump-and-dump patterns."""
    return await databus.fetch("shill_detector")


@router.get("/social/scam-monitor")
async def scam_monitor():
    """Scam channel monitoring — Telegram/Discord scam pattern detection."""
    return await databus.fetch("scam_monitor")


@router.get("/social/metrics")
async def social_metrics():
    """Social metrics — trending topics, sentiment, KOL activity."""
    return await databus.fetch("social_metrics")


@router.get("/intel/daily")
async def daily_intel():
    """Daily Intelligence Report — Groq AI-powered market briefing with all data sources."""
    return await databus.fetch("daily_intel")
