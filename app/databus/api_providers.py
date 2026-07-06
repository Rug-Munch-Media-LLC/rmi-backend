"""
CoinStats, Mobula, and CryptoNews DataBus Providers
===================================================
Three free/freemium API providers for enhanced crypto intelligence.

1. CoinStats - Per-wallet DeFi resolution across 10K+ protocols
2. Mobula - Long-tail DEX token pricing (10K free credits/month)
3. CryptoNews - Free unlimited news API (no key needed)
"""

import logging

logger = logging.getLogger("databus.api_providers")

# ═══════════════════════════════════════════════════════════════
# 1. COINSTATS - Per-Wallet DeFi Resolution
# Free tier: public API, no key needed for basic endpoints
# ═══════════════════════════════════════════════════════════════

COINSTATS_BASE = "https://openapiv1.coinstats.app"


async def fetch_coinstats_wallet(address: str, chain: str = "ethereum") -> dict:
    """Resolve a wallet's complete DeFi position - collateral, borrows, LPs, rewards."""
    import aiohttp

    try:
        api_key = __import__("os").environ.get("COINSTATS_API_KEY", "")
        headers = {"X-API-KEY": api_key} if api_key else {}

        async with aiohttp.ClientSession() as session:
            # Wallet balance + DeFi positions
            url = f"{COINSTATS_BASE}/wallet/v1/balance/history/{address}"
            params = {"chain": chain, "limit": 1}

            async with session.get(
                url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "address": address,
                        "chain": chain,
                        "balance": data,
                        "source": "CoinStats (free tier)",
                        "url": "https://coinstats.app/api-docs",
                    }

            # Fallback: try DeFi-specific endpoint
            url2 = f"{COINSTATS_BASE}/defi/v1/positions/{address}"
            async with session.get(
                url2, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    return {
                        "address": address,
                        "defi_positions": await resp.json(),
                        "source": "CoinStats DeFi (free tier)",
                    }

            return {
                "address": address,
                "error": f"CoinStats returned {resp.status}",
                "source": "CoinStats",
            }

    except Exception as e:
        logger.warning(f"CoinStats fetch failed: {e}")
        return {"error": str(e), "source": "CoinStats"}


# ═══════════════════════════════════════════════════════════════
# 2. MOBULA - Long-tail DEX Token Pricing
# Free tier: 10,000 credits/month, no rate limit
# ═══════════════════════════════════════════════════════════════

MOBULA_BASE = "https://api.mobula.io/api/1"


async def fetch_mobula_market(
    asset: str | None = None, blockchain: str | None = None, limit: int = 20
) -> dict:
    """Fetch market data from Mobula - covers long-tail tokens missed by CMC/CG."""
    import aiohttp

    mobula_key = __import__("os").environ.get("MOBULA_API_KEY", "")

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": mobula_key} if mobula_key else {}

            # Multi-data market endpoint
            url = f"{MOBULA_BASE}/market/multi-data"
            params = {"limit": limit}

            if asset:
                params["assets"] = asset
            if blockchain:
                params["blockchain"] = blockchain

            async with session.get(
                url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tokens = data.get("data", [])
                    return {
                        "tokens": tokens[:limit],
                        "count": len(tokens),
                        "query": {"asset": asset, "blockchain": blockchain},
                        "source": "Mobula (free tier - 10K credits/month)",
                        "url": "https://docs.mobula.io",
                        "credits_remaining": resp.headers.get("x-credits-remaining", "unknown"),
                    }

            return {"error": f"Mobula returned {resp.status}", "source": "Mobula"}

    except Exception as e:
        logger.warning(f"Mobula fetch failed: {e}")
        return {"error": str(e), "source": "Mobula"}


async def fetch_mobula_wallet(address: str, blockchain: str = "ethereum") -> dict:
    """Fetch wallet portfolio via Mobula - balances, tokens, transaction history."""
    import aiohttp

    mobula_key = __import__("os").environ.get("MOBULA_API_KEY", "")

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": mobula_key} if mobula_key else {}

            url = f"{MOBULA_BASE}/wallet/portfolio"
            params = {"wallet": address, "blockchain": blockchain}

            async with session.get(
                url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "address": address,
                        "blockchain": blockchain,
                        "portfolio": data.get("data", data),
                        "source": "Mobula Wallet (free tier)",
                    }

            return {
                "address": address,
                "error": f"Mobula wallet returned {resp.status}",
                "source": "Mobula",
            }

    except Exception as e:
        logger.warning(f"Mobula wallet fetch failed: {e}")
        return {"error": str(e), "source": "Mobula"}


# ═══════════════════════════════════════════════════════════════
# 3. CRYPTONEWS (cryptocurrency.cv) - Free unlimited news API
# No API key, no rate limits, REST + RSS
# ═══════════════════════════════════════════════════════════════

CRYPTONEWS_BASE = "https://cryptocurrency.cv/api"


async def fetch_crypto_news(
    category: str | None = None, source: str | None = None, limit: int = 20
) -> dict:
    """Fetch crypto news from cryptocurrency.cv - free, no key, unlimited."""
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            # Try REST API
            url = f"{CRYPTONEWS_BASE}/v1/news"
            params = {"limit": limit}
            if category:
                params["category"] = category
            if source:
                params["source"] = source

            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    articles = data.get("articles", data.get("news", data.get("data", [])))
                    return {
                        "articles": articles[:limit] if isinstance(articles, list) else [],
                        "count": len(articles) if isinstance(articles, list) else 0,
                        "category": category,
                        "source": "cryptocurrency.cv (free, no API key)",
                        "url": "https://github.com/nirholas/cryptocurrency.cv",
                        "features": "RSS/Atom feeds, JSON REST, MCP server, Python SDK",
                    }

            # Fallback: try RSS feed
            url2 = f"{CRYPTONEWS_BASE}/v1/news/rss"
            async with session.get(url2, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return {
                        "rss": (await resp.text())[:5000],
                        "source": "cryptocurrency.cv RSS (free)",
                    }

            return {"error": f"CryptoNews returned {resp.status}", "source": "cryptocurrency.cv"}

    except Exception as e:
        logger.warning(f"CryptoNews fetch failed: {e}")
        return {"error": str(e), "source": "cryptocurrency.cv"}
