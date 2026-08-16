"""
RugCharts News & Market Data Provider
======================================
Redis-backed cached news aggregation. All endpoints serve from
Redis (instant response). A background task refreshes every 5 min.

Data sources:
  - CoinGecko (prices, trending)
  - Alternative.me (Fear & Greed Index)
  - Polymarket (prediction markets)
  - CryptoPanic (news sentiment - if key)
  - 75+ RSS feeds (CoinTelegraph, Decrypt, The Block, etc.)
  - Existing MCP servers (feargreed, prediction-market, jupiter)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import redis.asyncio as aioredis

from app.core.logging import get_logger

logger = get_logger("news_provider")

# ── Free API endpoints (no keys needed) ────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
POLYMARKET_URL = "https://gamma-api.polymarket.com/events"
COINGECKO_KEY = os.getenv("COINGECKO_API_KEY", "")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "").strip()

# ── Redis config ───────────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "") or None
NEWS_CACHE_KEY = "rmi:news:full_feed"
NEWS_CACHE_TTL = 300  # 5 minutes
MARKET_BRIEF_KEY = "rmi:news:market_brief"
MARKET_BRIEF_TTL = 60  # 1 minute

CACHE = {}  # In-memory fallback cache with TTL


def _cached(key: str, ttl: int = 60) -> dict[str, Any] | None:
    if key in CACHE:
        data, ts = CACHE[key]
        if time.time() - ts < ttl:
            return data
    return None


def _cache_set(key: str, data: dict[str, Any]) -> None:
    CACHE[key] = (data, time.time())


async def _get_redis() -> aioredis.Redis | None:
    """Get async Redis connection."""
    try:
        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=3,
        )
        await r.ping()
        return r
    except Exception as e:
        logger.debug(f"Redis unavailable: {e}")
        return None


async def _redis_get(key: str) -> dict[str, Any] | None:
    """Read JSON from Redis."""
    try:
        r = await _get_redis()
        if r:
            data = await r.get(key)
            await r.close()
            if data:
                return json.loads(data)
    except Exception as e:
        logger.debug(f"Redis get failed: {e}")
    return None


async def _redis_set(key: str, data: dict[str, Any], ttl: int = 300) -> None:
    """Write JSON to Redis with TTL."""
    try:
        r = await _get_redis()
        if r:
            await r.setex(key, ttl, json.dumps(data, default=str))
            await r.close()
            logger.debug(f"Redis set {key} TTL={ttl}s")
    except Exception as e:
        logger.debug(f"Redis set failed: {e}")


# ── Top RSS feeds (the "TOP_50_FEEDS" list; 75+ after 2026-08-16 expansion) ──
# Curated from the 170+ feed list — fast, reliable, high-signal sources.
# All entries added 2026-08-16 were live-probed (HTTP 200 + feedparser parse
# + fresh items within 30 days) before inclusion; a feed that later dies is
# skipped at fetch time by the fail-open per-feed handler (never blocks the
# batch), and the registry-level probe (scripts/test_news_sources.py +
# app/databus/news_source_health.py) tracks VERIFIED/STALE/DEAD for pruning.
TOP_50_FEEDS = [
    # ---- Tier 1: Major News Outlets (fastest, always up) ----
    ("https://cointelegraph.com/rss", "CoinTelegraph"),
    ("https://decrypt.co/feed", "Decrypt"),
    ("https://blockworks.co/feed", "Blockworks"),
    ("https://www.theblock.co/rss.xml", "The Block"),
    ("https://thedefiant.io/feed", "The Defiant"),
    ("https://www.bankless.com/feed", "Bankless"),
    ("https://bitcoinmagazine.com/feed", "Bitcoin Magazine"),
    ("https://protos.com/feed/", "Protos"),
    ("https://beincrypto.com/feed/", "BeInCrypto"),
    ("https://unchainedcrypto.com/feed/", "Unchained"),
    ("https://coindesk.com/arc/outboundfeeds/rss/", "CoinDesk"),
    ("https://cryptoslate.com/feed/", "CryptoSlate"),
    ("https://cryptobriefing.com/feed/", "CryptoBriefing"),
    ("https://cryptopotato.com/feed/", "CryptoPotato"),
    ("https://dailyhodl.com/feed/", "Daily Hodl"),
    ("https://www.newsbtc.com/feed/", "NewsBTC"),
    ("https://bitcoinist.com/feed/", "Bitcoinist"),
    # ---- Tier 2: Security Firms ----
    ("https://peckshield.medium.com/feed", "PeckShield"),
    ("https://slowmist.medium.com/feed", "SlowMist"),
    ("https://certik.medium.com/feed", "CertiK"),
    ("https://immunefi.medium.com/feed", "Immunefi"),
    ("https://blog.trailofbits.com/feed/", "Trail of Bits"),
    ("https://www.chainalysis.com/blog/feed/", "Chainalysis"),
    ("https://blog.openzeppelin.com/feed/", "OpenZeppelin"),
    # ---- Tier 3: Research & Analytics ----
    ("https://insights.glassnode.com/feed/", "Glassnode"),
    ("https://dune.com/blog/feed", "Dune Analytics"),
    ("https://messari.io/feed", "Messari"),
    ("https://defillama.com/feed", "DeFi Llama"),
    # ---- Tier 4: Protocol Blogs ----
    ("https://blog.ethereum.org/feed.xml", "Ethereum Foundation"),
    ("https://solana.com/news/feed", "Solana"),
    ("https://blog.arbitrum.io/feed", "Arbitrum"),
    ("https://blog.optimism.io/feed", "Optimism"),
    ("https://polygon.technology/blog/feed", "Polygon"),
    ("https://blog.sui.io/feed", "Sui"),
    ("https://blog.cosmos.network/feed", "Cosmos"),
    # ---- Tier 5: DeFi Protocols ----
    ("https://uniswap.org/blog/feed.xml", "Uniswap"),
    ("https://aave.com/blog/rss.xml", "Aave"),
    ("https://blog.chain.link/feed", "Chainlink"),
    ("https://blog.makerdao.com/feed/", "MakerDAO"),
    ("https://blog.lido.fi/feed", "Lido"),
    # ---- Tier 6: Exchange Blogs ----
    ("https://blog.coinbase.com/feed", "Coinbase"),
    ("https://blog.kraken.com/feed/", "Kraken"),
    ("https://www.binance.com/en/feed/news", "Binance"),
    ("https://blog.bitfinex.com/feed/", "Bitfinex"),
    ("https://blog.ledger.com/feed/", "Ledger"),
    # ---- Tier 7: VC & Investment ----
    ("https://a16zcrypto.com/feed/", "a16z Crypto"),
    ("https://www.paradigm.xyz/feed", "Paradigm"),
    ("https://panteracapital.com/feed/", "Pantera Capital"),
    ("https://multicolin.capital/feed/", "Multicoin Capital"),
    ("https://variant.fund/feed/", "Variant Fund"),
    # ---- Tier 8: Market Data ----
    ("https://www.tradingview.com/blog/feed/", "TradingView"),
    ("https://blog.santiment.net/feed/", "Santiment"),
    ("https://blog.coinmarketcap.com/feed/", "CoinMarketCap"),
    ("https://www.coingecko.com/en/blog/feed", "CoinGecko Blog"),
    # ---- Tier 9: Security/Threat Intel ----
    ("https://www.bleepingcomputer.com/feed/", "BleepingComputer"),
    ("https://www.web3isgoinggreat.com/feed", "W3IGG"),
    ("https://krebsonsecurity.com/feed/", "Krebs on Security"),
    # ---- Tier 10: News outlets (expansion 2026-08-16, live-probed) ----
    ("https://www.coingape.com/feed/", "CoinGape"),
    ("https://zycrypto.com/feed/", "ZyCrypto"),
    ("https://ambcrypto.com/feed/", "AMBCrypto"),
    ("https://coinpedia.org/feed/", "Coinpedia"),
    ("https://crypto.news/feed/", "Crypto.news"),
    ("https://blockonomi.com/feed/", "Blockonomi"),
    ("https://cryptodaily.co.uk/feed/", "CryptoDaily"),
    ("https://news.bitcoin.com/feed/", "Bitcoin.com News"),
    ("https://watcher.guru/feed/", "Watcher Guru"),
    ("https://cryptonews.com/feed/", "Cryptonews.com"),
    ("https://www.trustnodes.com/feed", "Trustnodes"),
    ("https://www.cryptopolitan.com/feed/", "Cryptopolitan"),
    ("https://u.today/rss", "U.Today"),
    ("https://nulltx.com/feed/", "NullTX"),
    ("https://www.crypto-news-flash.com/feed/", "Crypto News Flash"),
    ("https://www.thecoinrepublic.com/feed/", "Coin Republic"),
    ("https://coinedition.com/feed/", "Coin Edition"),
    ("https://www.crypto-reporter.com/feed/", "Crypto Reporter"),
    ("https://www.cointribune.com/en/feed/", "CoinTribune"),
    ("https://themerkle.com/feed/", "The Merkle"),
    ("https://www.coinspeaker.com/feed/", "CoinSpeaker"),
    ("https://dailycoin.com/feed/", "DailyCoin"),
    ("https://www.bitcoinsistemi.com/en/feed/", "Bitcoin Sistemi (EN)"),
    # ---- Tier 11: Exchange & market blogs (expansion 2026-08-16) ----
    ("https://www.financemagnates.com/cryptocurrency/feed/", "Finance Magnates Crypto"),
    ("https://www.investing.com/rss/news_25.rss", "Investing.com Crypto"),
    ("https://www.bitget.com/news/feed", "Bitget"),
    # ---- Tier 12: Security firms (expansion 2026-08-16, live-probed) ----
    ("https://blog.verichains.io/feed", "Verichains"),
]


# ── 1. COINGECKO - Prices, trending, market data ───────────────────


async def get_market_prices(coins: str = "bitcoin,ethereum,solana", **kw) -> dict[str, Any] | None:
    """Live crypto prices from CoinGecko. Free tier, no key needed."""
    cache_key = f"prices:{coins}"
    cached = _cached(cache_key, 30)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            headers = {}
            if COINGECKO_KEY:
                headers["x-cg-demo-api-key"] = COINGECKO_KEY
            r = await c.get(
                f"{COINGECKO_BASE}/simple/price",
                params={
                    "ids": coins,
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                    "include_market_cap": "true",
                },
                headers=headers,
            )
            if r.status_code == 200:
                data = r.json()
                result = {
                    "prices": data,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "source": "coingecko",
                    "free": True,
                }
                _cache_set(cache_key, result)
                return result
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"CoinGecko failed: {e}")
    return None


async def get_trending_coins(**kw) -> dict[str, Any] | None:
    """Trending coins from CoinGecko search."""
    cache_key = "trending"
    cached = _cached(cache_key, 120)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            headers = {}
            if COINGECKO_KEY:
                headers["x-cg-demo-api-key"] = COINGECKO_KEY
            r = await c.get(f"{COINGECKO_BASE}/search/trending", headers=headers)
            if r.status_code == 200:
                data = r.json()
                coins = data.get("coins", [])[:10]
                result = {
                    "trending": [
                        {
                            "name": c["item"]["name"],
                            "symbol": c["item"]["symbol"],
                            "market_cap_rank": c["item"].get("market_cap_rank"),
                            "score": c["item"].get("score"),
                        }
                        for c in coins
                    ],
                    "source": "coingecko",
                    "free": True,
                }
                _cache_set(cache_key, result)
                return result
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Trending failed: {e}")
    return None


# ── 2. FEAR & GREED INDEX - Alternative.me ─────────────────────────


async def get_fear_greed(**kw) -> dict[str, Any] | None:
    """Crypto Fear & Greed Index. Completely free, no key."""
    cache_key = "fear_greed"
    cached = _cached(cache_key, 300)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(FEAR_GREED_URL)
            if r.status_code == 200:
                data = r.json().get("data", [{}])[0]
                value = int(data.get("value", 50))
                classification = data.get("value_classification", "Neutral")

                if value <= 25:
                    color, sentiment = "#ff0044", "Extreme Fear"
                elif value <= 45:
                    color, sentiment = "#ff8800", "Fear"
                elif value <= 55:
                    color, sentiment = "#ffd700", "Neutral"
                elif value <= 75:
                    color, sentiment = "#88ff00", "Greed"
                else:
                    color, sentiment = "#00ff88", "Extreme Greed"

                result = {
                    "value": value,
                    "classification": classification,
                    "sentiment": sentiment,
                    "color": color,
                    "timestamp": data.get("timestamp"),
                    "source": "alternative.me",
                    "free": True,
                }
                _cache_set(cache_key, result)
                return result
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Fear & Greed failed: {e}")
    return None


# ── 3. POLYMARKET - Prediction markets ─────────────────────────────


async def get_prediction_markets(limit: int = 5, tag: str = "crypto", **kw) -> dict[str, Any] | None:
    """Prediction market events from Polymarket. Free, no key."""
    cache_key = f"polymarket:{tag}:{limit}"
    cached = _cached(cache_key, 120)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            params: dict[str, Any] = {"limit": limit, "active": "true", "closed": "false"}
            if tag:
                params["tag"] = tag
            r = await c.get(POLYMARKET_URL, params=params)
            if r.status_code == 200:
                events = r.json()
                result = {
                    "markets": [
                        {
                            "title": e.get("title", ""),
                            "volume": e.get("volume", 0),
                            "liquidity": e.get("liquidity", 0),
                            "end_date": e.get("endDate", ""),
                            "slug": e.get("slug", ""),
                        }
                        for e in events[:limit]
                    ],
                    "total": len(events),
                    "source": "polymarket",
                    "free": True,
                }
                _cache_set(cache_key, result)
                return result
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Polymarket failed: {e}")
    return None


# ── 4. COMBINED MARKET BRIEF - All sources in one call ─────────────


async def get_market_brief(**kw) -> dict[str, Any] | None:
    """One-call market overview: prices + fear/greed + trending + prediction markets."""
    prices_task = get_market_prices()
    fear_task = get_fear_greed()
    trending_task = get_trending_coins()
    poly_task = get_prediction_markets(limit=3)

    prices = await prices_task
    fear = await fear_task
    trending = await trending_task
    polymarket = await poly_task

    brief_parts = []
    if fear:
        brief_parts.append(f"Market sentiment: {fear['classification']} ({fear['value']}/100)")
    if prices:
        for coin, data in prices.get("prices", {}).items():
            change = data.get("usd_24h_change", 0) or 0
            emoji = "🔴" if change < -3 else "🟠" if change < 0 else "🟢"
            brief_parts.append(f"{emoji} {coin.title()}: ${data.get('usd', 0):,.0f} ({change:+.1f}%)")

    return {
        "brief": " | ".join(brief_parts),
        "prices": prices,
        "fear_greed": fear,
        "trending": trending,
        "prediction_markets": polymarket,
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": ["coingecko", "alternative.me", "polymarket"],
        "source": "market_brief",
        "free": True,
    }


# ── 5. FAST API NEWS FETCH (no RSS, instant) ──────────────────────


async def _fetch_fast_api_news(limit: int = 20) -> list[dict[str, Any]]:
    """Fetch news from fast APIs only — CoinGecko + CryptoPanic. No RSS.

    These complete in < 3 seconds and provide real articles.
    Used as the primary source on cold cache, and as fallback when RSS is empty.
    """
    articles: list[dict[str, Any]] = []

    # Try CoinGecko news API
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            headers = {"x-cg-demo-api-key": COINGECKO_KEY} if COINGECKO_KEY else {}
            r = await c.get(f"{COINGECKO_BASE}/news", params={"per_page": limit}, headers=headers)
            if r.status_code == 200:
                data = r.json()
                for item in data.get("data", [])[:limit]:
                    title = item.get("title", "")
                    url = item.get("url", "")
                    desc = item.get("description", "") or title
                    pub_str = item.get("updated_at", item.get("created_at", ""))
                    try:
                        published = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).isoformat()
                    except (ValueError, TypeError):
                        published = datetime.now(UTC).isoformat()
                    articles.append(
                        {
                            "id": f"cg-{hashlib_quick(title + url)}",
                            "title": title,
                            "url": url,
                            "description": desc[:300],
                            "source": "CoinGecko",
                            "published_at": published,
                            "category": "market",
                            "sentiment": "neutral",
                            "kind": "api",
                            "tier": "market",
                        }
                    )
    except Exception as e:
        logger.warning(f"CoinGecko news API failed: {e}")

    # Try CryptoPanic (free tier)
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            url = "https://cryptopanic.com/api/free/v1/posts/"
            params: dict[str, Any] = {"public": "true", "kind": "news", "limit": min(limit, 20)}
            if CRYPTOPANIC_API_KEY:
                url = "https://cryptopanic.com/api/v1/posts/"
                params["auth_token"] = CRYPTOPANIC_API_KEY
            r = await c.get(url, params=params)
            if r.status_code == 200:
                data = r.json()
                for item in data.get("results", [])[:limit]:
                    title = item.get("title", "")
                    url = item.get("url", "")
                    src = item.get("source", {})
                    source_name = src.get("title", "CryptoPanic") if isinstance(src, dict) else "CryptoPanic"
                    pub_str = item.get("published_at", item.get("created_at", ""))
                    try:
                        published = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).isoformat()
                    except (ValueError, TypeError):
                        published = datetime.now(UTC).isoformat()
                    votes = item.get("votes", {})
                    bull = votes.get("positive", 0) if isinstance(votes, dict) else 0
                    bear = votes.get("negative", 0) if isinstance(votes, dict) else 0
                    sentiment = "bullish" if bull > bear * 2 else "bearish" if bear > bull * 2 else "neutral"
                    articles.append(
                        {
                            "id": f"cp-{hashlib_quick(title + url)}",
                            "title": title,
                            "url": url,
                            "description": (item.get("description", "") or title)[:300],
                            "source": source_name,
                            "published_at": published,
                            "category": "market",
                            "sentiment": sentiment,
                            "kind": "api",
                            "tier": "market",
                        }
                    )
    except Exception as e:
        logger.warning(f"CryptoPanic API failed: {e}")

    return articles


def hashlib_quick(text: str) -> str:
    """Quick hash for article deduplication."""
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:12]


# ── 6. RSS FETCH (top 50 feeds, batched) ──────────────────────────


async def _fetch_rss_articles(
    feeds: list[tuple[str, str]], limit_per_source: int = 3, timeout: float = 8.0
) -> list[dict[str, Any]]:
    """Fetch RSS articles from a list of (url, source_name) feeds.

    Processes in batches of 10 with an overall timeout per batch.
    """
    try:
        import feedparser
    except ImportError:
        logger.warning("feedparser not installed — RSS disabled")
        return []

    articles: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    async def _fetch_one(url: str, source_name: str) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; RMI-NewsBot/4.0; +https://rugmunch.io)"},
            ) as c:
                r = await c.get(url)
                if r.status_code != 200:
                    return []
                feed = feedparser.parse(r.text)
        except (httpx.HTTPError, TimeoutError, OSError, Exception) as e:
            logger.debug(f"RSS fetch error {source_name}: {e}")
            return []

        result = []
        for entry in feed.entries[:limit_per_source]:
            try:
                title = entry.get("title", "")
                link = entry.get("link", "")
                if not title or title in seen_titles:
                    continue
                desc = entry.get("summary", "") or entry.get("description", "") or title
                pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub_parsed and isinstance(pub_parsed, (tuple, list)) and len(pub_parsed) >= 6:
                    from datetime import timedelta

                    pub_dt = datetime(*cast("tuple[int, int, int, int, int, int]", tuple(pub_parsed[:6])), tzinfo=UTC)
                    if datetime.now(UTC) - pub_dt > timedelta(hours=72):
                        continue
                    published = pub_dt.isoformat()
                else:
                    published = datetime.now(UTC).isoformat()
                result.append(
                    {
                        "id": f"rss-{hashlib_quick(title + link)}",
                        "title": title,
                        "url": link,
                        "description": desc[:300],
                        "source": source_name,
                        "published_at": published,
                        "category": "news",
                        "sentiment": "neutral",
                        "kind": "external",
                        "tier": "news",
                    }
                )
            except Exception:
                logger.debug("RSS entry skipped source=%s title=%r", source_name, title[:80])
                continue
        if result:
            logger.debug(f"RSS {source_name}: {len(result)} articles")
        return result

    # Process in batches of 10
    for i in range(0, len(feeds), 10):
        batch = feeds[i : i + 10]
        batch_results = await asyncio.gather(
            *[_fetch_one(url, name) for url, name in batch],
            return_exceptions=True,
        )
        for r in batch_results:
            if isinstance(r, list):
                for art in r:
                    if art["title"] not in seen_titles:
                        seen_titles.add(art["title"])
                        articles.append(art)

    return articles


# ── 7. BACKGROUND NEWS CACHE REFRESHER ─────────────────────────────


async def refresh_news_cache() -> dict[str, Any]:
    """Full background refresh: API news + top RSS feeds → Redis.

    Called on startup and every 5 minutes by the background task.
    Returns the cached data dict.
    """
    logger.info("Starting background news cache refresh...")

    # Fast API news first (instant)
    api_articles = await _fetch_fast_api_news(limit=25)
    logger.info(f"API news: {len(api_articles)} articles")

    # RSS feeds in background (with generous timeout per batch)
    try:
        rss_articles = await asyncio.wait_for(
            _fetch_rss_articles(TOP_50_FEEDS, limit_per_source=3, timeout=8.0),
            timeout=60.0,
        )
        logger.info(f"RSS news: {len(rss_articles)} articles")
    except TimeoutError:
        logger.warning("RSS fetch timed out after 60s — using partial results")
        rss_articles = []
    except Exception as e:
        logger.warning(f"RSS fetch failed: {e}")
        rss_articles = []

    # Merge and dedupe by title
    seen_titles: set[str] = set()
    all_articles: list[dict[str, Any]] = []
    for art in api_articles + rss_articles:
        title = art.get("title", "")
        if title and title not in seen_titles:
            seen_titles.add(title)
            all_articles.append(art)
        elif not title:
            # Allow articles without title (unusual but safe)
            all_articles.append(art)

    # Sort by published_at (newest first)
    def _sort_key(a: dict[str, Any]) -> str:
        return a.get("published_at", "")

    all_articles.sort(key=_sort_key, reverse=True)

    # Build the full news data
    market_brief = await get_market_brief()

    data = {
        "market_brief": market_brief,
        "headlines": {
            "articles": all_articles[:50],
            "total": len(all_articles),
            "source": "rmi_news_aggregator",
            "free": True,
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "data_sources": [
            "CoinGecko (news API, prices, trending)",
            "CryptoPanic (news sentiment)",
            "Alternative.me (Fear & Greed Index)",
            "Polymarket (prediction markets)",
            f"RSS ({len(TOP_50_FEEDS)} feeds: CoinTelegraph, Decrypt, The Block, etc.)",
        ],
        "source": "full_news_feed",
        "free": True,
    }

    # Write to Redis
    await _redis_set(NEWS_CACHE_KEY, data, NEWS_CACHE_TTL)
    logger.info(f"News cache refreshed: {len(all_articles)} total articles from APIs + RSS")

    return data


# ── 8. PUBLIC API: get_aggregated_news (used by DataBus) ──────────


async def get_aggregated_news(limit: int = 20, category: str = "", **kw) -> dict[str, Any] | None:
    """Get news articles. Reads from Redis cache (instant)."""
    cached = await _redis_get(NEWS_CACHE_KEY)
    if cached:
        headlines = cached.get("headlines", {})
        articles = headlines.get("articles", []) if isinstance(headlines, dict) else []
        if category:
            articles = [
                a for a in articles if category.lower() in (a.get("category", "") + a.get("source", "")).lower()
            ]
        articles = articles[:limit]
        return {
            "articles": articles,
            "total": len(articles),
            "filtered": len(articles),
            "source": "rmi_news_aggregator",
            "cached": True,
            "free": True,
        }

    # Cold cache — return empty gracefully
    return {
        "articles": [],
        "total": 0,
        "filtered": 0,
        "source": "rmi_news_aggregator",
        "cached": False,
        "free": True,
    }


# ── 9. PUBLIC API: get_full_news_feed (main endpoint) ─────────────


async def get_full_news_feed(limit: int = 15, **kw) -> dict[str, Any] | None:
    """Complete news page data. ALWAYS reads from Redis.

    On cache miss, returns fast API data + market brief immediately
    and triggers background refresh.
    """
    # Try Redis cache first
    cached = await _redis_get(NEWS_CACHE_KEY)
    if cached:
        # Trim headlines to requested limit
        headlines = cached.get("headlines", {})
        if isinstance(headlines, dict) and "articles" in headlines:
            headlines["articles"] = headlines["articles"][:limit]
            headlines["total"] = min(headlines.get("total", 0), limit)
        cached["headlines"] = headlines
        return cached

    # Cache miss — do a fast response with API news only
    logger.info("News cache miss — returning fast API data + triggering background refresh")

    # Kick off background refresh
    _ = asyncio.create_task(refresh_news_cache())

    # Get market brief (fast, parallel)
    market_brief_task = asyncio.create_task(get_market_brief())

    # Fast API news (3-5 seconds)
    try:
        api_articles = await asyncio.wait_for(
            _fetch_fast_api_news(limit=20),
            timeout=5.0,
        )
    except (TimeoutError, Exception):
        api_articles = []

    market_brief = await market_brief_task

    return {
        "market_brief": market_brief,
        "headlines": {
            "articles": api_articles[:limit],
            "total": len(api_articles),
            "source": "rmi_news_aggregator",
            "free": True,
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "data_sources": [
            "CoinGecko (news API, prices, trending)",
            "CryptoPanic (news sentiment)",
            "Alternative.me (Fear & Greed Index)",
            "Polymarket (prediction markets)",
            f"RSS ({len(TOP_50_FEEDS)} feeds — refreshing in background)",
        ],
        "source": "full_news_feed",
        "free": True,
        "refreshing": True,
    }
