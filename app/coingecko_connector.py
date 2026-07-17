"""
CoinGecko Connector - Free + Pro API with x402 pay-per-use fallback.
Free tier: 30 req/min. Pro tier: 500+ req/min. x402: pay-per-request.

Endpoints covered:
- /ping (health check)
- /simple/price (batch prices)
- /simple/token_price (token prices by contract)
- /search/trending (trending coins, NFTs, categories)
- /coins/markets (top coins by market cap)
- /coins/{id} (detailed token info)
- /coins/categories (category listings)
- /coins/{id}/ohlc (candlestick data)
- /global (global market metrics)
- /exchanges (exchange listings)
- /onchain/... (GeckoTerminal DEX data via pro)
- /key (API key status)
"""

import asyncio
import os
import time
from typing import Any

import httpx

from app.core.circuit_breaker import AsyncCircuitBreaker, CircuitBreakerOpenError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Circuit breaker for CoinGecko API
_coingecko_cb = AsyncCircuitBreaker(
    name="coingecko",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)

# ── API Keys ───────────────────────────────────────────────


def _load_key(filename: str) -> str:
    """Load API key from /root/.secrets/ or env var."""
    env = os.getenv(filename.upper(), "")
    if env:
        return env
    try:
        with open(f"/root/.secrets/{filename}") as f:
            return f.read().strip()
    except Exception:
        return ""


COINGECKO_FREE_KEY = _load_key("coingecko_api_key")  # Demo key: CG-...
COINGECKO_PRO_KEY = _load_key("coingecko_api_key_pro")  # Also Demo tier


class CoinGeckoConnector:
    """CoinGecko Demo API connector with rate limiting, caching, and smart polling.

    Both keys are Demo tier (30 req/min). Webhooks require Analyst plan ($103/mo).
    Strategy: cache aggressively, poll smart, use n8n for scheduled refresh.
    """

    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self):
        self._cache: dict[str, tuple] = {}  # key -> (expiry, value)
        self._last_request = 0.0
        self._min_interval = 2.0  # 30 req/min on Demo tier
        # Use whichever key we have (both are Demo tier)
        self._api_key = COINGECKO_PRO_KEY or COINGECKO_FREE_KEY

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["x-cg-demo-api-key"] = self._api_key
        return headers

    def _base(self) -> str:
        return self.BASE_URL

    async def _get(self, endpoint: str, params: dict | None = None, ttl: int = 300) -> Any | None:
        """Rate-limited GET with caching and circuit breaker."""
        cache_key = f"{endpoint}:{sorted(params.items()) if params else ''}"
        now = time.time()

        # Check cache (fresh)
        if cache_key in self._cache:
            expiry, value = self._cache[cache_key]
            if now < expiry:
                return value

        # Circuit breaker check — serve stale cache if OPEN
        if not await _coingecko_cb.can_call():
            stale = _coingecko_cb.get_stale_result()
            if stale is not None:
                return stale
            raise CircuitBreakerOpenError(
                _coingecko_cb.name,
                _coingecko_cb.state,
                f"CoinGecko circuit OPEN — serving stale cache or failing fast",
            )

        # Rate limit
        elapsed = now - self._last_request
        interval = 2.0  # 30 req/min on Demo tier
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)

        try:
            self._last_request = time.time()
            url = f"{self._base()}/{endpoint}"
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.get(url, params=params or {}, headers=self._headers())
                if r.status_code == 200:
                    data = r.json()
                    self._cache[cache_key] = (now + ttl, data)
                    _coingecko_cb.set_stale_result(data)
                    await _coingecko_cb.record_success()
                    return data
                elif r.status_code == 429:
                    logger.warning("CoinGecko rate limited, backing off")
                    await _coingecko_cb.record_failure(is_server_error=False)
                    await asyncio.sleep(30)
                    return None
                elif r.status_code >= 500:
                    logger.debug(f"CoinGecko {endpoint}: {r.status_code} {r.text[:200]}")
                    await _coingecko_cb.record_failure(is_server_error=True)
                    return None
                else:
                    logger.debug(f"CoinGecko {endpoint}: {r.status_code} {r.text[:200]}")
                    await _coingecko_cb.record_failure(is_server_error=False)
                    return None
        except Exception as e:
            logger.debug(f"CoinGecko error: {e}")
            await _coingecko_cb.record_failure(is_server_error=True)
            return None

    # ── Health Check ─────────────────────────────────────────

    async def ping(self) -> dict:
        """Check API status. Returns {"gecko_says": "To the Moon!"} if healthy."""
        data = await self._get("ping", ttl=60)
        return data or {"gecko_says": "error", "status": "unavailable"}

    async def api_key_status(self) -> dict:
        """Check API key validity and rate limits."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{self.BASE_URL}/key",
                    headers=self._headers(),
                )
                if r.status_code == 200:
                    return r.json()
                return {"status": f"error_{r.status_code}"}
        except Exception as e:
            return {"status": f"error: {e}"}

    # ── Simple Price ─────────────────────────────────────────

    async def get_token_price(self, token_id: str) -> dict | None:
        """Get current price for a token by CoinGecko ID."""
        data = await self._get(
            "simple/price",
            params={
                "ids": token_id,
                "vs_currencies": "usd,btc",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_24hr_change": "true",
            },
        )
        if data and token_id in data:
            return data[token_id]
        return None

    async def get_token_prices(self, token_ids: list[str]) -> dict[str, dict]:
        """Batch get prices for multiple tokens."""
        data = await self._get(
            "simple/price",
            params={
                "ids": ",".join(token_ids[:250]),  # API limit
                "vs_currencies": "usd,btc",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_24hr_change": "true",
            },
        )
        return data or {}

    async def get_token_price_by_contract(self, chain: str, contract: str) -> dict | None:
        """Get price by contract address (Ethereum, BSC, etc.)."""
        data = await self._get(
            f"simple/token_price/{chain}",
            params={
                "contract_addresses": contract,
                "vs_currencies": "usd,btc",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_24hr_change": "true",
            },
        )
        if data and contract in data:
            return data[contract]
        return None

    # ── Trending & Search ────────────────────────────────────

    async def get_trending(self) -> list[dict]:
        """Get trending coins (updated every 30 min)."""
        data = await self._get("search/trending", ttl=600)
        if not data:
            return []
        coins = data.get("coins", [])
        return [
            {
                "id": c.get("item", {}).get("id"),
                "symbol": c.get("item", {}).get("symbol", "").upper(),
                "name": c.get("item", {}).get("name"),
                "market_cap_rank": c.get("item", {}).get("market_cap_rank"),
                "price_btc": c.get("item", {}).get("price_btc"),
                "score": c.get("score"),
            }
            for c in coins[:20]
        ]

    # ── Market Data ──────────────────────────────────────────

    async def get_market_overview(self, vs_currency: str = "usd", per_page: int = 50) -> list[dict]:
        """Get top coins by market cap."""
        data = await self._get(
            "coins/markets",
            params={
                "vs_currency": vs_currency,
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": 1,
                "sparkline": "false",
            },
        )
        if not data:
            return []
        return [
            {
                "id": c.get("id"),
                "symbol": c.get("symbol", "").upper(),
                "name": c.get("name"),
                "price": c.get("current_price"),
                "market_cap": c.get("market_cap"),
                "volume_24h": c.get("total_volume"),
                "change_24h": c.get("price_change_percentage_24h"),
                "image": c.get("image"),
            }
            for c in data[:per_page]
        ]

    async def get_global_metrics(self) -> dict | None:
        """Get global crypto market metrics."""
        data = await self._get("global", ttl=300)
        if not data:
            return None
        d = data.get("data", {})
        return {
            "total_market_cap_usd": d.get("total_market_cap", {}).get("usd"),
            "total_volume_24h_usd": d.get("total_volume", {}).get("usd"),
            "btc_dominance": d.get("market_cap_percentage", {}).get("btc"),
            "eth_dominance": d.get("market_cap_percentage", {}).get("eth"),
            "active_cryptocurrencies": d.get("active_cryptocurrencies"),
            "markets": d.get("markets"),
        }

    # ── Token Detail ─────────────────────────────────────────

    async def get_token_detail(self, token_id: str) -> dict | None:
        """Get detailed info about a specific token."""
        data = await self._get(
            f"coins/{token_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false",
            },
            ttl=600,
        )
        if not data:
            return None
        md = data.get("market_data", {})
        return {
            "id": data.get("id"),
            "symbol": data.get("symbol", "").upper(),
            "name": data.get("name"),
            "price_usd": md.get("current_price", {}).get("usd"),
            "market_cap": md.get("market_cap", {}).get("usd"),
            "volume_24h": md.get("total_volume", {}).get("usd"),
            "change_24h": md.get("price_change_percentage_24h"),
            "change_7d": md.get("price_change_percentage_7d"),
            "ath": md.get("ath", {}).get("usd"),
            "atl": md.get("atl", {}).get("usd"),
            "circulating_supply": md.get("circulating_supply"),
            "total_supply": md.get("total_supply"),
            "max_supply": md.get("max_supply"),
            "categories": data.get("categories", []),
            "links": {
                "homepage": data.get("links", {}).get("homepage", [])[:1],
                "twitter": data.get("links", {}).get("twitter_screen_name"),
                "telegram": data.get("links", {}).get("telegram_channel_identifier"),
            },
        }

    # ── Categories ───────────────────────────────────────────

    async def get_categories(self) -> list[dict]:
        """Get all coin categories sorted by market cap."""
        data = await self._get("coins/categories/list", ttl=3600)
        if not data:
            return []
        return [{"id": c.get("id"), "name": c.get("name")} for c in data[:50]]

    async def get_category_market_data(self) -> list[dict]:
        """Get categories with market data (top gainers/losers by category)."""
        data = await self._get(
            "coins/categories",
            params={
                "order": "market_cap_desc",
            },
            ttl=600,
        )
        if not data:
            return []
        return [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "market_cap": c.get("market_cap"),
                "volume_24h": c.get("volume_24h"),
                "change_24h": c.get("market_cap_change_24h"),
                "top_3_coins": c.get("top_3_coins", [])[:3],
            }
            for c in data[:20]
        ]

    # ── OHLC / Candlestick ───────────────────────────────────

    async def get_ohlc(self, token_id: str, vs_currency: str = "usd", days: int = 7) -> list[list]:
        """Get OHLC candlestick data. Returns [[timestamp, open, high, low, close], ...]"""
        data = await self._get(
            f"coins/{token_id}/ohlc",
            params={
                "vs_currency": vs_currency,
                "days": str(days),
            },
            ttl=300,
        )
        return data or []

    # ── Exchanges ────────────────────────────────────────────

    async def get_exchanges(self, per_page: int = 20) -> list[dict]:
        """Get top exchanges by trading volume."""
        data = await self._get(
            "exchanges",
            params={
                "per_page": per_page,
                "page": 1,
            },
            ttl=600,
        )
        if not data:
            return []
        return [
            {
                "id": e.get("id"),
                "name": e.get("name"),
                "trust_score": e.get("trust_score"),
                "volume_24h_btc": e.get("trade_volume_24h_btc"),
                "url": e.get("url"),
            }
            for e in data[:per_page]
        ]

    # ── Onchain / GeckoTerminal (Free, no key needed) ──────────

    async def get_trending_pools(self, chain: str = "solana") -> list[dict]:
        """Get trending DEX pools via GeckoTerminal free API."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"https://api.geckoterminal.com/api/v2/networks/{chain}/trending_pools",
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    data = r.json()
                    pools = data.get("data", [])
                    if isinstance(pools, list):
                        return pools[:20]
                    # GeckoTerminal wraps in attributes
                    attrs = data.get("data", {})
                    if isinstance(attrs, dict):
                        items = attrs.get("attributes", attrs.get("items", []))
                        return items[:20] if isinstance(items, list) else []
        except Exception as e:
            logger.debug(f"GeckoTerminal trending failed: {e}")
        return []

    # ── Status ───────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "tier": "demo",
            "api_key": bool(self._api_key),
            "free_key": bool(COINGECKO_FREE_KEY),
            "pro_key_note": "both keys are Demo tier - webhooks require Analyst plan ($103/mo)",
            "cache_size": len(self._cache),
            "base_url": self._base(),
            "rate_limit": "30 req/min",
        }


# ── Singleton ──────────────────────────────────────────────

_connector: CoinGeckoConnector | None = None


def get_coingecko_connector() -> CoinGeckoConnector:
    global _connector
    if _connector is None:
        _connector = CoinGeckoConnector()
    return _connector
