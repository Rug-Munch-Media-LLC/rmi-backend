"""News and market data fetching: CoinGecko, Alternative.me, Polymarket,
CryptoPanic, and RSS feeds."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from app.core.logging import get_logger
from app.databus.news_provider.cache import _cache_set, _cached
from app.databus.news_provider.helpers import (
    COINGECKO_BASE,
    COINGECKO_KEY,
    CRYPTOPANIC_API_KEY,
    FEAR_GREED_URL,
    POLYMARKET_URL,
    hashlib_quick,
)

logger = get_logger("news_provider")


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

    def _extract_image(entry: Any) -> str | None:
        """Pull the best article image from an RSS entry.

        Order: media:content → media:thumbnail → enclosure link → first
        <img> in summary/description/content:encoded HTML. Most major
        crypto feeds (CoinDesk, Cointelegraph, Decrypt, The Block) expose
        one of these. Widget/embed URLs (e.g. stock chart iframes) are
        rejected — they render as broken images in cards.
        """
        import re as _re

        def _acceptable(url: str) -> bool:
            lowered = url.lower()
            return not any(marker in lowered for marker in ("/embed", "embed?", "&embed", "/widget", "/chart/"))

        for media in getattr(entry, "media_content", None) or []:
            if isinstance(media, dict) and media.get("url"):
                mtype = str(media.get("type") or "image")
                url = str(media["url"])
                if _acceptable(url) and ("image" in mtype or mtype.startswith("http")):
                    return url
        for thumb in getattr(entry, "media_thumbnail", None) or []:
            if isinstance(thumb, dict) and thumb.get("url") and _acceptable(str(thumb["url"])):
                return str(thumb["url"])
        for link in getattr(entry, "links", None) or []:
            if (
                isinstance(link, dict)
                and link.get("rel") == "enclosure"
                and "image" in str(link.get("type") or "")
                and _acceptable(str(link.get("href") or ""))
            ):
                return str(link.get("href") or "")
        html = f"{entry.get('summary', '') or ''}{entry.get('description', '') or ''}"
        # content:encoded — most crypto feeds embed the lead image here
        content = getattr(entry, "content", None) or []
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("value"):
                    html += str(c["value"])
        if "<img" in html:
            for match in _re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html):
                if _acceptable(match.group(1)):
                    return match.group(1)
        return None

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
        og_missing: list[int] = []
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
                image_url = _extract_image(entry)
                if not image_url and link:
                    # og:image fallback (bounded, fail-open) — resolved after
                    # the loop so the feed parse stays fast.
                    og_missing.append(len(result))
                result.append(
                    {
                        "id": f"rss-{hashlib_quick(title + link)}",
                        "title": title,
                        "url": link,
                        "description": desc[:300],
                        "source": source_name,
                        "published_at": published,
                        "image_url": image_url,
                        "category": "news",
                        "sentiment": "neutral",
                        "kind": "external",
                        "tier": "news",
                    }
                )
            except Exception:
                logger.debug("RSS entry skipped source=%s title=%r", source_name, title[:80])
                continue
        if og_missing:
            try:
                from app.domains.news.og_image import gather_og_images

                og_imgs = await gather_og_images([result[i]["url"] for i in og_missing])
                for i, img in zip(og_missing, og_imgs, strict=True):
                    if img:
                        result[i]["image_url"] = img
            except Exception:
                logger.debug("og_image_fallback_failed source=%s", source_name)
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
    """Full background refresh: API news + RSS feeds → Redis.

    Called on startup and every 5 minutes by the background task.
    Returns the cached data dict.

    Pipeline (RAG-benefits-news): pull from the merged master registry
    (~200 sources) → fuzzy-dedupe near-identical wire copies → rerank the
    freshest candidates with the local Qwen3-Reranker-4B so the feed is
    ordered by market relevance, not just recency. Fail-open everywhere:
    rerank/dedup degradation never empties the feed.
    """
    from app.databus.news_provider.cache import NEWS_CACHE_KEY, _redis_set
    from app.databus.news_provider.helpers import NEWS_CACHE_TTL, TOP_50_FEEDS
    from app.databus.news_provider.normalizers import (
        dedupe_articles,
        relevance_rerank,
        sort_by_published,
    )

    logger.info("Starting background news cache refresh...")

    # Fast API news first (instant)
    api_articles = await _fetch_fast_api_news(limit=25)
    logger.info(f"API news: {len(api_articles)} articles")

    # Merge curated fast list with the master registry (200+ sources),
    # dedupe by URL, cap for the refresh budget.
    try:
        from app.domains.news.news_service.sources import RSS_FEEDS as MASTER_FEEDS

        seen_urls = {u for u, _ in TOP_50_FEEDS}
        merged_feeds = list(TOP_50_FEEDS) + [(u, n) for u, n in MASTER_FEEDS if u not in seen_urls]
        rss_registry = merged_feeds[:140]
    except Exception:
        rss_registry = TOP_50_FEEDS

    # RSS feeds in background (with generous timeout per batch)
    try:
        rss_articles = await asyncio.wait_for(
            _fetch_rss_articles(rss_registry, limit_per_source=3, timeout=8.0),
            timeout=60.0,
        )
        logger.info(f"RSS news: {len(rss_articles)} articles from {len(rss_registry)} feeds")
    except TimeoutError:
        logger.warning("RSS fetch timed out after 60s — using partial results")
        rss_articles = []
    except Exception as e:
        logger.warning(f"RSS fetch failed: {e}")
        rss_articles = []

    # Merge, dedupe, sort, and relevance-rank (fail-open passes)
    all_articles = sort_by_published(dedupe_articles(api_articles + rss_articles))
    all_articles = await relevance_rerank(all_articles)

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
