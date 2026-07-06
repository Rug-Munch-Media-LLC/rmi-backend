"""
Token Discovery API Router - Multi-chain token discovery from DexScreener + GeckoTerminal.
Connects to /api/v1/discovery/*
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.token_discovery import MONITORED_CHAINS, discover_tokens, scan_token_security

router = APIRouter(prefix="/api/v1/discovery", tags=["token-discovery"])


class DiscoveryRequest(BaseModel):
    chains: list[str] | None = None
    limit: int = 20


@router.post("/tokens")
async def discover_new_tokens(req: DiscoveryRequest):
    """Discover new tokens across chains (DexScreener + GeckoTerminal)."""
    chains = req.chains if req.chains else list(MONITORED_CHAINS.keys())
    result = await discover_tokens(chains=chains)
    total = sum(len(tokens) for tokens in result.values())
    return {"chains": list(result.keys()), "total_tokens": total, "tokens": result}


@router.get("/chains")
async def list_chains():
    """List monitored chains."""
    return {"chains": MONITORED_CHAINS, "count": len(MONITORED_CHAINS)}


@router.get("/security/{chain}/{address}")
async def token_security(chain: str, address: str):
    """GoPlus security scan for a token."""
    chain_id = MONITORED_CHAINS.get(chain, chain)
    result = await scan_token_security(chain_id, address)
    if not result:
        raise HTTPException(status_code=404, detail="Security scan unavailable")
    return {"chain": chain, "address": address, "security": result}


@router.get("/health")
async def discovery_health():
    cg_status = {}
    try:
        from app.coingecko_connector import COINGECKO_FREE_KEY, COINGECKO_PRO_KEY

        cg_status = {
            "free_key": bool(COINGECKO_FREE_KEY),
            "pro_key": bool(COINGECKO_PRO_KEY),
            "mode": "pro" if COINGECKO_PRO_KEY else "demo",
        }
    except Exception:
        pass
    return {
        "status": "ok",
        "service": "token-discovery",
        "chains": len(MONITORED_CHAINS),
        "coingecko": cg_status,
    }


# ── CoinGecko Market Data ───────────────────────────────────


@router.get("/coingecko/trending")
async def coingecko_trending():
    """Get trending coins from CoinGecko (updated every 30 min)."""
    from app.coingecko_connector import get_coingecko_connector

    cg = get_coingecko_connector()
    trending = await cg.get_trending()
    return {"trending": trending, "count": len(trending)}


@router.get("/coingecko/markets")
async def coingecko_markets(vs: str = "usd", limit: int = 50):
    """Get top coins by market cap."""
    from app.coingecko_connector import get_coingecko_connector

    cg = get_coingecko_connector()
    markets = await cg.get_market_overview(vs_currency=vs, per_page=min(limit, 250))
    return {"markets": markets, "count": len(markets)}


@router.get("/coingecko/global")
async def coingecko_global():
    """Get global crypto market metrics (total market cap, BTC dominance, etc)."""
    from app.coingecko_connector import get_coingecko_connector

    cg = get_coingecko_connector()
    metrics = await cg.get_global_metrics()
    return metrics or {"error": "unavailable"}


@router.get("/coingecko/coin/{coin_id}")
async def coingecko_coin_detail(coin_id: str):
    """Get detailed info about a specific coin."""
    from app.coingecko_connector import get_coingecko_connector

    cg = get_coingecko_connector()
    detail = await cg.get_token_detail(coin_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Coin '{coin_id}' not found")
    return detail


@router.get("/coingecko/categories")
async def coingecko_categories():
    """Get coin categories with market data."""
    from app.coingecko_connector import get_coingecko_connector

    cg = get_coingecko_connector()
    categories = await cg.get_category_market_data()
    return {"categories": categories, "count": len(categories)}


@router.get("/coingecko/exchanges")
async def coingecko_exchanges(limit: int = 20):
    """Get top exchanges by trading volume."""
    from app.coingecko_connector import get_coingecko_connector

    cg = get_coingecko_connector()
    exchanges = await cg.get_exchanges(per_page=min(limit, 100))
    return {"exchanges": exchanges, "count": len(exchanges)}


@router.get("/coingecko/ohlc/{coin_id}")
async def coingecko_ohlc(coin_id: str, vs: str = "usd", days: int = 7):
    """Get OHLC candlestick data for a coin."""
    from app.coingecko_connector import get_coingecko_connector

    cg = get_coingecko_connector()
    data = await cg.get_ohlc(coin_id, vs_currency=vs, days=days)
    return {"coin_id": coin_id, "vs": vs, "days": days, "candles": data}


@router.get("/coingecko/pools/trending")
async def coingecko_trending_pools(chain: str = "solana"):
    """Get trending DEX pools (GeckoTerminal)."""
    from app.coingecko_connector import get_coingecko_connector

    cg = get_coingecko_connector()
    pools = await cg.get_trending_pools(chain=chain)
    return {"chain": chain, "pools": pools, "count": len(pools)}


@router.get("/coingecko/status")
async def coingecko_status():
    """Check CoinGecko API key status and connectivity."""
    from app.coingecko_connector import get_coingecko_connector

    cg = get_coingecko_connector()
    ping = await cg.ping()
    key_status = await cg.api_key_status()
    return {
        "ping": ping,
        "key_status": key_status,
        "connector": cg.status(),
    }
