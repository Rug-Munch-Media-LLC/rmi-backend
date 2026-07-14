"""
RMI Prediction Market Intelligence Service
===========================================
Multi-source prediction market data aggregation for crypto security intelligence.

Data sources (all free, zero auth for read-only):
  - Polymarket: Gamma API (search/discovery), CLOB API (prices/history), Data API (trades)
  - Kalshi: REST API /series, /markets, /events, /orderbook (unauthenticated)
  - Limitless: REST API /markets (Base chain, crypto-native)
  - Manifold: REST API /markets (play-money, open-source sentiment signals)

Open-source reference implementations (GitHub):
  - homerun (braedonsaunders/homerun): Open-source prediction market platform for
    Polymarket + Kalshi. Python strategies, backtesting, data sources, live trading.
  - prediction-market-edge-bot: SX Bet + Polymarket aggregator with smart order routing.
  - Awesome-Prediction-Market-Tools (aarora4): Curated directory of 50+ tools
    including Oddpool (cross-venue aggregator), analytics dashboards, trading bots.

Architecture:
  - Direct external API calls (NEVER route through own API - anti-circular-dependency rule)
  - All 4 sources queried in parallel with individual try/except
  - Results normalized into unified PredictionMarket dataclass
  - Redis caching: 30s TTL prices, 5min searches, 1hr digests

Integration points:
  - SENTINEL scanner: cross-reference token risk scores with market probability
  - Wallet Memory Bank: entity/deployer reputation from prediction market odds
  - RugMaps: visual correlation between market odds and on-chain wallet clusters
  - x402 tools: expose as paid security intelligence endpoints

Pitfalls:
  - Polymarket Gamma API double-encodes outcomePrices/clobTokenIds as JSON strings
  - One source timeout must not kill the entire call - individual try/except per source
  - Prediction market data is probabilistic, not definitive - always cross-reference
    with on-chain scanner results
  - Don't poll every market every tick - use targeted search + category filters
"""

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

logger = logging.getLogger("prediction_market")

# ── API Endpoints ────────────────────────────────────────────────

POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB = "https://clob.polymarket.com"
POLYMARKET_DATA = "https://data-api.polymarket.com"

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

LIMITLESS_BASE = "https://api.limitless.exchange"

MANIFOLD_BASE = "https://api.manifold.markets/v0"

# ── Category mappings for security relevance ────────────────────

SECURITY_KEYWORDS = [
    "hack",
    "exploit",
    "rug",
    "scam",
    "fraud",
    "breach",
    "leak",
    "drain",
    "phish",
    "backdoor",
    "vulnerability",
    "zero-day",
    "sanction",
    "indict",
    "arrest",
    "freeze",
    "seize",
    "clampdown",
    "depeg",
    "insolvent",
    "bankrupt",
    "collapse",
    "default",
    "theft",
    "heist",
    "compromise",
    "ransomware",
    "malware",
    "SEC",
    "CFTC",
    "DOJ",
    "FBI",
    "regulatory",
    "enforcement",
]

CRYPTO_KEYWORDS = [
    "bitcoin",
    "ethereum",
    "solana",
    "crypto",
    "defi",
    "token",
    "blockchain",
    "web3",
    "nft",
    "stablecoin",
    "usdt",
    "usdc",
    "dai",
    "exchange",
    "binance",
    "coinbase",
    "uniswap",
    "aave",
    "tether",
    "circle",
    "layer",
    "L1",
    "L2",
    "rollup",
    "bridge",
    "polygon",
    "arbitrum",
    "optimism",
    "avalanche",
    "fantom",
    "chainlink",
    "makerdao",
    "lido",
    "eigenlayer",
]

# ── Dataclasses ─────────────────────────────────────────────────


@dataclass
class PredictionMarket:
    """Unified prediction market result across all sources."""

    source: str  # "polymarket" | "kalshi" | "limitless" | "manifold"
    source_id: str  # native ID from source (slug, ticker, etc.)
    question: str
    slug: str
    probability_yes: float  # 0.0-1.0
    probability_no: float  # 0.0-1.0
    volume_usd: float
    liquidity_usd: float = 0.0
    category: str = ""
    tags: list[str] = field(default_factory=list)
    tokens_mentioned: list[str] = field(default_factory=list)
    is_security_relevant: bool = False
    is_crypto_relevant: bool = False
    url: str = ""
    ends_at: str | None = None
    updated_at: str = ""

    def __post_init__(self):
        """Auto-classify relevance based on keywords in question."""
        q_lower = self.question.lower()
        self.is_security_relevant = any(kw in q_lower for kw in SECURITY_KEYWORDS)
        self.is_crypto_relevant = any(kw in q_lower for kw in CRYPTO_KEYWORDS)


@dataclass
class PredictionDigest:
    """Daily intelligence digest of security-relevant prediction markets."""

    generated_at: str
    total_markets_searched: int
    security_relevant_count: int
    crypto_relevant_count: int
    top_threats: list[PredictionMarket] = field(default_factory=list)
    token_specific_markets: list[PredictionMarket] = field(default_factory=list)
    ecosystem_risk_markets: list[PredictionMarket] = field(default_factory=list)
    regulatory_markets: list[PredictionMarket] = field(default_factory=list)


# ── Service ─────────────────────────────────────────────────────


class PredictionMarketService:
    """Multi-source prediction market data with parallel fetching + caching."""

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._http = http_client or httpx.AsyncClient(timeout=15.0)
        self._redis = None  # Lazy init via get_redis()

    def _get_redis(self):
        """Lazy Redis connection for caching. Returns None if unavailable."""
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                decode_responses=True,
                socket_connect_timeout=3,
            )
            # Set to False if we couldn't actually connect
            if self._redis is None:
                self._redis = False
        except Exception as e:
            logger.warning(f"Redis unavailable, caching disabled: {e}")
            self._redis = False
        return self._redis if self._redis is not False else None

    # ── Public API ──────────────────────────────────────────

    async def search(
        self,
        query: str,
        categories: list[str] | None = None,
        min_volume: float = 0,
        security_only: bool = False,
    ) -> list[PredictionMarket]:
        """Search all prediction market sources in parallel.

        Args:
            query: Search term (token name, event, protocol, etc.)
            categories: Optional filter by source categories
            min_volume: Minimum USD volume to include
            security_only: Only return security-relevant markets
        """
        # Check Redis cache
        cache_key = f"predmkt:search:{_cache_hash(query, categories, min_volume, security_only)}"
        redis = self._get_redis()
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    markets_data = json.loads(cached)
                    return [_dict_to_market(d) for d in markets_data]
            except Exception:
                pass  # Cache miss or Redis error - fall through to live query

        # Fire all 4 sources in parallel
        results: list[list[PredictionMarket]] = await asyncio.gather(
            self._search_polymarket(query),
            self._search_kalshi(query),
            self._search_limitless(query),
            self._search_manifold(query),
            return_exceptions=True,
        )

        # Flatten and handle exceptions
        all_markets: list[PredictionMarket] = []
        sources = ["polymarket", "kalshi", "limitless", "manifold"]
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"{sources[i]} search failed: {result}")
                continue
            if isinstance(result, list):
                all_markets.extend(result)

        # Filter
        if min_volume > 0:
            all_markets = [m for m in all_markets if m.volume_usd >= min_volume]
        if security_only:
            all_markets = [m for m in all_markets if m.is_security_relevant]

        # Sort by volume descending
        all_markets.sort(key=lambda m: m.volume_usd, reverse=True)

        # Cache (5 min TTL)
        if redis:
            try:
                await redis.setex(cache_key, 300, json.dumps([_market_to_dict(m) for m in all_markets[:50]]))
            except Exception as e:
                logger.warning(f"Redis cache write failed: {e}")

        return all_markets

    async def token_markets(self, symbol: str) -> list[PredictionMarket]:
        """Find all prediction markets mentioning a specific token symbol."""
        results = await self.search(f'"{symbol}" token crypto', security_only=False)

        # Filter to markets actually about this token (mention in question)
        symbol_lower = symbol.lower()
        token_markets = [m for m in results if symbol_lower in m.question.lower()]
        return token_markets

    async def security_digest(self) -> PredictionDigest:
        """Generate daily intelligence digest of security-relevant prediction markets.

        Queries crypto + security keywords across all sources, categorizes
        results by threat type: top threats, token-specific, ecosystem risk,
        regulatory.
        """
        cache_key = f"predmkt:digest:{datetime.now(UTC).strftime('%Y-%m-%d')}"
        redis = self._get_redis()
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return _dict_to_digest(data)
            except Exception:
                pass  # Cache miss or Redis error - fall through to live query

        # Search for security-relevant crypto markets
        security_queries = [
            "crypto hack exploit scam",
            "defi rug pull fraud",
            "exchange insolvent breach",
            "stablecoin depeg collapse",
            "crypto regulation SEC enforcement",
            "blockchain vulnerability zero-day",
        ]

        all_markets: list[PredictionMarket] = []
        searches = [self.search(q, security_only=False) for q in security_queries]
        results = await asyncio.gather(*searches, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_markets.extend(result)

        # De-duplicate by question similarity
        seen_questions = set()
        unique_markets = []
        for m in all_markets:
            q_key = m.question.lower().strip()[:80]
            if q_key not in seen_questions:
                seen_questions.add(q_key)
                unique_markets.append(m)

        # Categorize
        top_threats = [m for m in unique_markets if m.is_security_relevant and m.volume_usd > 10000]
        top_threats.sort(key=lambda m: m.volume_usd, reverse=True)

        token_specific = [
            m for m in unique_markets if m.is_crypto_relevant and m.is_security_relevant and len(m.tokens_mentioned) > 0
        ]
        token_specific.sort(key=lambda m: m.volume_usd, reverse=True)

        ecosystem_risk = [
            m for m in unique_markets if m.is_crypto_relevant and not m.is_security_relevant and m.volume_usd > 50000
        ]
        ecosystem_risk.sort(key=lambda m: m.volume_usd, reverse=True)

        regulatory = [
            m
            for m in unique_markets
            if m.is_security_relevant
            and any(kw in m.question.lower() for kw in ["sec", "cftc", "doj", "regulation", "sanction", "ban"])
        ]
        regulatory.sort(key=lambda m: m.volume_usd, reverse=True)

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

        # Cache (1 hour)
        if redis:
            try:
                await redis.setex(cache_key, 3600, json.dumps(_digest_to_dict(digest)))
            except Exception as e:
                logger.warning(f"Redis digest cache write failed: {e}")

        return digest

    async def trending(self, limit: int = 20, source: str | None = None) -> list[PredictionMarket]:
        """Get top trending prediction markets by volume across all sources."""
        # Fetch top events from each source in parallel
        tasks = []
        if not source or source == "polymarket":
            tasks.append(self._trending_polymarket(limit))
        else:
            tasks.append(asyncio.sleep(0))  # placeholder

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
            elif isinstance(result, Exception):
                pass  # Individual source failures logged in _trending_* methods

        all_markets.sort(key=lambda m: m.volume_usd, reverse=True)
        return all_markets[:limit]

    async def market_detail(self, source: str, market_id: str) -> PredictionMarket | None:
        """Get detailed data for a specific market including orderbook."""
        if source == "polymarket":
            return await self._polymarket_detail(market_id)
        elif source == "kalshi":
            return await self._kalshi_detail(market_id)
        # Limitless and Manifold details on demand
        return None

    # ── Polymarket ──────────────────────────────────────────

    async def _search_polymarket(self, query: str) -> list[PredictionMarket]:
        """Search Polymarket Gamma API."""
        try:
            resp = await self._http.get(
                f"{POLYMARKET_GAMMA}/public-search",
                params={"q": query},
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning(f"Polymarket search returned {resp.status_code}")
                return []

            data = resp.json()
            events = data.get("events", [])
            markets = []

            for event in events[:10]:
                for m in event.get("markets", [])[:5]:
                    pm = self._parse_polymarket_market(m, event)
                    if pm:
                        markets.append(pm)

            return markets
        except Exception as e:
            logger.warning(f"Polymarket search error: {e}")
            return []

    def _parse_polymarket_market(self, m: dict, event: dict | None = None) -> PredictionMarket | None:
        """Parse a Polymarket market dict into unified PredictionMarket."""
        try:
            question = m.get("question", "")
            slug = m.get("slug", "")

            # Parse double-encoded JSON fields
            prices = self._parse_json_field(m.get("outcomePrices", "[]"))
            self._parse_json_field(m.get("outcomes", "[]"))
            self._parse_json_field(m.get("clobTokenIds", "[]"))

            if isinstance(prices, list) and len(prices) >= 2:
                prob_yes = float(prices[0])
                prob_no = float(prices[1])
            else:
                prob_yes = 0.5
                prob_no = 0.5

            volume = float(m.get("volume", 0))
            liquidity = float(m.get("liquidity", 0))

            # Extract token mentions from question
            tokens_mentioned = _extract_token_symbols(question)

            tags = []
            if event:
                tags.extend([t.get("label", "") for t in event.get("tags", [])])

            return PredictionMarket(
                source="polymarket",
                source_id=slug,
                question=question,
                slug=slug,
                probability_yes=prob_yes,
                probability_no=prob_no,
                volume_usd=volume,
                liquidity_usd=liquidity,
                category=m.get("category", event.get("category", "") if event else ""),
                tags=tags,
                tokens_mentioned=tokens_mentioned,
                url=f"https://polymarket.com/event/{slug}" if slug else "",
                ends_at=m.get("endDate", event.get("endDate", "") if event else ""),
                updated_at=datetime.now(UTC).isoformat(),
            )
        except Exception as e:
            logger.warning(f"Failed to parse Polymarket market: {e}")
            return None

    async def _trending_polymarket(self, limit: int) -> list[PredictionMarket]:
        """Get trending Polymarket events by volume."""
        try:
            resp = await self._http.get(
                f"{POLYMARKET_GAMMA}/events",
                params={
                    "limit": limit,
                    "active": "true",
                    "closed": "false",
                    "order": "volume",
                    "ascending": "false",
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                return []

            events = resp.json()
            markets = []
            for event in events[:limit]:
                for m in event.get("markets", [])[:3]:
                    pm = self._parse_polymarket_market(m, event)
                    if pm:
                        markets.append(pm)
            return markets
        except Exception as e:
            logger.warning(f"Polymarket trending error: {e}")
            return []

    async def _polymarket_detail(self, slug: str) -> PredictionMarket | None:
        """Get detailed Polymarket market data including CLOB prices."""
        try:
            # Fetch from Gamma
            resp = await self._http.get(
                f"{POLYMARKET_GAMMA}/markets",
                params={"slug": slug},
                timeout=10.0,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            if not data:
                return None

            m = data[0]
            pm = self._parse_polymarket_market(m)

            # Also fetch CLOB price for live data
            if pm:
                tokens = self._parse_json_field(m.get("clobTokenIds", "[]"))
                if isinstance(tokens, list) and len(tokens) >= 2:
                    try:
                        price_resp = await self._http.get(
                            f"{POLYMARKET_CLOB}/price",
                            params={"token_id": tokens[0], "side": "buy"},
                            timeout=5.0,
                        )
                        if price_resp.status_code == 200:
                            price_data = price_resp.json()
                            live_price = float(price_data.get("price", pm.probability_yes))
                            pm.probability_yes = live_price
                            pm.probability_no = 1.0 - live_price
                    except Exception:
                        pass  # CLOB price is a bonus, Gamma price is fine

            return pm
        except Exception as e:
            logger.warning(f"Polymarket detail error for {slug}: {e}")
            return None

    # ── Kalshi ──────────────────────────────────────────────

    async def _search_kalshi(self, query: str) -> list[PredictionMarket]:
        """Search Kalshi by scanning events then fetching their markets."""
        try:
            # Step 1: Get open events (organized by category, not sports-dominant)
            resp = await self._http.get(
                f"{KALSHI_BASE}/events",
                params={"status": "open", "limit": 50},
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning(f"Kalshi events returned {resp.status_code}")
                return []

            data = resp.json()
            events = data.get("events", [])
            query_lower = query.lower()
            query_terms = query_lower.split()

            results = []

            # Step 2: Check event titles for matches, then fetch markets
            for i, event in enumerate(events[:10]):
                event_title = event.get("title", "").lower()
                event_ticker = event.get("ticker", "")

                # Match if query terms appear in event title
                if not any(term in event_title for term in query_terms):
                    continue

                # Rate limit: small delay between event fetches
                if i > 0:
                    await asyncio.sleep(0.3)

                # Step 3: Fetch markets for this event
                try:
                    mr = await self._http.get(
                        f"{KALSHI_BASE}/markets",
                        params={"event_ticker": event_ticker, "status": "open", "limit": 10},
                        headers={"Accept": "application/json"},
                        timeout=8.0,
                    )
                    if mr.status_code == 200:
                        markets_data = mr.json()
                        for m in markets_data.get("markets", []):
                            pm = self._parse_kalshi_market(m)
                            if pm:
                                results.append(pm)
                except Exception:
                    continue

            return results
        except Exception as e:
            logger.warning(f"Kalshi search error: {e}")
            return []

    def _parse_kalshi_market(self, m: dict) -> PredictionMarket | None:
        """Parse a Kalshi market dict into unified PredictionMarket."""
        try:
            ticker = m.get("ticker", "")
            title = m.get("title", "")
            yes_bid = float(m.get("yes_bid_dollars", 0))
            volume = float(m.get("volume_fp", 0))  # Kalshi uses fake-penny notation
            m.get("event_ticker", "")
            category = m.get("category", "")

            # Skip multi-outcome markets (sports parlays, etc.) - they have no yes_bid
            if yes_bid <= 0 or ",yes " in title.lower():
                return None

            prob_yes = yes_bid  # Best YES bid approximates probability
            prob_no = 1.0 - prob_yes if prob_yes else 0.5

            tokens_mentioned = _extract_token_symbols(title)

            return PredictionMarket(
                source="kalshi",
                source_id=ticker,
                question=title,
                slug=ticker,
                probability_yes=prob_yes,
                probability_no=prob_no,
                volume_usd=volume,
                category=category,
                tokens_mentioned=tokens_mentioned,
                url=f"https://kalshi.com/markets/{ticker}" if ticker else "",
                updated_at=datetime.now(UTC).isoformat(),
            )
        except Exception as e:
            logger.warning(f"Failed to parse Kalshi market: {e}")
            return None

    async def _trending_kalshi(self, limit: int) -> list[PredictionMarket]:
        """Get trending Kalshi markets by volume - uses events-first approach."""
        try:
            # Get open events (avoid sports-multi-outcome noise from raw /markets)
            resp = await self._http.get(
                f"{KALSHI_BASE}/events",
                params={"status": "open", "limit": min(limit * 2, 30)},
                timeout=10.0,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            events = data.get("events", [])

            results = []
            for event in events[:limit]:
                event_ticker = event.get("ticker", "")
                try:
                    mr = await self._http.get(
                        f"{KALSHI_BASE}/markets",
                        params={"event_ticker": event_ticker, "status": "open", "limit": 5},
                        timeout=8.0,
                    )
                    if mr.status_code == 200:
                        markets_data = mr.json()
                        for m in markets_data.get("markets", []):
                            pm = self._parse_kalshi_market(m)
                            if pm:
                                results.append(pm)
                except Exception:
                    continue

            return results[:limit]
        except Exception as e:
            logger.warning(f"Kalshi trending error: {e}")
            return []

    async def _kalshi_detail(self, ticker: str) -> PredictionMarket | None:
        """Get detailed Kalshi market data including orderbook."""
        try:
            resp = await self._http.get(
                f"{KALSHI_BASE}/markets/{ticker}/orderbook",
                timeout=10.0,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            orderbook = data.get("orderbook_fp", {})
            yes_bids = orderbook.get("yes_dollars", [])
            best_yes = float(yes_bids[0][0]) if yes_bids else 0.5

            # Also get market metadata
            meta_resp = await self._http.get(
                f"{KALSHI_BASE}/markets",
                params={"ticker": ticker},
                timeout=10.0,
            )
            if meta_resp.status_code == 200:
                meta_data = meta_resp.json()
                markets = meta_data.get("markets", [])
                if markets:
                    pm = self._parse_kalshi_market(markets[0])
                    if pm:
                        pm.probability_yes = best_yes
                        pm.probability_no = 1.0 - best_yes
                        return pm

            return None
        except Exception as e:
            logger.warning(f"Kalshi detail error for {ticker}: {e}")
            return None

    # ── Limitless ───────────────────────────────────────────

    async def _search_limitless(self, query: str) -> list[PredictionMarket]:
        """Search Limitless Exchange markets by fetching active and filtering."""
        try:
            resp = await self._http.get(
                f"{LIMITLESS_BASE}/markets/active",
                params={"limit": 25},
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning(f"Limitless markets returned {resp.status_code}")
                return []

            data = resp.json()
            all_markets = data.get("data", [])

            # Filter client-side by query terms
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
        except Exception as e:
            logger.warning(f"Limitless search error: {e}")
            return []

    def _parse_limitless_market(self, m: dict) -> PredictionMarket | None:
        """Parse a Limitless market dict into unified PredictionMarket."""
        try:
            title = m.get("title", "")
            slug = m.get("slug", str(m.get("id", "")))

            # Prices: [YES%, NO%] - e.g., [42.8, 57.2]
            prices = m.get("prices", [50, 50])
            prob_yes = float(prices[0]) / 100 if isinstance(prices, list) and len(prices) >= 1 else 0.5
            prob_no = float(prices[1]) / 100 if isinstance(prices, list) and len(prices) >= 2 else 0.5

            # Volume: use volumeFormatted if available, else volume
            vol_str = m.get("volumeFormatted", str(m.get("volume", 0)))
            volume = float(vol_str) if vol_str else 0.0

            categories = m.get("categories", [])
            category = categories[0] if categories else ""
            tags = m.get("tags", [])
            tokens_mentioned = _extract_token_symbols(title)

            return PredictionMarket(
                source="limitless",
                source_id=str(slug),
                question=title,
                slug=str(slug),
                probability_yes=prob_yes,
                probability_no=prob_no,
                volume_usd=volume,
                category=category,
                tags=tags,
                tokens_mentioned=tokens_mentioned,
                url=f"https://limitless.exchange/markets/{slug}" if slug else "",
                ends_at=m.get("expirationDate", ""),
                updated_at=datetime.now(UTC).isoformat(),
            )
        except Exception as e:
            logger.warning(f"Failed to parse Limitless market: {e}")
            return None

    async def _trending_limitless(self, limit: int) -> list[PredictionMarket]:
        """Get trending Limitless markets."""
        try:
            limit = min(limit, 25)  # API max
            resp = await self._http.get(
                f"{LIMITLESS_BASE}/markets/active",
                params={"limit": limit},
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            markets = data.get("data", [])
            return [pm for m in markets[:limit] if (pm := self._parse_limitless_market(m))]
        except Exception as e:
            logger.warning(f"Limitless trending error: {e}")
            return []

    # ── Manifold ────────────────────────────────────────────

    async def _search_manifold(self, query: str) -> list[PredictionMarket]:
        """Search Manifold Markets (play-money, sentiment signals).

        Manifold is pure play-money but useful for:
        - Forecasting community sentiment
        - Early signal detection (top forecasters often move before real-money markets)
        - Broad question coverage (more niche crypto questions than Polymarket)
        """
        try:
            resp = await self._http.get(
                f"{MANIFOLD_BASE}/search-markets",
                params={"term": query, "limit": 20},
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning(f"Manifold search returned {resp.status_code}")
                return []

            data = resp.json()
            contracts = data if isinstance(data, list) else data.get("contracts", data.get("markets", []))

            results = []
            for c in contracts[:10]:
                pm = self._parse_manifold_market(c)
                if pm:
                    results.append(pm)

            return results
        except Exception as e:
            logger.warning(f"Manifold search error: {e}")
            return []

    def _parse_manifold_market(self, c: dict) -> PredictionMarket | None:
        """Parse a Manifold contract into unified PredictionMarket."""
        try:
            question = c.get("question", "")
            slug = c.get("slug", c.get("id", ""))
            prob = float(c.get("probability", c.get("prob", 0.5)))
            volume = float(c.get("volume", c.get("volume24Hours", 0)))

            # Manifold uses "Mana" play money, volume is a signal but lower weight
            tokens_mentioned = _extract_token_symbols(question)
            tags = list(c.get("tags", []))

            return PredictionMarket(
                source="manifold",
                source_id=str(slug),
                question=question,
                slug=str(slug),
                probability_yes=prob,
                probability_no=1.0 - prob,
                volume_usd=volume,
                category="",
                tags=tags,
                tokens_mentioned=tokens_mentioned,
                url=f"https://manifold.markets/{c.get('creatorUsername', '')}/{slug}" if slug else "",
                updated_at=datetime.now(UTC).isoformat(),
            )
        except Exception as e:
            logger.warning(f"Failed to parse Manifold market: {e}")
            return None

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _parse_json_field(val):
        """Parse double-encoded JSON fields (Polymarket Gamma API)."""
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return val
        return val


# ── Singleton ────────────────────────────────────────────────────

_service: PredictionMarketService | None = None


def get_prediction_market_service() -> PredictionMarketService:
    """Get or create the singleton PredictionMarketService."""
    global _service
    if _service is None:
        _service = PredictionMarketService()
    return _service


# ── Helpers: Token Extraction ────────────────────────────────────

# Common token symbols to detect in market questions
_COMMON_TOKENS = {
    "BTC",
    "ETH",
    "SOL",
    "USDT",
    "USDC",
    "DAI",
    "BNB",
    "XRP",
    "ADA",
    "DOGE",
    "MATIC",
    "POL",
    "DOT",
    "AVAX",
    "LINK",
    "UNI",
    "AAVE",
    "ARB",
    "OP",
    "SUI",
    "APT",
    "TIA",
    "SEI",
    "STRK",
    "WLD",
    "PEPE",
    "SHIB",
    "BONK",
    "WIF",
    "JUP",
    "PYTH",
    "RNDR",
    "FET",
    "AGIX",
    "OCEAN",
    "IMX",
    "INJ",
    "EIGEN",
    "ENA",
    "ETHFI",
}

# Broader project names often referenced in prediction markets
_COMMON_PROJECTS = {
    "polymarket",
    "kalshi",
    "manifold",
    "uniswap",
    "sushiswap",
    "aave",
    "compound",
    "makerdao",
    "maker",
    "lido",
    "eigenlayer",
    "chainlink",
    "arbitrum",
    "optimism",
    "polygon",
    "avalanche",
    "fantom",
    "near",
    "celestia",
    "worldcoin",
    "tether",
    "circle",
    "coinbase",
    "binance",
    "kraken",
    "ftx",
    "celcius",
    "blockfi",
    "three arrows",
    "alameda",
    "jump crypto",
    "wintermute",
    "curve",
    "balancer",
    "thorchain",
    "osmosis",
    "dydx",
    "gmx",
    "hyperliquid",
    "jupiter",
    "raydium",
    "orca",
    "wormhole",
    "layerzero",
    "zksync",
    "starknet",
    "scroll",
    "linea",
    "base",
    "mantle",
    "mode",
    "blast",
}


def _extract_token_symbols(text: str) -> list[str]:
    """Extract known token symbols and project names from text."""
    found = []
    text_upper = text.upper()
    text_lower = text.lower()

    # Check token symbols (typically uppercase in text)
    for token in _COMMON_TOKENS:
        # Match as word boundary: " BTC " or "BTC's" or "$BTC"
        if (
            f" {token} " in f" {text_upper} "
            or f"${token}" in text_upper
            or text_upper.startswith(f"{token} ")
            or text_upper.endswith(f" {token}")
        ) and token not in found:
            found.append(token)

    # Check project names (case-insensitive)
    for project in _COMMON_PROJECTS:
        if project in text_lower and project.upper() not in found:
            found.append(project)

    return found


# ── Caching Helpers ──────────────────────────────────────────────


def _cache_hash(*args) -> str:
    """Create a short hash for cache keys."""
    raw = "|".join(str(a) for a in args)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _market_to_dict(m: PredictionMarket) -> dict:
    """Serialize PredictionMarket to dict for JSON caching."""
    return {
        "source": m.source,
        "source_id": m.source_id,
        "question": m.question,
        "slug": m.slug,
        "probability_yes": m.probability_yes,
        "probability_no": m.probability_no,
        "volume_usd": m.volume_usd,
        "liquidity_usd": m.liquidity_usd,
        "category": m.category,
        "tags": m.tags,
        "tokens_mentioned": m.tokens_mentioned,
        "is_security_relevant": m.is_security_relevant,
        "is_crypto_relevant": m.is_crypto_relevant,
        "url": m.url,
        "ends_at": m.ends_at,
        "updated_at": m.updated_at,
    }


def _dict_to_market(d: dict) -> PredictionMarket:
    """Deserialize dict back to PredictionMarket."""
    return PredictionMarket(
        source=d.get("source", ""),
        source_id=d.get("source_id", ""),
        question=d.get("question", ""),
        slug=d.get("slug", ""),
        probability_yes=float(d.get("probability_yes", 0.5)),
        probability_no=float(d.get("probability_no", 0.5)),
        volume_usd=float(d.get("volume_usd", 0)),
        liquidity_usd=float(d.get("liquidity_usd", 0)),
        category=d.get("category", ""),
        tags=d.get("tags", []),
        tokens_mentioned=d.get("tokens_mentioned", []),
        is_security_relevant=d.get("is_security_relevant", False),
        is_crypto_relevant=d.get("is_crypto_relevant", False),
        url=d.get("url", ""),
        ends_at=d.get("ends_at", ""),
        updated_at=d.get("updated_at", ""),
    )


def _digest_to_dict(d: PredictionDigest) -> dict:
    """Serialize PredictionDigest to dict for JSON caching."""
    return {
        "generated_at": d.generated_at,
        "total_markets_searched": d.total_markets_searched,
        "security_relevant_count": d.security_relevant_count,
        "crypto_relevant_count": d.crypto_relevant_count,
        "top_threats": [_market_to_dict(m) for m in d.top_threats],
        "token_specific_markets": [_market_to_dict(m) for m in d.token_specific_markets],
        "ecosystem_risk_markets": [_market_to_dict(m) for m in d.ecosystem_risk_markets],
        "regulatory_markets": [_market_to_dict(m) for m in d.regulatory_markets],
    }


def _dict_to_digest(d: dict) -> PredictionDigest:
    """Deserialize dict back to PredictionDigest."""
    return PredictionDigest(
        generated_at=d.get("generated_at", ""),
        total_markets_searched=d.get("total_markets_searched", 0),
        security_relevant_count=d.get("security_relevant_count", 0),
        crypto_relevant_count=d.get("crypto_relevant_count", 0),
        top_threats=[_dict_to_market(m) for m in d.get("top_threats", [])],
        token_specific_markets=[_dict_to_market(m) for m in d.get("token_specific_markets", [])],
        ecosystem_risk_markets=[_dict_to_market(m) for m in d.get("ecosystem_risk_markets", [])],
        regulatory_markets=[_dict_to_market(m) for m in d.get("regulatory_markets", [])],
    )
