#!/usr/bin/env python3
"""
RMI Supabase API Router - Exposes all platform tables via REST endpoints.
Frontend connects here for live, real-time data from Supabase.
"""

import contextlib
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.supabase_service import (
    acknowledge_alert,
    add_to_watchlist,
    check_health,
    create_alert,
    get_alerts,
    get_cluster_wallets,
    get_entity_clusters,
    get_market_data,
    get_subscription,
    get_syndicate_wallets,
    get_token,
    get_tokens,
    get_transactions,
    get_wallet,
    get_wallet_connections,
    get_wallet_intel,
    get_wallets,
    get_watchlist,
    remove_from_watchlist,
    store_market_data,
    track_event,
    upsert_wallet,
)

router = APIRouter(prefix="/api/v1")

# ── Models ────────────────────────────────────────────────────


class WalletUpsert(BaseModel):
    address: str
    chain: str = "ethereum"
    label: str | None = None
    risk_score: int | None = None
    tags: list[str] | None = None
    balance_usd: float | None = None
    metadata: dict | None = None


class WatchlistItem(BaseModel):
    user_id: str
    address: str
    type: str = "wallet"  # "token" or "wallet"
    chain: str = "ethereum"
    tags: list[str] | None = None


class MarketDataIn(BaseModel):
    source: str
    data_type: str
    data: dict


# ═════════════════════════════════════════════════════════
# ALERTS
# ═════════════════════════════════════════════════════════


@router.get("/alerts")
async def alerts(
    severity: str = Query(None),
    user_id: str = Query(None),
    limit: int = Query(50, ge=1, le=500),
    unread: bool = Query(False),
):
    data = await get_alerts(severity=severity, user_id=user_id, limit=limit, unread_only=unread)
    return {"alerts": data, "total": len(data)}


@router.post("/alerts")
async def create_alert_endpoint(
    title: str,
    description: str,
    severity: str = "medium",
    alert_type: str = "security",
    chain: str = Query(None),
    token: str = Query(None),
    wallet: str = Query(None),
    amount_usd: float = Query(None),
    user_id: str = Query(None),
):
    result = await create_alert(
        title=title,
        description=description,
        severity=severity,
        alert_type=alert_type,
        chain=chain,
        token=token,
        wallet=wallet,
        amount_usd=amount_usd,
        user_id=user_id,
    )
    if not result:
        raise HTTPException(status_code=500, detail="Create failed")
    return result


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str):
    ok = await acknowledge_alert(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "acknowledged"}


# ═════════════════════════════════════════════════════════
# WALLETS
# ═════════════════════════════════════════════════════════


@router.get("/wallets")
async def wallets(
    chain: str = Query(None),
    risk_min: int = Query(None),
    risk_max: int = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    data = await get_wallets(chain=chain, risk_min=risk_min, risk_max=risk_max, limit=limit)
    return {"wallets": data, "total": len(data)}


@router.get("/wallets/{address}")
async def wallet_detail(address: str, chain: str = Query("ethereum")):
    wallet = await get_wallet(address, chain)
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    # Enrich with intel and transactions
    intel = await get_wallet_intel(address=address, chain=chain, limit=1)
    txns = await get_transactions(wallet_address=address, chain=chain, limit=20)
    return {"wallet": wallet, "intel": intel[0] if intel else None, "transactions": txns}


@router.post("/wallets")
async def wallet_upsert(w: WalletUpsert):
    result = await upsert_wallet(
        w.address,
        w.chain,
        label=w.label,
        risk_score=w.risk_score,
        tags=w.tags,
        balance_usd=w.balance_usd,
        metadata=w.metadata,
    )
    if not result:
        raise HTTPException(status_code=500, detail="Upsert failed")
    return result


@router.get("/wallets/{address}/connections")
async def wallet_connections(address: str):
    connections = await get_wallet_connections(address)
    return {"address": address, "connections": connections, "count": len(connections)}


# ═════════════════════════════════════════════════════════
# SYNDICATE WALLETS
# ═════════════════════════════════════════════════════════


@router.get("/syndicate")
async def syndicates(status: str = Query(None), chain: str = Query(None), limit: int = Query(200, ge=1, le=500)):
    data = await get_syndicate_wallets(status=status, chain=chain, limit=limit)
    return {"syndicate_wallets": data, "total": len(data)}


# ═════════════════════════════════════════════════════════
# WATCHLIST (supports tokens and wallets)
# ═════════════════════════════════════════════════════════


@router.get("/watchlist/{user_id}")
async def watchlist(user_id: str, type: str | None = Query(None)):
    """Get user's watchlist. Optional ?type=token|wallet filter.
    Tries Supabase first, falls back to Redis."""
    data = []
    # Try Supabase first
    with contextlib.suppress(Exception):
        data = await get_watchlist(user_id)

    # Redis fallback
    if not data:
        import json as _json
        import os

        try:
            import redis as _redis

            _r = _redis.Redis(
                host=os.getenv("REDIS_HOST", "rmi-redis"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD") or None,
                decode_responses=True,
            )
            wl_key = f"rmi:watchlist:{user_id}"
            raw_entries = _r.lrange(wl_key, 0, -1)
            data = [_json.loads(e) for e in raw_entries]
        except Exception:
            pass

    if type:
        data = [item for item in data if item.get("type") == type]
    return {"watchlist": data, "total": len(data)}


@router.post("/watchlist")
async def watchlist_add(item: WatchlistItem):
    """Add a token or wallet address to the user's watchlist.
    Tries Supabase first, falls back to Redis."""
    if item.type not in ("token", "wallet"):
        raise HTTPException(status_code=400, detail="type must be 'token' or 'wallet'")

    result = None
    # Try Supabase first
    with contextlib.suppress(Exception):
        result = await add_to_watchlist(
            user_id=item.user_id,
            address=item.address,
            chain=item.chain,
            tags=item.tags,
            item_type=item.type,
        )

    # Redis fallback - always written so watchlist works even without Supabase
    if not result:
        import json as _json
        import os

        try:
            import redis as _redis

            _r = _redis.Redis(
                host=os.getenv("REDIS_HOST", "rmi-redis"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD") or None,
                decode_responses=True,
            )
            wl_key = f"rmi:watchlist:{item.user_id}"
            entry = _json.dumps(
                {
                    "user_id": item.user_id,
                    "address": item.address,
                    "type": item.type,
                    "chain": item.chain,
                    "tags": item.tags or [],
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            _r.lpush(wl_key, entry)
            _r.expire(wl_key, 86400 * 30)  # 30 day TTL
            result = _json.loads(entry)
        except Exception:
            pass

    if not result:
        raise HTTPException(status_code=500, detail="Add failed - both Supabase and Redis unavailable")

    # Broadcast alert via WebSocket so frontend gets notified
    try:
        import asyncio

        from main import ws_broadcast_alert

        asyncio.create_task(
            ws_broadcast_alert(
                {
                    "event": "watchlist_add",
                    "user_id": item.user_id,
                    "address": item.address,
                    "type": item.type,
                    "chain": item.chain,
                }
            )
        )
    except Exception:
        pass

    return result


@router.delete("/watchlist/{user_id}/{address}")
async def watchlist_remove(user_id: str, address: str, chain: str = Query("ethereum")):
    """Remove item from watchlist. Tries Supabase and Redis."""
    ok = False
    with contextlib.suppress(Exception):
        ok = await remove_from_watchlist(user_id, address, chain)

    # Also remove from Redis
    import json as _json
    import os

    try:
        import redis as _redis

        _r = _redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
            decode_responses=True,
        )
        wl_key = f"rmi:watchlist:{user_id}"
        raw_entries = _r.lrange(wl_key, 0, -1)
        for raw in raw_entries:
            entry = _json.loads(raw)
            if entry.get("address") == address and entry.get("chain", "ethereum") == chain:
                _r.lrem(wl_key, 1, raw)
                ok = True
                break
    except Exception:
        pass

    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    return {"status": "removed"}


# ═════════════════════════════════════════════════════════
# TOKENS
# ═════════════════════════════════════════════════════════


@router.get("/tokens")
async def tokens(
    chain: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
    order: str = Query("volume_24h.desc"),
):
    data = await get_tokens(chain=chain, limit=limit, order_by=order)
    return {"tokens": data, "total": len(data)}


@router.get("/tokens/discover")
async def token_discover(chains: str = Query("solana,ethereum,base,bsc")):
    """Discover new token launches across chains via DexScreener + GeckoTerminal."""
    chain_list = [c.strip() for c in chains.split(",") if c.strip()]
    from app.token_discovery import discover_tokens

    try:
        discovered = await discover_tokens(chains=chain_list)
        total = sum(len(tokens) for tokens in discovered.values())
        return {"chains": discovered, "total": total}
    except Exception as e:
        return {"error": str(e), "chains": {}, "total": 0}


@router.get("/tokens/trending")
async def trending_tokens(limit: int = Query(20, ge=1, le=50)):
    """Trending tokens from CoinGecko with GeckoTerminal fallback."""
    from app.coingecko_connector import CoinGeckoConnector

    tokens = []
    try:
        cg = CoinGeckoConnector()
        data = await cg.get_trending()  # returns [{id, symbol, name, market_cap_rank, price_btc, score}]
        if data and isinstance(data, list):
            for item in data[:limit]:
                tokens.append(
                    {
                        "address": item.get("id", ""),
                        "name": item.get("name", ""),
                        "symbol": item.get("symbol", "").upper(),
                        "price_usd": None,
                        "price_btc": item.get("price_btc"),
                        "volume_h24": None,
                        "change_24h": None,
                        "market_cap_rank": item.get("market_cap_rank"),
                        "flags": [],
                    }
                )
    except Exception:
        pass
    # Fallback: GeckoTerminal trending pools
    if not tokens:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools",
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    ds = r.json()
                    for pool in (ds.get("data", []) or [])[:limit]:
                        attrs = pool.get("attributes", {})
                        rel = pool.get("relationships", {})
                        base_attrs = {}
                        with contextlib.suppress(Exception):
                            base_attrs = rel.get("base_token", {}).get("data", {}) or {}
                        tokens.append(
                            {
                                "address": base_attrs.get("address", pool.get("id", "")[:12]),
                                "name": attrs.get("name", pool.get("id", "")[:12]),
                                "symbol": (attrs.get("symbol", "") or base_attrs.get("symbol", "") or "").upper(),
                                "price_usd": float(attrs.get("base_token_price_usd", 0) or 0),
                                "volume_h24": str(
                                    attrs.get("volume_usd", {}).get("h24", 0)
                                    if isinstance(attrs.get("volume_usd"), dict)
                                    else 0
                                ),
                                "change_24h": float(
                                    attrs.get("price_change_percentage", {}).get("h24", 0)
                                    if isinstance(attrs.get("price_change_percentage"), dict)
                                    else 0
                                ),
                                "flags": ["solana"],
                            }
                        )
        except Exception:
            pass
    return {"tokens": tokens, "count": len(tokens)}


@router.get("/tokens/{address}")
async def token_detail(address: str, chain: str = Query("ethereum")):
    token = await get_token(address, chain)
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    return token


# ═════════════════════════════════════════════════════════
# ENTITY CLUSTERS
# ═════════════════════════════════════════════════════════


@router.get("/clusters")
async def clusters(limit: int = Query(50, ge=1, le=200)):
    data = await get_entity_clusters(limit=limit)
    return {"clusters": data, "total": len(data)}


@router.get("/clusters/{cluster_id}")
async def cluster_detail(cluster_id: str):
    addresses = await get_cluster_wallets(cluster_id)
    return {"cluster_id": cluster_id, "addresses": addresses, "count": len(addresses)}


# ═════════════════════════════════════════════════════════
# TRANSACTIONS
# ═════════════════════════════════════════════════════════


@router.get("/transactions/{wallet_address}")
async def transactions(wallet_address: str, chain: str = Query(None), limit: int = Query(50)):
    data = await get_transactions(wallet_address=wallet_address, chain=chain, limit=limit)
    return {"transactions": data, "total": len(data)}


# ═════════════════════════════════════════════════════════
# MARKET DATA
# ═════════════════════════════════════════════════════════


@router.get("/market-data/{data_type}")
async def market_data(data_type: str, limit: int = Query(10)):
    data = await get_market_data(data_type=data_type, limit=limit)
    return {"data": data, "total": len(data)}


@router.post("/market-data")
async def market_data_store(m: MarketDataIn):
    result = await store_market_data(m.source, m.data_type, m.data)
    if not result:
        raise HTTPException(status_code=500, detail="Store failed")
    return result


# ═════════════════════════════════════════════════════════
# SUBSCRIPTIONS
# ═════════════════════════════════════════════════════════


@router.get("/subscriptions/{user_id}")
async def subscription(user_id: str):
    sub = await get_subscription(user_id)
    if not sub:
        return {"plan": "FREE", "status": "none"}
    return sub


# ═════════════════════════════════════════════════════════
# ANALYTICS
# ═════════════════════════════════════════════════════════


@router.post("/analytics/event")
async def analytics_event(
    event_type: str,
    user_id: str = Query(None),
    event_data: str = Query("{}"),
    page: str = Query(None),
):
    import json

    try:
        data = json.loads(event_data)
    except Exception:
        data = {"raw": event_data}
    result = await track_event(event_type=event_type, user_id=user_id, event_data=data, page=page)
    return {"status": "tracked", "id": result.get("id") if result else None}


# ═════════════════════════════════════════════════════════
# HEALTH
# ═════════════════════════════════════════════════════════


@router.get("/health/supabase")
async def supabase_health():
    return await check_health()


@router.get("/health")
async def full_health():
    sb = await check_health()
    tables = [
        "wallets",
        "alerts",
        "watchlist",
        "tokens",
        "bulletin_posts",
        "badges",
        "syndicate_wallets",
        "entity_clusters",
    ]
    return {"supabase": sb, "timestamp": datetime.now().isoformat(), "tables_monitored": tables}
