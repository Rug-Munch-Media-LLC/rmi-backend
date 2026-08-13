"""Trending engine and real-time launch monitor."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

LAST_KNOWN_LAUNCHES: dict[str, set[Any]] = {}


async def get_trending_tokens(chain: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Get trending tokens from multiple sources, normalized across chains."""
    import time

    start = time.time()
    trending = []
    sources_used = []

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            url = "https://api.dexscreener.com/latest/dex/search"
            params: dict[str, Any] = {"q": chain or "SOL", "limit": limit}
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                now = datetime.now(UTC)
                for pair in pairs:
                    created = pair.get("pairCreatedAt")
                    liq = float(pair.get("liquidity", {}).get("usd", 0))
                    vol = float(pair.get("volume", {}).get("h24", 0))
                    if liq < 1000:
                        continue
                    age_hours: float = 999
                    if created:
                        age_hours = (now - datetime.fromtimestamp(created / 1000, tz=UTC)).total_seconds() / 3600
                    trending_score = (vol * 0.6 + liq * 0.3) / max(age_hours, 1)
                    trending.append(
                        {
                            "chain": pair.get("chainId", chain or "unknown"),
                            "address": pair.get("baseToken", {}).get("address", ""),
                            "symbol": pair.get("baseToken", {}).get("symbol", ""),
                            "name": pair.get("baseToken", {}).get("name", ""),
                            "price_usd": float(pair.get("priceUsd", 0)),
                            "liquidity_usd": liq,
                            "volume_24h": vol,
                            "price_change_24h": float(pair.get("priceChange", {}).get("h24", 0)),
                            "age_hours": round(age_hours, 1),
                            "trending_score": round(trending_score, 2),
                            "dex": pair.get("dexId", ""),
                            "source": "dexscreener",
                        }
                    )
                sources_used.append("dexscreener")
        except Exception as e:
            logger.warning(f"DexScreener trending failed: {e}")

        try:
            network = "solana" if chain == "solana" else "eth"
            resp = await client.get(
                f"https://api.geckoterminal.com/api/v2/networks/{network}/trending_pools",
                params={"limit": limit},
            )
            if resp.status_code == 200:
                pools = resp.json().get("data", [])
                for pool in pools:
                    attrs = pool.get("attributes", {})
                    trending.append(
                        {
                            "chain": chain or "unknown",
                            "address": attrs.get("base_token_address", ""),
                            "symbol": attrs.get("base_token_symbol", ""),
                            "name": attrs.get("name", ""),
                            "price_usd": float(attrs.get("base_token_price_usd", 0)),
                            "liquidity_usd": float(attrs.get("reserve_in_usd", 0)),
                            "volume_24h": float(attrs.get("volume_usd", {}).get("h24", 0)),
                            "price_change_24h": float(attrs.get("price_change_percentage", {}).get("h24", 0)),
                            "source": "geckoterminal",
                        }
                    )
                sources_used.append("geckoterminal")
        except (ConnectionError, OSError, TimeoutError):
            pass

        try:
            resp = await client.get("https://api.coingecko.com/api/v3/search/trending")
            if resp.status_code == 200:
                coins = resp.json().get("coins", [])
                for item in coins[:limit]:
                    coin = item.get("item", {})
                    trending.append(
                        {
                            "chain": chain or "multi",
                            "address": coin.get("id", ""),
                            "symbol": coin.get("symbol", "").upper(),
                            "name": coin.get("name", ""),
                            "price_usd": coin.get("data", {}).get("price", 0),
                            "market_cap_rank": coin.get("market_cap_rank"),
                            "trending_score": coin.get("score", 0),
                            "source": "coingecko",
                        }
                    )
                sources_used.append("coingecko")
        except (ConnectionError, OSError, TimeoutError):
            pass

    seen = set()
    unique = []
    for t in trending:
        key = f"{t.get('chain')}:{t.get('address')}"
        if key not in seen:
            seen.add(key)
            unique.append(t)

    unique.sort(key=lambda x: x.get("trending_score", 0), reverse=True)

    elapsed = time.time() - start
    logger.info(f"Trending: {len(unique)} tokens from {sources_used} in {elapsed:.1f}s")

    return {
        "tokens": unique[:limit],
        "total": len(unique),
        "sources": sources_used,
        "chains_covered": list({t.get("chain", "?") for t in unique}),
        "updated_at": datetime.now(UTC).isoformat(),
    }


async def get_new_launches(chain: str = "solana", since_seconds: int = 300) -> dict[str, Any]:
    """Get tokens launched in the last N seconds."""
    import time

    start = time.time()
    now = datetime.now(UTC)
    cutoff = (now.timestamp() * 1000) - (since_seconds * 1000)

    launches = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get("https://api.dexscreener.com/latest/dex/search", params={"q": chain, "limit": 50})
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                for pair in pairs:
                    created = pair.get("pairCreatedAt", 0)
                    if created and created > cutoff:
                        liq = float(pair.get("liquidity", {}).get("usd", 0))
                        if liq < 500:
                            continue
                        launches.append(
                            {
                                "chain": pair.get("chainId", chain),
                                "address": pair.get("baseToken", {}).get("address", ""),
                                "symbol": pair.get("baseToken", {}).get("symbol", ""),
                                "name": pair.get("baseToken", {}).get("name", ""),
                                "price_usd": float(pair.get("priceUsd", 0)),
                                "liquidity_usd": liq,
                                "age_seconds": int((now.timestamp() * 1000 - created) / 1000),
                                "dex": pair.get("dexId", ""),
                            }
                        )
        except (ConnectionError, OSError, TimeoutError):
            pass

    chain_key = chain or "all"
    if chain_key not in LAST_KNOWN_LAUNCHES:
        LAST_KNOWN_LAUNCHES[chain_key] = set()

    known = LAST_KNOWN_LAUNCHES[chain_key]
    new_launches = [line for line in launches if line["address"] not in known]
    for line in new_launches:
        known.add(line["address"])

    if len(known) > 10000:
        LAST_KNOWN_LAUNCHES[chain_key] = set(list(known)[-5000:])

    elapsed = time.time() - start
    logger.info(f"New launches [{chain}]: {len(new_launches)} in {elapsed:.1f}s")

    return {
        "launches": new_launches,
        "total_new": len(new_launches),
        "total_known": len(known),
        "chain": chain,
        "since_seconds": since_seconds,
        "updated_at": datetime.now(UTC).isoformat(),
    }
