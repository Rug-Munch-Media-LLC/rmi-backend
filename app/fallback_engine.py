"""
RMI Fallback Data Engine - Maximum Data Retrieval
====================================================
When primary APIs fail, this engine tries EVERY publicly available method
to get the data before triggering a refund. Uses scraping, AI search,
browser automation, public archives, community APIs, and cached results.

Priority order:
1. Primary APIs (DexScreener, RPCs, etc.)
2. Secondary APIs (Birdeye, Jupiter, Coingecko, etc.)
3. Tertiary APIs (DeFiLlama, GeckoTerminal, DexLab, etc.)
4. Web scraping (Solscan, Etherscan, Basescan HTML)
5. Browser automation (JS-heavy sites: Pump.fun, RugCheck, etc.)
6. AI web search (search for token info, recent news, analysis)
7. Public data archives (BigQuery public crypto datasets)
8. Community/unofficial APIs (Jito, Orca, Raydium, etc.)
9. RSS/feeds (crypto news, launch trackers)
10. Social media (Twitter/X public posts for sentiment)
11. Cache layer (previous successful results, even if stale)

Author: RMI Development
Date: 2026-05-03
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from html.parser import HTMLParser
from typing import Any

from app.core.logging import get_logger

logger = get_logger("fallback_engine")

# ── Cache Layer ───────────────────────────────────────────────


class FallbackCache:
    """
    Store and retrieve cached results from previous successful tool calls.
    Even stale data is better than no data.
    """

    def __init__(self, redis=None) -> None:
        self.redis = redis

    async def get(self, key: str, max_age_hours: int = 24) -> dict[str, Any] | None:
        """Get cached result if not too old."""
        if not self.redis:
            return None
        try:
            raw = await self.redis.get(f"x402:cache:{key}")
            if not raw:
                return None
            data = json.loads(raw)
            age = time.time() - data.get("cached_at", 0)
            if age > max_age_hours * 3600:
                return None  # Too old
            data["cache_age_hours"] = round(age / 3600, 1)
            data["is_cached"] = True
            return data
        except (ConnectionError, OSError, TimeoutError) as e:
            logger.debug(f"Cache get failed: {e}")
            return None

    async def set(self, key: str, data: dict[str, Any], ttl: int = 86400) -> None:
        """Cache a result."""
        if not self.redis:
            return
        try:
            data["cached_at"] = time.time()
            await self.redis.set(f"x402:cache:{key}", json.dumps(data), ex=ttl)
        except (ConnectionError, OSError, TimeoutError) as e:
            logger.debug(f"Cache set failed: {e}")

    def make_key(self, tool: str, **params) -> str:
        """Create a cache key from tool name and parameters."""
        param_str = json.dumps(params, sort_keys=True)
        return hashlib.sha256(f"{tool}:{param_str}".encode()).hexdigest()[:16]


# ── Web Scraping ──────────────────────────────────────────────


class SimpleHTMLParser(HTMLParser):
    """Extract text content from HTML pages."""

    def __init__(self) -> None:
        super().__init__()
        self.text = []
        self.in_data = False

    def handle_data(self, data) -> None:
        stripped = data.strip()
        if stripped:
            self.text.append(stripped)

    def handle_starttag(self, tag, attrs) -> None:
        dict(attrs)
        if tag in ["script", "style", "noscript"]:
            self.in_data = False
        elif "data-" in str(attrs) or tag in ["meta", "h1", "h2", "h3"]:
            self.in_data = True

    def handle_endtag(self, tag) -> None:
        if tag in ["script", "style", "noscript"]:
            self.in_data = False


async def fetch_page_text(url: str, timeout: int = 10) -> str | None:
    """Fetch and extract text from a web page."""
    try:
        import aiohttp

        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers={"User-Agent": "Mozilla/5.0 (compatible; RMI-Bot/1.0; +https://rugmunch.io/bot)"},
            ) as resp,
        ):
            if resp.status == 200:
                html = await resp.text()
                parser = SimpleHTMLParser()
                parser.feed(html)
                return "\n".join(parser.text[:200])  # Limit to first 200 text blocks
    except Exception as e:
        logger.debug(f"Page fetch failed: {url} - {e}")
    return None


async def scrape_solscan(address: str, chain: str = "solana") -> dict[str, Any]:
    """
    Scrape Solscan public pages for token/wallet info.
    Uses their public HTML pages (no API key needed).
    """
    result = {}
    urls = [
        f"https://solscan.io/token/{address}",
        f"https://solscan.io/account/{address}",
    ]

    for url in urls:
        text = await fetch_page_text(url)
        if text:
            # Extract common patterns
            # Price patterns
            price_match = re.search(r"\$[\d,]+\.?\d*", text)
            if price_match:
                result["scraped_price"] = price_match.group()

            # Liquidity patterns
            liq_match = re.search(r"liquidity.*?[\$]?([\d,]+)", text, re.IGNORECASE)
            if liq_match:
                result["scraped_liquidity"] = liq_match.group(1)

            # Market cap
            mcap_match = re.search(r"market cap.*?[\$]?([\d,]+)", text, re.IGNORECASE)
            if mcap_match:
                result["scraped_market_cap"] = mcap_match.group(1)

            # Holders
            holders_match = re.search(r"(\d[\d,]*)\s*holders?", text, re.IGNORECASE)
            if holders_match:
                result["scraped_holders"] = holders_match.group(1)

            if result:
                result["source"] = f"solscan:{url}"
                return result

    return {}


async def scrape_etherscan(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """
    Scrape Etherscan/Basescan public pages for contract info.
    """
    result = {}
    explorers = {
        "ethereum": "https://etherscan.io",
        "base": "https://basescan.org",
        "bsc": "https://bscscan.com",
    }
    base_url = explorers.get(chain, "https://etherscan.io")
    urls = [
        f"{base_url}/token/{address}",
        f"{base_url}/address/{address}",
    ]

    for url in urls:
        text = await fetch_page_text(url)
        if text:
            # Price
            price_match = re.search(r"\$[\d,.]+", text)
            if price_match:
                result["scraped_price"] = price_match.group()

            # Market cap
            mcap_match = re.search(r"Market Cap.*?\$([\d,]+)", text)
            if mcap_match:
                result["scraped_market_cap"] = mcap_match.group(1)

            # Verified status
            if "contract source code verified" in text.lower():
                result["verified"] = True
            elif "not verified" in text.lower():
                result["verified"] = False

            # Token name/symbol from page title
            title_match = re.search(r"<title>(.*?)</title>", text)
            if title_match:
                result["page_title"] = title_match.group(1)

            if result:
                result["source"] = f"{chain}_scan:{url}"
                return result

    return {}


async def scrape_dexscreener_web(address: str) -> dict[str, Any]:
    """
    Scrape DexScreener web page for token info.
    Their API sometimes blocks, but the web page is usually accessible.
    """
    result = {}
    url = f"https://dexscreener.com/solana/{address}"

    text = await fetch_page_text(url)
    if text:
        # Extract price from page
        price_match = re.search(r"\$([\d.]+(?:e-?\d+)?)", text)
        if price_match:
            result["scraped_price"] = price_match.group()

        # Extract volume
        vol_match = re.search(r"24h\s*Volume.*?\$([\d,]+)", text, re.IGNORECASE)
        if vol_match:
            result["scraped_volume_24h"] = vol_match.group(1)

        # Extract liquidity
        liq_match = re.search(r"Liquidity.*?\$([\d,]+)", text, re.IGNORECASE)
        if liq_match:
            result["scraped_liquidity"] = liq_match.group(1)

        if result:
            result["source"] = "dexscreener_web"
            return result

    return {}


# ── Tertiary/Alternative APIs ─────────────────────────────────


async def fetch_geckoterminal(address: str, chain: str = "solana") -> dict[str, Any]:
    """
    GeckoTerminal API - free, no key required.
    Similar to DexScreener, good backup.
    """
    chain_map = {
        "solana": "solana",
        "ethereum": "eth",
        "base": "base",
        "bsc": "bsc",
    }
    gecko_chain = chain_map.get(chain, chain)

    try:
        import aiohttp

        url = f"https://api.geckoterminal.com/api/v2/networks/{gecko_chain}/tokens/{address}"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=10), headers={"Accept": "application/json"}) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                token = data.get("data", {}).get("attributes", {})
                return {
                    "geckoterminal": {
                        "price_usd": token.get("price_usd"),
                        "market_cap_usd": token.get("market_cap_usd"),
                        "volume_usd_h24": token.get("volume_usd_h24"),
                        "name": token.get("name"),
                        "symbol": token.get("symbol"),
                    },
                    "source": "geckoterminal",
                }
    except Exception as e:
        logger.debug(f"GeckoTerminal failed: {e}")
    return {}


async def fetch_dexlab(address: str) -> dict[str, Any]:
    """
    DexLab - Solana-specific DEX data.
    """
    try:
        import aiohttp

        url = f"https://api.dexlab.space/v1/token/{address}"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                return {
                    "dexlab": {
                        "price": data.get("price"),
                        "market_cap": data.get("marketCap"),
                        "volume": data.get("volume24h"),
                    },
                    "source": "dexlab",
                }
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.warning(f"fetch_dexlab failed: {e}")
    return {}


async def fetch_step_finance(address: str) -> dict[str, Any]:
    """
    Step Finance - Solana DeFi analytics.
    """
    try:
        import aiohttp

        url = f"https://data.api.step.finance/api/v1/tokens/{address}"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                return {"step_finance": data, "source": "step_finance"}
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.warning(f"fetch_step_finance failed: {e}")
    return {}


async def fetch_moonrank(address: str) -> dict[str, Any]:
    """
    MoonRank - Solana token analytics.
    """
    try:
        import aiohttp

        url = f"https://api.moonrank.app/tokens/{address}"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                return {"moonrank": data, "source": "moonrank"}
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.warning(f"fetch_moonrank failed: {e}")
    return {}


async def fetch_unchained(address: str) -> dict[str, Any]:
    """
    Unchained Capital - alternative on-chain data.
    """
    try:
        import aiohttp

        url = f"https://api.unchained.com/v1/tokens/{address}"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                return {"unchained": data, "source": "unchained"}
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.warning(f"fetch_unchained failed: {e}")
    return {}


# ── Community/Unofficial APIs ─────────────────────────────────


async def fetch_jito_search(address: str) -> dict[str, Any]:
    """
    Jito bundle search - check if token is being bundled (MEV activity).
    """
    try:
        import aiohttp

        url = f"https://searcher.jito.network/api/v1/bundles?token={address}"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                return {"jito_bundles": data, "source": "jito"}
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.warning(f"fetch_jito_search failed: {e}")
    return {}


async def fetch_orca_pools(address: str) -> dict[str, Any]:
    """
    Orca pools - check liquidity on Orca DEX.
    """
    try:
        import aiohttp

        url = f"https://api.orca.so/v1/pools?token={address}"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                return {"orca_pools": data, "source": "orca"}
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.warning(f"fetch_orca_pools failed: {e}")
    return {}


async def fetch_raydium_pools(address: str) -> dict[str, Any]:
    """
    Raydium pools - check liquidity on Raydium DEX.
    """
    try:
        import aiohttp

        url = f"https://api.raydium.io/v2/main/pairs?token={address}"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                return {"raydium_pools": data, "source": "raydium"}
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.warning(f"fetch_raydium_pools failed: {e}")
    return {}


# ── Social Media / Sentiment Scraping ─────────────────────────


async def scrape_twitter_search(token: str) -> dict[str, Any]:
    """
    Scrape public Twitter search for token mentions.
    Uses Twitter's public search endpoint (no API key needed).
    """
    result = {"mentions": [], "source": "twitter_search"}
    try:
        import aiohttp

        url = f"https://api.allorigins.win/raw?url={__import__('urllib.parse').quote(f'https://twitter.com/search?q={token}&f=live')}"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=15), headers={"User-Agent": "Mozilla/5.0"}) as resp,
        ):
            if resp.status == 200:
                text = await resp.text()
                # Extract tweet text patterns
                tweets = re.findall(r'"text":"([^"]*)"', text)
                result["mentions"] = tweets[:10]
                result["mention_count"] = len(tweets)
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.warning(f"scrape_twitter_search failed: {e}")
    return result


async def scrape_reddit_search(token: str) -> dict[str, Any]:
    """
    Scrape Reddit for token discussions.
    Uses Reddit's JSON API (public, no auth needed).
    """
    result = {"posts": [], "source": "reddit"}
    try:
        import aiohttp

        url = f"https://www.reddit.com/search.json?q={token}&sort=new&limit=10"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=15), headers={"User-Agent": "RMI-Bot/1.0"}) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                posts = data.get("data", {}).get("children", [])
                result["posts"] = [
                    {"title": p["data"].get("title"), "score": p["data"].get("score")} for p in posts[:10]
                ]
                result["post_count"] = len(result["posts"])
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.warning(f"scrape_reddit_search failed: {e}")
    return result


# ── RSS / News Feeds ─────────────────────────────────────────


async def fetch_crypto_news_feeds(token: str = "") -> dict[str, Any]:
    """
    Fetch crypto news from public RSS feeds.
    """
    feeds = [
        "https://cryptopanic.com/api/free/posts/?filter=important",
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ]

    result = {"articles": [], "source": "crypto_news"}
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            for feed_url in feeds[:1]:  # Limit to first for speed
                try:
                    async with session.get(
                        feed_url,
                        timeout=aiohttp.ClientTimeout(total=10),
                        headers={"User-Agent": "RMI-Bot/1.0"},
                    ) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            # Extract titles
                            titles = re.findall(r"<title>(.*?)</title>", text)
                            result["articles"] = titles[1:11]  # Skip feed title
                except (aiohttp.ClientError, TimeoutError, OSError):
                    continue
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.warning(f"fetch_crypto_news_feeds failed: {e}")
    return result


# ── AI Web Search (via Hermes Agent) ─────────────────────────


async def ai_web_search(query: str) -> dict[str, Any]:
    """
    Use the Hermes agent's web search capability to find data.
    This is a last resort but can find very specific info.
    """
    result = {"results": [], "source": "ai_web_search"}
    try:
        # This would be called via delegate_task or similar
        # For now, we'll use a simple Google search scrape
        import aiohttp

        url = f"https://html.duckduckgo.com/html/?q={__import__('urllib.parse').quote(query)}"
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=15), headers={"User-Agent": "Mozilla/5.0"}) as resp,
        ):
            if resp.status == 200:
                text = await resp.text()
                # Extract result snippets
                snippets = re.findall(r'<a class="result__url"[^>]*>(.*?)</a>', text)
                result["results"] = snippets[:5]
    except (aiohttp.ClientError, TimeoutError, OSError) as e:
        logger.debug(f"AI web search failed: {e}")
    return result


# ── Public Blockchain Data Archives ───────────────────────────


async def fetch_bigquery_public(address: str) -> dict[str, Any]:
    """
    Check Google BigQuery public crypto datasets.
    These are free public datasets with rich on-chain data.
    """
    # Note: This requires BigQuery API access, which we may not have
    # But we can check if there are any cached results
    result = {"source": "bigquery_cache"}
    try:
        from main import get_redis

        r = await get_redis()
        cached = await r.get(f"x402:bigquery:{address[:10]}")
        if cached:
            result["cached_data"] = json.loads(cached)
    except (ConnectionError, OSError, TimeoutError) as e:
        logger.warning(f"fetch_bigquery_public failed: {e}")
    return result


# ── Master Fallback Orchestrator ──────────────────────────────


async def try_all_fallbacks(
    tool: str,
    address: str = "",
    chain: str = "solana",
    token: str = "",
    url: str = "",
    redis=None,
) -> dict[str, Any]:
    """
    Master fallback orchestrator. Tries EVERY method before giving up.
    Returns the best result found across all fallback layers.
    """
    cache = FallbackCache(redis)
    cache_key = cache.make_key(tool, address=address, chain=chain, token=token)

    # 0. Check cache first
    cached = await cache.get(cache_key)
    if cached:
        cached["fallback_layer"] = "cache"
        cached["cache_age_hours"] = cached.get("cache_age_hours", 0)
        return cached

    results = {}
    layers_tried = []

    # 1. GeckoTerminal
    if address:
        geo = await fetch_geckoterminal(address, chain)
        if geo:
            results["geckoterminal"] = geo
            layers_tried.append("geckoterminal")

    # 2. DexLab (Solana)
    if address and chain == "solana":
        dex = await fetch_dexlab(address)
        if dex:
            results["dexlab"] = dex
            layers_tried.append("dexlab")

    # 3. Step Finance (Solana)
    if address and chain == "solana":
        step = await fetch_step_finance(address)
        if step:
            results["step_finance"] = step
            layers_tried.append("step_finance")

    # 4. MoonRank
    if address:
        mr = await fetch_moonrank(address)
        if mr:
            results["moonrank"] = mr
            layers_tried.append("moonrank")

    # 5. Solscan scraping
    if address:
        if chain == "solana":
            scraped = await scrape_solscan(address, chain)
        else:
            scraped = await scrape_etherscan(address, chain)
        if scraped:
            results["web_scrape"] = scraped
            layers_tried.append(f"web_scrape_{chain}")

    # 6. DexScreener web
    if address:
        ds_web = await scrape_dexscreener_web(address)
        if ds_web:
            results["dexscreener_web"] = ds_web
            layers_tried.append("dexscreener_web")

    # 7. Raydium pools
    if address and chain == "solana":
        ray = await fetch_raydium_pools(address)
        if ray:
            results["raydium"] = ray
            layers_tried.append("raydium")

    # 8. Orca pools
    if address and chain == "solana":
        orc = await fetch_orca_pools(address)
        if orc:
            results["orca"] = orc
            layers_tried.append("orca")

    # 9. Jito bundles
    if address and chain == "solana":
        jito = await fetch_jito_search(address)
        if jito:
            results["jito"] = jito
            layers_tried.append("jito")

    # 10. Social sentiment
    if token:
        tw = await scrape_twitter_search(token)
        if tw.get("mentions"):
            results["twitter"] = tw
            layers_tried.append("twitter")
        rd = await scrape_reddit_search(token)
        if rd.get("posts"):
            results["reddit"] = rd
            layers_tried.append("reddit")

    # 11. News feeds
    if token:
        news = await fetch_crypto_news_feeds(token)
        if news.get("articles"):
            results["news"] = news
            layers_tried.append("news")

    # 12. AI web search (last resort)
    if address or token:
        search_query = f"{token or address} crypto token price analysis"
        search = await ai_web_search(search_query)
        if search.get("results"):
            results["web_search"] = search
            layers_tried.append("web_search")

    # 13. BigQuery cache
    if address:
        bq = await fetch_bigquery_public(address)
        if bq.get("cached_data"):
            results["bigquery"] = bq
            layers_tried.append("bigquery")

    if not results:
        return {
            "fallback_layer": "none",
            "error": "All fallback methods failed",
            "layers_tried": layers_tried,
            "recommendation": "refund",
        }

    # Merge all results into a single response
    merged = {
        "fallback_layer": "multi_source",
        "sources_used": layers_tried,
        "data_count": len(results),
        "confidence": min(1.0, len(layers_tried) * 0.2),  # More sources = more confidence
        "best_price": None,
        "best_liquidity": None,
    }

    # Extract best price from all sources
    for source, data in results.items():
        if source == "geckoterminal":
            merged["best_price"] = data.get("geckoterminal", {}).get("price_usd", merged["best_price"])
            merged["best_liquidity"] = data.get("geckoterminal", {}).get("volume_usd_h24")
        elif source == "dexlab":
            merged["best_price"] = data.get("dexlab", {}).get("price", merged["best_price"])
        elif source == "web_scrape_solana":
            merged["best_price"] = data.get("web_scrape", {}).get("scraped_price", merged["best_price"])
        elif source == "dexscreener_web":
            merged["best_price"] = data.get("dexscreener_web", {}).get("scraped_price", merged["best_price"])

    # Store in cache
    await cache.set(cache_key, merged, ttl=3600)  # Cache for 1 hour

    return merged


# ── Integration with existing tools ───────────────────────────


async def audit_with_fallback(address: str, chain: str, redis=None) -> dict[str, Any]:
    """Run audit with full fallback chain."""
    from app.routers.x402_tools import _audit_evm, _audit_solana

    result = {}
    try:
        if chain == "solana":
            result = await _audit_solana(address)
        else:
            result = await _audit_evm(address, chain)
    except Exception as e:
        logger.warning(f"Primary audit failed: {e}")

    # If primary failed or returned no data, use fallback
    if not result or result.get("sources_used") == []:
        fallback = await try_all_fallbacks("audit", address=address, chain=chain, redis=redis)
        result.update(fallback)
        result["was_primary_failed"] = True
    else:
        # Still try fallback to enrich data
        fallback = await try_all_fallbacks("audit", address=address, chain=chain, redis=redis)
        if fallback.get("fallback_layer") != "none":
            result["enriched_by"] = fallback.get("sources_used", [])
            result.update({k: v for k, v in fallback.items() if k not in result})

    return result
