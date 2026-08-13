#!/usr/bin/env python3
"""
GMGN Data Provider — Real GMGN public API integration.
======================================================
Provides: token info, live trades, holder analysis, smart money,
bundle/sniper detection, KOL tracking, new pair discovery.

Uses GMGN's public (undocumented) JSON API — no API key required.
The GMGN Agent API key (GMGN_API_KEY) is accepted but optional; when
present it is sent as the ``X-APIKEY`` header (the auth convention used by
the official GMGN SDK, see ``src/client/OpenApiClient.ts`` in
GMGNAI/gmgn-skills), otherwise browser-like headers are used. Falls back
to DexScreener/Birdeye when GMGN is unavailable.

Cloudflare escape: when residential proxies are configured
(``WEBSHARE_PROXY_LIST`` / ``WEBSHARE_PROXY_USER`` / ``WEBSHARE_PROXY_PASS``)
the provider routes requests through
:class:`app.databus.proxy_rotator.ProxyRotator` — proxy-first, and a
403/5xx/timeout on the proxy egress marks the proxy failed (cooldown) and
retries once direct. With no proxies configured the provider uses a direct
client exactly as before.

Endpoint provenance (verified against public open-source usage 2026-08-09):
  - token info:      /defi/quotation/v1/tokens/{chain}/{address}
  - trades:          /defi/quotation/v1/trades/{chain}/{address}
  - holders:         /vas/api/v1/token_holders/{chain}/{address}
  - smart money:     /defi/quotation/v1/smartmoney/{chain}/walletNew/{wallet}
  - new pairs:       /defi/quotation/v1/rank/{chain}/pump?new_creation=true
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.databus.proxy_rotator import get_proxy_rotator

logger = get_logger(__name__)

GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
GMGN_BASE = "https://gmgn.ai"

# Browser-like headers — GMGN's public API rejects plain curl/httpx UAs.
_BROWSER_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://gmgn.ai/?chain=sol",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}

# Cache TTLs (seconds)
TRADE_CACHE_TTL = 30
TOKEN_CACHE_TTL = 120
HOLDER_CACHE_TTL = 300


def _normalize_chain(chain: str) -> str:
    """Map chain names to GMGN's short chain slugs (sol, eth, bsc, base...)."""
    aliases = {
        "solana": "sol",
        "sol": "sol",
        "ethereum": "eth",
        "eth": "eth",
        "mainnet": "eth",
        "bsc": "bsc",
        "bnb": "bsc",
        "binance": "bsc",
        "base": "base",
        "polygon": "polygon",
        "matic": "polygon",
        "arbitrum": "arbitrum",
        "arb": "arbitrum",
        "optimism": "optimism",
        "op": "optimism",
        "avalanche": "avax",
        "avax": "avax",
        "fantom": "fantom",
        "ftm": "fantom",
        "tron": "tron",
        "trx": "tron",
        "robinhood": "robinhood",
    }
    return aliases.get(chain.lower(), chain.lower())


class GMGNProvider:
    """Real GMGN public API client with fallback cascade."""

    def __init__(self) -> None:
        self.api_key = GMGN_API_KEY
        self.headers = dict(_BROWSER_HEADERS)
        if self.api_key:
            # GMGN Agent API auth convention (official SDK: X-APIKEY header).
            self.headers["X-APIKEY"] = self.api_key
        # Direct client is the always-available fallback egress.
        self.client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        # Proxy-first egress when residential proxies are configured.
        self._rotator = get_proxy_rotator()
        self._proxy_url: str | None = None
        self._proxy_client: httpx.AsyncClient | None = None
        if self._rotator.enabled:
            self._proxy_url = self._rotator.get_rotating_proxy()
            self._proxy_client = self._rotator.get_client(proxy=self._proxy_url)
        # Public API works without a key; _available is only used to decide
        # whether to try GMGN at all (True = always try, fallbacks cover failures).
        self._available = True

    # ═══════════════════════════════════════════════════════════════
    # CORE: Token Info
    # ═══════════════════════════════════════════════════════════════

    async def get_token_info(self, address: str, chain: str = "solana") -> dict[str, Any]:
        """Get comprehensive token data from GMGN."""
        result = await self._cached_or_fetch(
            f"token:{chain}:{address}",
            TOKEN_CACHE_TTL,
            self._fetch_token_info,
            address,
            chain,
        )
        if result:
            return result

        # Fallback: DexScreener
        return await self._fallback_dexscreener_token(address, chain)

    async def _fetch_token_info(self, address: str, chain: str) -> dict[str, Any] | None:
        """Fetch token info from GMGN public API directly."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            # GMGN public token quotation endpoint
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}/defi/quotation/v1/tokens/{chain_slug}/{address}",
                headers=self.headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                token = (data.get("data") or {}).get("token", data.get("data", data))
                if not token:
                    return None
                return {
                    "address": address,
                    "chain": chain,
                    "name": token.get("name", ""),
                    "symbol": token.get("symbol", ""),
                    "price": float(token.get("price", 0) or 0),
                    "market_cap": float(token.get("market_cap", 0) or 0),
                    "fdv": float(token.get("fdv", 0) or 0),
                    "liquidity": float(token.get("liquidity", 0) or 0),
                    "volume_24h": float(token.get("volume_24h", 0) or 0),
                    "holders": int(token.get("holder_count", token.get("holders", 0)) or 0),
                    "price_change_1h": float(token.get("price_1h", 0) or 0),
                    "price_change_24h": float(token.get("price_24h", 0) or 0),
                    "buy_24h": int(token.get("buys_24h", 0) or 0),
                    "sell_24h": int(token.get("sells_24h", 0) or 0),
                    "created_at": token.get("creation_timestamp"),
                    "launchpad": token.get("launchpad"),
                    "source": "gmgn",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            logger.debug("GMGN token_info HTTP %s for %s", resp.status_code, address)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN token_info failed: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # LIVE TRADES
    # ═══════════════════════════════════════════════════════════════

    async def get_token_trades(
        self,
        address: str,
        chain: str = "solana",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get recent trades for a token."""
        result = await self._cached_or_fetch_list(
            f"trades:{chain}:{address}",
            TRADE_CACHE_TTL,
            self._fetch_trades,
            address,
            chain,
            limit,
        )
        if result:
            return result

        # Fallback: DexScreener trades
        return await self._fallback_dexscreener_trades(address, chain, limit)

    async def _fetch_trades(self, address: str, chain: str, limit: int) -> list[dict[str, Any]] | None:
        """Fetch live trades from GMGN."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}/defi/quotation/v1/trades/{chain_slug}/{address}",
                headers=self.headers,
                params={"limit": limit},
            )
            if resp.status_code == 200:
                data = resp.json()
                trades = (data.get("data") or {}).get("history", data.get("data", data.get("trades", [])))
                if isinstance(trades, list):
                    return self._normalize_trades(trades, "gmgn")
            logger.debug("GMGN trades HTTP %s", resp.status_code)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN trades failed: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # HOLDER ANALYSIS
    # ═══════════════════════════════════════════════════════════════

    async def get_holder_analysis(self, address: str, chain: str = "solana") -> dict[str, Any]:
        """Get holder breakdown including concentration, sniper wallets, top holders."""
        result = await self._cached_or_fetch(
            f"holders:{chain}:{address}",
            HOLDER_CACHE_TTL,
            self._fetch_holder_analysis,
            address,
            chain,
        )
        if result:
            return result

        # Fallback: Birdeye holder data
        return await self._fallback_birdeye_holders(address)

    async def _fetch_holder_analysis(self, address: str, chain: str) -> dict[str, Any] | None:
        """Fetch holder analysis from GMGN."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}/vas/api/v1/token_holders/{chain_slug}/{address}",
                headers=self.headers,
                params={"orderby": "amount_percentage", "direction": "desc", "limit": 100},
            )
            if resp.status_code == 200:
                data = resp.json()
                rows = (data.get("data") or {}).get("list", [])
                top = []
                for h in rows[:20]:
                    top.append(
                        {
                            "address": h.get("address", ""),
                            "amount_percentage": h.get("amount_percentage"),
                            "realized_profit": h.get("realized_profit"),
                            "unrealized_profit": h.get("unrealized_profit"),
                            "profit_change": h.get("profit_change"),
                            "is_insider": bool(h.get("insider", False)),
                            "is_smart_money": bool(h.get("is_smart_dealer", h.get("smart_money", False))),
                        }
                    )
                return {
                    "address": address,
                    "chain": chain,
                    "total_holders": len(rows),
                    "top_10_pct": None,
                    "top_holders": top,
                    "sniper_count": sum(
                        1 for h in rows if h.get("profit_change", 0) and h.get("buy_tx_count_cur", 0) <= 2
                    ),
                    "insider_count": sum(1 for h in rows if h.get("insider", False)),
                    "fresh_wallet_pct": 0,
                    "source": "gmgn",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            logger.debug("GMGN holders HTTP %s", resp.status_code)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN holders failed: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # SMART MONEY
    # ═══════════════════════════════════════════════════════════════

    async def get_smart_money(self, address: str, chain: str = "solana") -> dict[str, Any]:
        """Get smart money/KOL activity for a token."""
        result = await self._cached_or_fetch(
            f"smartmoney:{chain}:{address}",
            TOKEN_CACHE_TTL,
            self._fetch_smart_money,
            address,
            chain,
        )
        if result:
            return result
        return {"address": address, "smart_money_activity": [], "source": "none"}

    async def _fetch_smart_money(self, address: str, chain: str) -> dict[str, Any] | None:
        """Fetch smart money data from GMGN."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}/defi/quotation/v1/smartmoney/{chain_slug}/walletNew/{address}",
                headers=self.headers,
                params={"period": "7d"},
            )
            if resp.status_code == 200:
                data = resp.json()
                sm = data.get("data", data)
                return {
                    "address": address,
                    "chain": chain,
                    "smart_money_activity": sm.get("activity", []),
                    "kol_trades": sm.get("kol_trades", []),
                    "smart_money_count": sm.get("smart_money_count", 0),
                    "kol_count": sm.get("kol_count", 0),
                    "buy_pressure": sm.get("buy_pressure", 0),
                    "total_profit_pnl": sm.get("total_profit_pnl"),
                    "winrate": sm.get("winrate"),
                    "sol_balance": sm.get("sol_balance"),
                    "buy_7d": sm.get("buy_7d"),
                    "tags": sm.get("tags", []),
                    "source": "gmgn",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN smart_money failed: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # NEW PAIRS / TOKEN LAUNCHES
    # ═══════════════════════════════════════════════════════════════

    async def get_new_pairs(self, chain: str = "solana", limit: int = 50) -> list[dict[str, Any]]:
        """Get newest token launches from GMGN."""
        result = await self._cached_or_fetch_list(
            f"newpairs:{chain}",
            30,  # 30 second cache for launch detection
            self._fetch_new_pairs,
            chain,
            limit,
        )
        if result:
            return result

        # Fallback: DexScreener new pairs
        return await self._fallback_dexscreener_new_pairs(chain, limit)

    async def _fetch_new_pairs(self, chain: str, limit: int) -> list[dict[str, Any]] | None:
        """Fetch new token listings from GMGN (pump.fun new_creation rank)."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}/defi/quotation/v1/rank/{chain_slug}/pump",
                headers=self.headers,
                params={
                    "limit": limit,
                    "orderby": "created_timestamp",
                    "direction": "desc",
                    "new_creation": "true",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                pairs = (data.get("data") or {}).get("rank", data.get("data", data.get("pairs", [])))
                if isinstance(pairs, list):
                    return [
                        {
                            "address": p.get("address", ""),
                            "chain": chain,
                            "symbol": p.get("symbol", ""),
                            "name": p.get("name", ""),
                            "price": float(p.get("price", 0) or 0),
                            "liquidity": float(p.get("liquidity", 0) or 0),
                            "market_cap": float(p.get("market_cap", 0) or 0),
                            "created_at": p.get("creation_timestamp"),
                            "age_seconds": 0,
                            "launchpad": p.get("launchpad", "pump"),
                            "source": "gmgn",
                        }
                        for p in pairs
                    ]
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN new_pairs failed: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # FALLBACKS (DexScreener, Birdeye)
    # ═══════════════════════════════════════════════════════════════

    async def _fallback_dexscreener_token(self, address: str, chain: str) -> dict[str, Any]:
        """Fallback token info from DexScreener (free, no key)."""
        try:
            resp = await self._request(
                "GET",
                f"https://api.dexscreener.com/latest/dex/tokens/{address}",
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    return {
                        "address": address,
                        "chain": p.get("chainId", chain),
                        "name": p.get("baseToken", {}).get("name", ""),
                        "symbol": p.get("baseToken", {}).get("symbol", ""),
                        "price": float(p.get("priceUsd", 0)),
                        "liquidity": float(p.get("liquidity", {}).get("usd", 0)),
                        "fdv": float(p.get("fdv", 0)),
                        "market_cap": float(p.get("marketCap", 0)),
                        "volume_24h": float(p.get("volume", {}).get("h24", 0)),
                        "price_change_1h": float(p.get("priceChange", {}).get("h1", 0)),
                        "price_change_24h": float(p.get("priceChange", {}).get("h24", 0)),
                        "dex": p.get("dexId", ""),
                        "pair_created_at": p.get("pairCreatedAt"),
                        "source": "dexscreener_fallback",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("DexScreener fallback failed: %s", e)
        return {"address": address, "error": "no_data", "source": "none"}

    async def _fallback_dexscreener_trades(self, address: str, chain: str, limit: int) -> list[dict[str, Any]]:
        """Fallback: Derive trade summary from DexScreener pair data (io endpoint blocked by CF)."""
        try:
            import time

            resp = await self._request(
                "GET",
                f"https://api.dexscreener.com/latest/dex/tokens/{address}",
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    vol_24h = float(p.get("volume", {}).get("h24", 0))
                    buys = int(p.get("txns", {}).get("h24", {}).get("buys", 0))
                    sells = int(p.get("txns", {}).get("h24", {}).get("sells", 0))
                    price = float(p.get("priceUsd", 0))
                    price_change = float(p.get("priceChange", {}).get("h24", 0))
                    total = buys + sells
                    if total > 0 and vol_24h > 0:
                        avg_trade = vol_24h / total
                        now_ts = int(time.time() * 1000)
                        trades = []
                        for i in range(min(limit, total)):
                            is_buy = buys > sells if i == 0 else (i % 2 == 0)
                            trades.append(
                                {
                                    "tx_hash": f"dex:{p.get('pairAddress', address)}:{i}",
                                    "timestamp": int(now_ts - (i * 60000 / max(total, 1))),
                                    "side": "buy" if is_buy else "sell",
                                    "price_usd": round(price * (1 + (price_change / 100) * (i / max(total, 1))), 10),
                                    "amount_usd": round(avg_trade, 2),
                                    "amount_token": round(avg_trade / price, 6) if price > 0 else 0,
                                    "maker": "",
                                    "source": "dexscreener_derived",
                                }
                            )
                        return trades
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("DexScreener trades fallback failed: %s", e)
        return []

    async def _fallback_dexscreener_new_pairs(self, chain: str, limit: int) -> list[dict[str, Any]]:
        """Fallback new pairs from DexScreener search."""
        try:
            resp = await self._request(
                "GET",
                "https://api.dexscreener.com/latest/dex/search",
                params={"q": chain.upper() if chain != "solana" else "SOL", "limit": limit},
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                return [
                    {
                        "address": p.get("baseToken", {}).get("address", ""),
                        "chain": p.get("chainId", chain),
                        "symbol": p.get("baseToken", {}).get("symbol", ""),
                        "name": p.get("baseToken", {}).get("name", ""),
                        "price": float(p.get("priceUsd", 0)),
                        "liquidity": float(p.get("liquidity", {}).get("usd", 0)),
                        "market_cap": float(p.get("fdv", 0)),
                        "created_at": p.get("pairCreatedAt"),
                        "source": "dexscreener_fallback",
                    }
                    for p in pairs
                    if float(p.get("liquidity", {}).get("usd", 0)) > 100
                ]
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("DexScreener new pairs fallback failed: %s", e)
        return []

    async def _fallback_birdeye_holders(self, address: str) -> dict[str, Any]:
        """Fallback holders from Birdeye."""
        try:
            api_key = os.getenv("BIRDEYE_API_KEY", "")
            if not api_key:
                return {"address": address, "total_holders": 0, "source": "none"}
            resp = await self._request(
                "GET",
                "https://public-api.birdeye.so/defi/token_overview",
                headers={"X-API-KEY": api_key, "accept": "application/json"},
                params={"address": address},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                return {
                    "address": address,
                    "total_holders": data.get("holder", 0),
                    "top_10_pct": data.get("top10HolderPercent", 0),
                    "source": "birdeye_fallback",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("Birdeye holders fallback failed: %s", e)
        return {"address": address, "total_holders": 0, "source": "none"}

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Proxy-first HTTP request with a single direct retry.

        When a proxied client is configured it is tried first; on a proxy
        failure (403/5xx/timeout — Cloudflare IP-block family) the proxy is
        marked failed (cooldown) and the request is retried once against the
        direct client. Transport errors propagate to callers, which already
        guard each fetch with try/except.
        """
        if self._proxy_client is not None:
            try:
                proxy_resp = await self._proxy_client.request(method, url, headers=headers, params=params)
            except (httpx.HTTPError, ConnectionError, TimeoutError):
                self._rotate_proxy()
                return await self.client.request(method, url, headers=headers, params=params)
            if proxy_resp.status_code in (403, 429) or proxy_resp.status_code >= 500:
                self._rotate_proxy()
                return await self.client.request(method, url, headers=headers, params=params)
            return proxy_resp
        return await self.client.request(method, url, headers=headers, params=params)

    def _rotate_proxy(self) -> None:
        """Mark the current proxy failed and pick the next healthy egress."""
        self._rotator.mark_failed(self._proxy_url)
        self._proxy_url = self._rotator.next_after_failure()
        self._proxy_client = self._rotator.get_client(proxy=self._proxy_url)

    def _normalize_trades(self, trades: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
        """Normalize trade data from any source to a standard format."""
        normalized = []
        for t in trades:
            amount_usd = float(t.get("volume", {}).get("usd", t.get("amountUsd", t.get("usd_amount", 0))))
            normalized.append(
                {
                    "tx_hash": t.get("txHash", t.get("tx_hash", "")),
                    "timestamp": t.get("time", t.get("timestamp", t.get("blockTime", ""))),
                    "side": "buy" if t.get("type", "").lower() in ("buy", "b") else "sell",
                    "price_usd": float(t.get("priceUsd", t.get("price", 0))),
                    "amount_usd": amount_usd,
                    "amount_token": float(t.get("amount", t.get("token_amount", 0))),
                    "maker": t.get("maker", t.get("wallet", "")),
                    "is_whale": amount_usd > 10000,
                    "source": source,
                }
            )
        return normalized

    async def _cached_or_fetch(
        self,
        cache_key: str,
        ttl: int,
        fetcher: Any,
        *args: Any,
    ) -> Any:
        """Try Redis cache first, then fetch. Cache result on success."""
        # Try Redis
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                import json

                cached = await r.get(f"gmgn:{cache_key}")
                if cached:
                    return json.loads(cached)
        except Exception:  # noqa: S110 — best-effort cache
            pass

        # Fetch
        result = await fetcher(*args)
        if result:
            # Cache in Redis
            try:
                from app.core.redis import get_redis_async

                r = get_redis_async()
                if r:
                    import json

                    await r.setex(f"gmgn:{cache_key}", ttl, json.dumps(result))
            except Exception:  # noqa: S110 — best-effort cache
                pass

        return result

    async def _cached_or_fetch_list(
        self,
        cache_key: str,
        ttl: int,
        fetcher: Any,
        *args: Any,
    ) -> list[dict[str, Any]]:
        """Same as _cached_or_fetch but for list returns."""
        return await self._cached_or_fetch(cache_key, ttl, fetcher, *args) or []

    async def close(self) -> None:
        """Clean up resources."""
        await self.client.aclose()
        if self._proxy_client is not None:
            await self._proxy_client.aclose()


# ── Singleton ──────────────────────────────────────────────

_gmgn_provider: GMGNProvider | None = None


def get_gmgn_provider() -> GMGNProvider:
    """Get or create the GMGN provider singleton."""
    global _gmgn_provider
    if _gmgn_provider is None:
        _gmgn_provider = GMGNProvider()
    return _gmgn_provider
