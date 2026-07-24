"""
News Service — Core Aggregation Service
=========================================
Thin NewsService class that delegates fetching to fetchers module.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.news.news_service import fetchers_api, fetchers_rss
from app.domains.news.news_service.sources import BEARISH_WORDS, BULLISH_WORDS, CATEGORY_KEYWORDS

logger = get_logger(__name__)

class NewsService:
    """Multi-source crypto news aggregator - 170+ sources, real data only."""

    def __init__(self) -> None:
        self.cache: dict[str, Any] = {}
        self.cache_ttl = 180
        self.last_fetch: datetime | None = None
        self.seen_hashes: set = set()
        self._sentiment_cache: dict[str, str] = {}

    # ─── SENTIMENT ANALYSIS ───────────────────────────────────────
    def _analyze_sentiment(self, text: str) -> str:
        if not text:
            return "neutral"
        text_lower = text.lower()
        cache_key = hashlib.sha256(text_lower[:200].encode()).hexdigest()
        if cache_key in self._sentiment_cache:
            return self._sentiment_cache[cache_key]
        bull_score = sum(1 for w in BULLISH_WORDS if w in text_lower)
        bear_score = sum(1 for w in BEARISH_WORDS if w in text_lower)
        if bull_score > bear_score + 2:
            result = "bullish"
        elif bear_score > bull_score + 2:
            result = "bearish"
        elif bull_score > bear_score:
            result = "slightly_bullish"
        elif bear_score > bull_score:
            result = "slightly_bearish"
        else:
            result = "neutral"
        if len(self._sentiment_cache) > 5000:
            self._sentiment_cache.clear()
        self._sentiment_cache[cache_key] = result
        return result

    # ─── CATEGORIZATION ───────────────────────────────────────────
    def _categorize(self, text: str) -> str:
        if not text:
            return "general"
        text_lower = text.lower()
        scores = {cat: sum(1 for kw in kws if kw in text_lower) for cat, kws in CATEGORY_KEYWORDS.items()}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "general"

    def _categorize_reddit(self, subreddit: str, text: str) -> str:
        sub_cats = {
            "CryptoCurrency": "market", "CryptoMarkets": "market", "ethdev": "protocol",
            "CryptoTechnology": "protocol", "defi": "defi", "ethfinance": "defi",
            "solana": "protocol", "CryptoScams": "security", "Bitcoin": "market",
            "ethereum": "protocol", "NFT": "nft_gaming", "web3": "protocol",
            "layer2": "layer2", "CryptoGaming": "nft_gaming",
        }
        return sub_cats.get(subreddit, self._categorize(text))

    # ─── FETCH DELEGATION ─────────────────────────────────────────
    async def _fetch_rss(self, url: str, source_name: str, limit: int = 5, use_proxy: bool = False) -> list[dict[str, Any]]:
        return await fetchers_rss.fetch_rss(self, url, source_name, limit, use_proxy)

    async def _fetch_all_rss(self, limit_per_source: int = 3) -> list[dict[str, Any]]:
        return await fetchers_rss.fetch_all_rss(self, limit_per_source)

    async def _fetch_reddit_hot(self, subreddit: str, limit: int = 5) -> list[dict[str, Any]]:
        return await fetchers_rss.fetch_reddit_hot(self, subreddit, limit)

    async def _fetch_all_reddit(self) -> list[dict[str, Any]]:
        return await fetchers_rss.fetch_all_reddit(self)

    async def _fetch_coingecko(self, limit: int = 15) -> list[dict[str, Any]]:
        return await fetchers_api.fetch_coingecko(self, limit)

    async def _fetch_cryptopanic(self, limit: int = 15) -> list[dict[str, Any]]:
        return await fetchers_api.fetch_cryptopanic(self, limit)

    async def _fetch_rmi_scanner(self) -> list[dict[str, Any]]:
        return await fetchers_api.fetch_rmi_scanner(self)

    async def _fetch_rmi_alerts(self) -> list[dict[str, Any]]:
        return await fetchers_api.fetch_rmi_alerts(self)

    async def _fetch_ghost(self, limit: int = 5) -> list[dict[str, Any]]:
        return await fetchers_api.fetch_ghost(self, limit)

    # ─── MAIN AGGREGATOR ──────────────────────────────────────────
    async def fetch_all(
        self, limit: int = 100, include_rss: bool = True, include_reddit: bool = True,
        include_api: bool = True, include_internal: bool = True,
    ) -> dict[str, Any]:
        """Fetch from all sources. Heavy operation - call sparingly."""
        all_sources = []
        tasks, task_labels = [], []
        if include_rss:
            tasks.append(self._fetch_all_rss(3)); task_labels.append("RSS")
        if include_reddit:
            tasks.append(self._fetch_all_reddit()); task_labels.append("Reddit")
        if include_api:
            tasks.append(self._fetch_coingecko(15)); task_labels.append("CoinGecko")
            tasks.append(self._fetch_cryptopanic(15)); task_labels.append("CryptoPanic")
        if include_internal:
            tasks.append(self._fetch_ghost(5)); task_labels.append("Ghost")
            tasks.append(self._fetch_rmi_scanner()); task_labels.append("Scanner")
            tasks.append(self._fetch_rmi_alerts()); task_labels.append("Alerts")

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for label, result in zip(task_labels, results, strict=False):
            if isinstance(result, list):
                all_sources.extend(result)
                logger.info(f"  {label}: {len(result)} items")
            else:
                logger.warning(f"  {label}: failed")

        seen = set()
        unique = []
        for a in all_sources:
            aid = a.get("id", "")
            if aid not in seen:
                seen.add(aid)
                unique.append(a)

        def parse_ts(a: dict) -> Any:
            try:
                return datetime.fromisoformat(a.get("published_at", "2020-01-01").replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return datetime(2020, 1, 1, tzinfo=UTC)

        unique.sort(key=parse_ts, reverse=True)
        sources = sorted({a["source"] for a in unique})
        result = unique[:limit]

        sentiment_counts = {"bullish": 0, "bearish": 0, "neutral": 0, "slightly_bullish": 0, "slightly_bearish": 0}
        for a in result:
            s = a.get("sentiment", "neutral")
            sentiment_counts[s] = sentiment_counts.get(s, 0) + 1

        return {
            "status": "success", "articles": result, "total": len(result),
            "sources": sources, "source_count": len(sources),
            "sentiment_summary": sentiment_counts,
            "tiers": sorted({a.get("tier", "unknown") for a in result}),
            "cached": False, "fetched_at": datetime.now(UTC).isoformat(),
        }

# ─── Singleton ───────────────────────────────────────────────────
_news_service: NewsService | None = None

def get_news_service() -> NewsService:
    global _news_service
    if _news_service is None:
        _news_service = NewsService()
    return _news_service
