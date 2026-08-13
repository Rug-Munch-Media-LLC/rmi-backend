"""
Prediction Market — Main Service
Multi-source prediction market data aggregation with parallel fetching + caching.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domains.prediction.prediction_market_package.cache import (
    cache_hash,
    dict_to_digest,
    dict_to_market,
    digest_to_dict,
    market_to_dict,
)
from app.domains.prediction.prediction_market_package.models import (
    PredictionDigest,
    PredictionMarket,
)
from app.domains.prediction.prediction_market_package.sources import (
    kalshi as kalshi_source,
    limitless as limitless_source,
    manifold as manifold_source,
    polymarket as polymarket_source,
)

logger = get_logger("prediction_market")

SECURITY_QUERIES = [
    "crypto hack exploit scam",
    "defi rug pull fraud",
    "exchange insolvent breach",
    "stablecoin depeg collapse",
    "crypto regulation SEC enforcement",
    "blockchain vulnerability zero-day",
]

SOURCE_NAMES = ["polymarket", "kalshi", "limitless", "manifold"]


class PredictionMarketService:
    """Multi-source prediction market data with parallel fetching + caching."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(timeout=15.0)
        self._redis: Any = None  # Lazy init via _get_redis()

    def _get_redis(self) -> Any:
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True, socket_connect_timeout=3
            )
            if self._redis is None:
                self._redis = False
        except (httpx.HTTPError, ConnectionError, OSError, ValueError) as e:
            logger.debug("_get_redis_error", exc_info=True)
            logger.warning(f"Redis unavailable, caching disabled: {e}")
            self._redis = False
        return self._redis if self._redis is not False else None

    async def search(
        self, query: str, categories: list[str] | None = None, min_volume: float = 0, security_only: bool = False
    ) -> list[PredictionMarket]:
        cache_key = f"predmkt:search:{cache_hash(query, categories, min_volume, security_only)}"
        redis = self._get_redis()
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    return [dict_to_market(d) for d in json.loads(cached)]
            except (ConnectionError, OSError, json.JSONDecodeError):
                pass

        results = await asyncio.gather(
            polymarket_source.search(self._http, query),
            kalshi_source.search(self._http, query),
            limitless_source.search(self._http, query),
            manifold_source.search(self._http, query),
            return_exceptions=True,
        )

        all_markets: list[PredictionMarket] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"{SOURCE_NAMES[i]} search failed: {result}")
                continue
            if isinstance(result, list):
                all_markets.extend(result)

        if min_volume > 0:
            all_markets = [m for m in all_markets if m.volume_usd >= min_volume]
        if security_only:
            all_markets = [m for m in all_markets if m.is_security_relevant]
        all_markets.sort(key=lambda m: m.volume_usd, reverse=True)

        if redis:
            try:
                await redis.setex(cache_key, 300, json.dumps([market_to_dict(m) for m in all_markets[:50]]))
            except (
                httpx.HTTPError,
                json.JSONDecodeError,
                ConnectionError,
                OSError,
                ValueError,
                KeyError,
                TypeError,
            ) as e:
                logger.debug("search_error", exc_info=True)
                logger.warning(f"Redis cache write failed: {e}")
        return all_markets

    async def token_markets(self, symbol: str) -> list[PredictionMarket]:
        results = await self.search(f'"{symbol}" token crypto', security_only=False)
        symbol_lower = symbol.lower()
        return [m for m in results if symbol_lower in m.question.lower()]

    async def security_digest(self) -> PredictionDigest:
        cache_key = f"predmkt:digest:{datetime.now(UTC).strftime('%Y-%m-%d')}"
        redis = self._get_redis()
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    return dict_to_digest(json.loads(cached))
            except (ConnectionError, OSError, json.JSONDecodeError):
                pass

        all_markets: list[PredictionMarket] = []
        searches = [self.search(q, security_only=False) for q in SECURITY_QUERIES]
        results = await asyncio.gather(*searches, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                all_markets.extend(result)

        seen_questions = set()
        unique_markets = []
        for m in all_markets:
            q_key = m.question.lower().strip()[:80]
            if q_key not in seen_questions:
                seen_questions.add(q_key)
                unique_markets.append(m)

        top_threats = sorted(
            [m for m in unique_markets if m.is_security_relevant and m.volume_usd > 10000],
            key=lambda m: m.volume_usd,
            reverse=True,
        )
        token_specific = sorted(
            [
                m
                for m in unique_markets
                if m.is_crypto_relevant and m.is_security_relevant and len(m.tokens_mentioned) > 0
            ],
            key=lambda m: m.volume_usd,
            reverse=True,
        )
        ecosystem_risk = sorted(
            [m for m in unique_markets if m.is_crypto_relevant and not m.is_security_relevant and m.volume_usd > 50000],
            key=lambda m: m.volume_usd,
            reverse=True,
        )
        regulatory = sorted(
            [
                m
                for m in unique_markets
                if m.is_security_relevant
                and any(kw in m.question.lower() for kw in ["sec", "cftc", "doj", "regulation", "sanction", "ban"])
            ],
            key=lambda m: m.volume_usd,
            reverse=True,
        )

        digest = PredictionDigest(
            generated_at=datetime.now(UTC).isoformat(),
            total_markets_searched=len(unique_markets),
            security_relevant_count=len([m for m in unique_markets if m.is_security_relevant]),
            crypto_relevant_count=len([m for m in unique_markets if m.is_crypto_relevant]),
            top_threats=top_threats[:20],
            token_specific_markets=token_specific[:20],
            ecosystem_risk_markets=ecosystem_risk[:10],
            regulatory_markets=regulatory[:10],
        )

        if redis:
            try:
                await redis.setex(cache_key, 3600, json.dumps(digest_to_dict(digest)))
            except (httpx.HTTPError, json.JSONDecodeError, ConnectionError, OSError, ValueError) as e:
                logger.debug("unknown_error", exc_info=True)
                logger.warning(f"Redis digest cache write failed: {e}")
        return digest

    async def trending(self, limit: int = 20, source: str | None = None) -> list[PredictionMarket]:
        tasks: list[Any] = []
        if not source or source == "polymarket":
            tasks.append(polymarket_source.trending(self._http, limit))
        else:
            tasks.append(asyncio.sleep(0))
        if not source or source == "kalshi":
            tasks.append(kalshi_source.trending(self._http, limit))
        else:
            tasks.append(asyncio.sleep(0))
        if not source or source == "limitless":
            tasks.append(limitless_source.trending(self._http, limit))
        else:
            tasks.append(asyncio.sleep(0))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_markets = []
        for result in results:
            if isinstance(result, list):
                all_markets.extend(result)
        all_markets.sort(key=lambda m: m.volume_usd, reverse=True)
        return all_markets[:limit]

    async def market_detail(self, source: str, market_id: str) -> PredictionMarket | None:
        if source == "polymarket":
            return await polymarket_source.detail(self._http, market_id)
        elif source == "kalshi":
            return await kalshi_source.detail(self._http, market_id)
        return None


# Singleton
_service: PredictionMarketService | None = None


def get_prediction_market_service() -> PredictionMarketService:
    global _service
    if _service is None:
        _service = PredictionMarketService()
    return _service
