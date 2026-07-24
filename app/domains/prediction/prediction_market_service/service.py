"""Main prediction market service."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

from .keywords import _extract_token_symbols
from .models import PredictionDigest, PredictionMarket
from .utils import _cache_hash, _dict_to_digest, _dict_to_market, _digest_to_dict, _market_to_dict

logger = get_logger("prediction_market")

POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB = "https://clob.polymarket.com"
POLYMARKET_DATA = "https://data-api.polymarket.com"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
LIMITLESS_BASE = "https://api.limitless.exchange"
MANIFOLD_BASE = "https://api.manifold.markets/v0"

class PredictionMarketService:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(timeout=15.0)
        self._redis = None

    def _get_redis(self) -> Any:
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True, socket_connect_timeout=3)
            if self._redis is None:
                self._redis = False
        except Exception as e:
            logger.warning(f"Redis unavailable, caching disabled: {e}")
            self._redis = False
        return self._redis if self._redis is not False else None

    async def search(self, query: str, categories: list[str] | None = None, min_volume: float = 0, security_only: bool = False) -> list[PredictionMarket]:
        cache_key = f"predmkt:search:{_cache_hash(query, categories, min_volume, security_only)}"
        redis = self._get_redis()
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    return [_dict_to_market(d) for d in json.loads(cached)]
            except Exception:
                logger.debug("search_cache_read_failed")
        results: list[list[PredictionMarket]] = await asyncio.gather(self._search_polymarket(query), self._search_kalshi(query), self._search_limitless(query), self._search_manifold(query), return_exceptions=True)
        all_markets: list[PredictionMarket] = []
        sources = ["polymarket", "kalshi", "limitless", "manifold"]
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"{sources[i]} search failed: {result}")
            elif isinstance(result, list):
                all_markets.extend(result)
        if min_volume > 0:
            all_markets = [m for m in all_markets if m.volume_usd >= min_volume]
        if security_only:
            all_markets = [m for m in all_markets if m.is_security_relevant]
        all_markets.sort(key=lambda m: m.volume_usd, reverse=True)
        if redis:
            try:
                await redis.setex(cache_key, 300, json.dumps([_market_to_dict(m) for m in all_markets[:50]]))
            except Exception as e:
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
                    return _dict_to_digest(json.loads(cached))
            except Exception:
                logger.debug("digest_cache_read_failed")
        security_queries = ["crypto hack exploit scam", "defi rug pull fraud", "exchange insolvent breach", "stablecoin depeg collapse", "crypto regulation SEC enforcement", "blockchain vulnerability zero-day"]
        all_markets: list[PredictionMarket] = []
        searches = [self.search(q, security_only=False) for q in security_queries]
        results = await asyncio.gather(*searches, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                all_markets.extend(result)
        seen_questions = set()
        unique_markets = []
        for m in all_markets:
            q_key = m.question.lower().strip()[:80]
            if q_key not in seen_questions: seen_questions.add(q_key)
            unique_markets.append(m)
        top_threats = sorted([m for m in unique_markets if m.is_security_relevant and m.volume_usd > 10000], key=lambda m: m.volume_usd, reverse=True)
        token_specific = sorted([m for m in unique_markets if m.is_crypto_relevant and m.is_security_relevant and len(m.tokens_mentioned) > 0], key=lambda m: m.volume_usd, reverse=True)
        ecosystem_risk = sorted([m for m in unique_markets if m.is_crypto_relevant and not m.is_security_relevant and m.volume_usd > 50000], key=lambda m: m.volume_usd, reverse=True)
        regulatory = sorted([m for m in unique_markets if m.is_security_relevant and any(kw in m.question.lower() for kw in ["sec","cftc","doj","regulation","sanction","ban"])], key=lambda m: m.volume_usd, reverse=True)
        digest = PredictionDigest(generated_at=datetime.now(UTC).isoformat(), total_markets_searched=len(unique_markets), security_relevant_count=len([m for m in unique_markets if m.is_security_relevant]), crypto_relevant_count=len([m for m in unique_markets if m.is_crypto_relevant]), top_threats=top_threats[:20], token_specific_markets=token_specific[:20], ecosystem_risk_markets=ecosystem_risk[:10], regulatory_markets=regulatory[:10])
        if redis:
            try:
                await redis.setex(cache_key, 3600, json.dumps(_digest_to_dict(digest)))
            except Exception as e:
                logger.warning(f"Redis digest cache write failed: {e}")
        return digest

    async def trending(self, limit: int = 20, source: str | None = None) -> list[PredictionMarket]:
        tasks = []
        if not source or source == "polymarket":
            tasks.append(self._trending_polymarket(limit))
        else:
            tasks.append(asyncio.sleep(0))
        if not source or source == "kalshi":
            tasks.append(self._trending_kalshi(limit))
        else:
            tasks.append(asyncio.sleep(0))
        if not source or source == "limitless":
            tasks.append(self._trending_limitless(limit))
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
            return await self._polymarket_detail(market_id)
        elif source == "kalshi":
            return await self._kalshi_detail(market_id)
        return None

    async def _search_polymarket(self, query: str) -> list[PredictionMarket]:
        try:
            resp = await self._http.get(f"{POLYMARKET_GAMMA}/public-search", params={"q": query}, timeout=10.0)
            if resp.status_code != 200:
                return []
            data = resp.json()
            events = data.get("events", [])
            markets = []
            for event in events[:
                10]:
                for m in event.get("markets", [])[:
                    5]:
                    pm = self._parse_polymarket_market(m, event)
                    if pm:
                        markets.append(pm)
            return markets
        except Exception as e: logger.warning(f"Polymarket search error: {e}")
        return []

    def _parse_polymarket_market(self, m: dict, event: dict | None = None) -> PredictionMarket | None:
        try:
            question = m.get("question", "")
            slug = m.get("slug", "")
            prices = self._parse_json_field(m.get("outcomePrices", "[]"))
            if isinstance(prices, list) and len(prices) >= 2:
                prob_yes, prob_no = float(prices[0]), float(prices[1])
            else:
                prob_yes = prob_no = 0.5
            volume = float(m.get("volume", 0))
            liquidity = float(m.get("liquidity", 0))
            tokens_mentioned = _extract_token_symbols(question)
            tags = []
            if event:
                tags.extend([t.get("label", "") for t in event.get("tags", [])])
            return PredictionMarket(source="polymarket", source_id=slug, question=question, slug=slug, probability_yes=prob_yes, probability_no=prob_no, volume_usd=volume, liquidity_usd=liquidity, category=m.get("category", event.get("category", "") if event else ""), tags=tags, tokens_mentioned=tokens_mentioned, url=f"https://polymarket.com/event/{slug}" if slug else "", ends_at=m.get("endDate", event.get("endDate", "") if event else ""), updated_at=datetime.now(UTC).isoformat())
        except Exception as e: logger.warning(f"Failed to parse Polymarket market: {e}")
        return None

    async def _trending_polymarket(self, limit: int) -> list[PredictionMarket]:
        try:
            resp = await self._http.get(f"{POLYMARKET_GAMMA}/events", params={"limit": limit, "active": "true", "closed": "false", "order": "volume", "ascending": "false"}, timeout=10.0)
            if resp.status_code != 200:
                return []
            events = resp.json()
            markets = []
            for event in events[:
                limit]:
                for m in event.get("markets", [])[:
                    3]:
                    pm = self._parse_polymarket_market(m, event)
                    if pm:
                        markets.append(pm)
            return markets
        except Exception as e: logger.warning(f"Polymarket trending error: {e}")
        return []

    async def _polymarket_detail(self, slug: str) -> PredictionMarket | None:
        try:
            resp = await self._http.get(f"{POLYMARKET_GAMMA}/markets", params={"slug": slug}, timeout=10.0)
            if resp.status_code != 200 or not resp.json():
                return None
            m = resp.json()[0]
            pm = self._parse_polymarket_market(m)
            if pm:
                tokens = self._parse_json_field(m.get("clobTokenIds", "[]"))
                if isinstance(tokens, list) and len(tokens) >= 2:
                    try:
                        price_resp = await self._http.get(f"{POLYMARKET_CLOB}/price", params={"token_id": tokens[0], "side": "buy"}, timeout=5.0)
                        if price_resp.status_code == 200:
                            pm.probability_yes = float(price_resp.json().get("price", pm.probability_yes))
                    except Exception:
                        logger.debug("polymarket_price_fetch_failed %s", slug)
            return pm
        except Exception as e: logger.warning(f"Polymarket detail error for {slug}: {e}")
        return None

    async def _search_kalshi(self, query: str) -> list[PredictionMarket]:
        try:
            resp = await self._http.get(f"{KALSHI_BASE}/events", params={"status": "open", "limit": 50}, headers={"Accept": "application/json"}, timeout=10.0)
            if resp.status_code != 200:
                return []
            data = resp.json()
            events = data.get("events", [])
            query_lower = query.lower()
            query_terms = query_lower.split()
            results = []
            for i, event in enumerate(events[:
                10]):
                event_title = event.get("title", "").lower()
                event_ticker = event.get("ticker", "")
                if not any(term in event_title for term in query_terms):
                    continue
                if i > 0:
                    await asyncio.sleep(0.3)
                try:
                    mr = await self._http.get(f"{KALSHI_BASE}/markets", params={"event_ticker": event_ticker, "status": "open", "limit": 10}, headers={"Accept": "application/json"}, timeout=8.0)
                    if mr.status_code == 200:
                        for m in mr.json().get("markets", []):
                            pm = self._parse_kalshi_market(m)
                            if pm:
                                results.append(pm)
                except Exception:
                    logger.debug("kalshi_market_fetch_failed %s", event_ticker)
                    continue
            return results
        except Exception as e: logger.warning(f"Kalshi search error: {e}")
        return []

    def _parse_kalshi_market(self, m: dict) -> PredictionMarket | None:
        try:
            ticker = m.get("ticker", "")
            title = m.get("title", "")
            yes_bid = float(m.get("yes_bid_dollars", 0))
            volume = float(m.get("volume_fp", 0))
            category = m.get("category", "")
            if yes_bid <= 0 or ",yes " in title.lower():
                return None
            prob_yes = yes_bid
            prob_no = 1.0 - prob_yes if prob_yes else 0.5
            tokens_mentioned = _extract_token_symbols(title)
            return PredictionMarket(source="kalshi", source_id=ticker, question=title, slug=ticker, probability_yes=prob_yes, probability_no=prob_no, volume_usd=volume, category=category, tokens_mentioned=tokens_mentioned, url=f"https://kalshi.com/markets/{ticker}" if ticker else "", updated_at=datetime.now(UTC).isoformat())
        except Exception as e: logger.warning(f"Failed to parse Kalshi market: {e}")
        return None

    async def _trending_kalshi(self, limit: int) -> list[PredictionMarket]:
        try:
            resp = await self._http.get(f"{KALSHI_BASE}/events", params={"status": "open", "limit": min(limit * 2, 30)}, timeout=10.0)
            if resp.status_code != 200:
                return []
            events = resp.json().get("events", [])
            results = []
            for event in events[:
                limit]:
                event_ticker = event.get("ticker", "")
                try:
                    mr = await self._http.get(f"{KALSHI_BASE}/markets", params={"event_ticker": event_ticker, "status": "open", "limit": 5}, timeout=8.0)
                    if mr.status_code == 200:
                        for m in mr.json().get("markets", []):
                            pm = self._parse_kalshi_market(m)
                            if pm:
                                results.append(pm)
                except Exception:
                    logger.debug("kalshi_trending_market_failed %s", event_ticker)
                    continue
            return results[:limit]
        except Exception as e: logger.warning(f"Kalshi trending error: {e}")
        return []

    async def _kalshi_detail(self, ticker: str) -> PredictionMarket | None:
        try:
            resp = await self._http.get(f"{KALSHI_BASE}/markets/{ticker}/orderbook", timeout=10.0)
            if resp.status_code != 200:
                return None
            data = resp.json()
            orderbook = data.get("orderbook_fp", {})
            yes_bids = orderbook.get("yes_dollars", [])
            best_yes = float(yes_bids[0][0]) if yes_bids else 0.5
            meta_resp = await self._http.get(f"{KALSHI_BASE}/markets", params={"ticker": ticker}, timeout=10.0)
            if meta_resp.status_code == 200:
                markets = meta_resp.json().get("markets", [])
                if markets:
                    pm = self._parse_kalshi_market(markets[0])
                    if pm: pm.probability_yes = best_yes
                    pm.probability_no = 1.0 - best_yes
                    return pm
            return None
        except Exception as e: logger.warning(f"Kalshi detail error for {ticker}: {e}")
        return None

    async def _search_limitless(self, query: str) -> list[PredictionMarket]:
        try:
            resp = await self._http.get(f"{LIMITLESS_BASE}/markets/active", params={"limit": 25}, headers={"Accept": "application/json"}, timeout=10.0)
            if resp.status_code != 200:
                return []
            data = resp.json()
            all_markets = data.get("data", [])
            query_lower = query.lower()
            query_terms = query_lower.split()
            results = []
            for m in all_markets:
                title = m.get("title", "").lower()
                if any(term in title for term in query_terms):
                    pm = self._parse_limitless_market(m)
                    if pm:
                        results.append(pm)
            return results
        except Exception as e: logger.warning(f"Limitless search error: {e}")
        return []

    def _parse_limitless_market(self, m: dict) -> PredictionMarket | None:
        try:
            title = m.get("title", "")
            slug = m.get("slug", str(m.get("id", "")))
            prices = m.get("prices", [50, 50])
            prob_yes = float(prices[0]) / 100 if isinstance(prices, list) and len(prices) >= 1 else 0.5
            prob_no = float(prices[1]) / 100 if isinstance(prices, list) and len(prices) >= 2 else 0.5
            vol_str = m.get("volumeFormatted", str(m.get("volume", 0)))
            volume = float(vol_str) if vol_str else 0.0
            categories = m.get("categories", [])
            category = categories[0] if categories else ""
            tags = m.get("tags", [])
            tokens_mentioned = _extract_token_symbols(title)
            return PredictionMarket(source="limitless", source_id=str(slug), question=title, slug=str(slug), probability_yes=prob_yes, probability_no=prob_no, volume_usd=volume, category=category, tags=tags, tokens_mentioned=tokens_mentioned, url=f"https://limitless.exchange/markets/{slug}" if slug else "", ends_at=m.get("expirationDate", ""), updated_at=datetime.now(UTC).isoformat())
        except Exception as e: logger.warning(f"Failed to parse Limitless market: {e}")
        return None

    async def _trending_limitless(self, limit: int) -> list[PredictionMarket]:
        try:
            limit = min(limit, 25)
            resp = await self._http.get(f"{LIMITLESS_BASE}/markets/active", params={"limit": limit}, headers={"Accept": "application/json"}, timeout=10.0)
            if resp.status_code != 200:
                return []
            data = resp.json()
            markets = data.get("data", [])
            return [pm for m in markets[:limit] if (pm := self._parse_limitless_market(m))]
        except Exception as e: logger.warning(f"Limitless trending error: {e}")
        return []

    async def _search_manifold(self, query: str) -> list[PredictionMarket]:
        try:
            resp = await self._http.get(f"{MANIFOLD_BASE}/search-markets", params={"term": query, "limit": 20}, timeout=10.0)
            if resp.status_code != 200:
                return []
            data = resp.json()
            contracts = data if isinstance(data, list) else data.get("contracts", data.get("markets", []))
            results = []
            for c in contracts[:
                10]:
                pm = self._parse_manifold_market(c)
                if pm:
                    results.append(pm)
            return results
        except Exception as e: logger.warning(f"Manifold search error: {e}")
        return []

    def _parse_manifold_market(self, c: dict) -> PredictionMarket | None:
        try:
            question = c.get("question", "")
            slug = c.get("slug", c.get("id", ""))
            prob = float(c.get("probability", c.get("prob", 0.5)))
            volume = float(c.get("volume", c.get("volume24Hours", 0)))
            tokens_mentioned = _extract_token_symbols(question)
            tags = list(c.get("tags", []))
            return PredictionMarket(source="manifold", source_id=str(slug), question=question, slug=str(slug), probability_yes=prob, probability_no=1.0-prob, volume_usd=volume, category="", tags=tags, tokens_mentioned=tokens_mentioned, url=f"https://manifold.markets/{c.get('creatorUsername','')}/{slug}" if slug else "", updated_at=datetime.now(UTC).isoformat())
        except Exception as e: logger.warning(f"Failed to parse Manifold market: {e}")
        return None

    @staticmethod
    def _parse_json_field(val) -> Any:
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return val
        return val

_service: PredictionMarketService | None = None

def get_prediction_market_service() -> PredictionMarketService:
    global _service
    if _service is None:
        _service = PredictionMarketService()
    return _service
