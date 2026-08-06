#!/usr/bin/env python3
"""
GMGN Data Provider — Real GMGN Agent API integration.
======================================================
Provides: token info, live trades, holder analysis, smart money,
bundle/sniper detection, KOL tracking, new pair discovery.

Uses the GMGN Agent API key from env (GMGN_API_KEY).
Falls back to DexScreener/Birdeye when GMGN is unavailable.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
GMGN_BASE = "https://gmgn.ai"

# Cache TTLs (seconds)
TRADE_CACHE_TTL = 30
TOKEN_CACHE_TTL = 120
HOLDER_CACHE_TTL = 300


class GMGNProvider:
    """Real GMGN Agent API client with fallback cascade."""

    def __init__(self) -> None:
        self.api_key = GMGN_API_KEY
        self.headers = {
            "Authorization": f"Bearer {GMGN_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        self._available = bool(GMGN_API_KEY)

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
        """Fetch token info from GMGN API directly."""
        if not self._available:
            return None
        try:
            # GMGN agent API token endpoint
            resp = await self.client.get(
                f"{GMGN_BASE}/defi/v1/{chain}/token/{address}",
                headers=self.headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("data", data)
                return {
                    "address": address,
                    "chain": chain,
                    "name": token.get("name", ""),
                    "symbol": token.get("symbol", ""),
                    "price": float(token.get("price", 0)),
                    "market_cap": float(token.get("market_cap", 0)),
                    "fdv": float(token.get("fdv", 0)),
                    "liquidity": float(token.get("liquidity", 0)),
                    "volume_24h": float(token.get("volume_24h", 0)),
                    "holders": int(token.get("holders", 0)),
                    "price_change_1h": float(token.get("price_change_1h", 0)),
                    "price_change_24h": float(token.get("price_change_24h", 0)),
                    "buy_24h": int(token.get("buy_24h", 0)),
                    "sell_24h": int(token.get("sell_24h", 0)),
                    "created_at": token.get("created_at"),
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
            resp = await self.client.get(
                f"{GMGN_BASE}/defi/v1/{chain}/token/{address}/trades",
                headers=self.headers,
                params={"limit": limit},
            )
            if resp.status_code == 200:
                data = resp.json()
                trades = data.get("data", data.get("trades", []))
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
            resp = await self.client.get(
                f"{GMGN_BASE}/defi/v1/{chain}/token/{address}/holders",
                headers=self.headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                holders = data.get("data", data)
                return {
                    "address": address,
                    "chain": chain,
                    "total_holders": holders.get("total", 0),
                    "top_10_pct": holders.get("top_10_percent", 0),
                    "top_holders": holders.get("top_holders", [])[:20],
                    "sniper_count": holders.get("sniper_count", 0),
                    "insider_count": holders.get("insider_count", 0),
                    "fresh_wallet_pct": holders.get("fresh_wallet_percent", 0),
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
            resp = await self.client.get(
                f"{GMGN_BASE}/defi/v1/{chain}/token/{address}/smart-money",
                headers=self.headers,
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
        """Fetch new token listings from GMGN."""
        if not self._available:
            return None
        try:
            resp = await self.client.get(
                f"{GMGN_BASE}/defi/v1/{chain}/new-pairs",
                headers=self.headers,
                params={"limit": limit},
            )
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("data", data.get("pairs", []))
                if isinstance(pairs, list):
                    return [
                        {
                            "address": p.get("address", ""),
                            "chain": chain,
                            "symbol": p.get("symbol", ""),
                            "name": p.get("name", ""),
                            "price": float(p.get("price", 0)),
                            "liquidity": float(p.get("liquidity", 0)),
                            "market_cap": float(p.get("market_cap", 0)),
                            "created_at": p.get("created_at"),
                            "age_seconds": p.get("age_seconds", 0),
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
            resp = await self.client.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}")
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
        """Fallback trades from DexScreener."""
        try:
            resp = await self.client.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}")
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                if pairs:
                    pair_addr = pairs[0].get("pairAddress", "")
                    if pair_addr:
                        # DexScreener trades endpoint (free, per-pair)
                        trades_resp = await self.client.get(
                            f"https://io.dexscreener.com/dex/log/latest/dex/trades/{pair_addr}"
                        )
                        if trades_resp.status_code == 200:
                            trades = trades_resp.json()
                            if isinstance(trades, list):
                                return self._normalize_trades(trades[:limit], "dexscreener")
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("DexScreener trades fallback failed: %s", e)
        return []

    async def _fallback_dexscreener_new_pairs(self, chain: str, limit: int) -> list[dict[str, Any]]:
        """Fallback new pairs from DexScreener search."""
        try:
            resp = await self.client.get(
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
            resp = await self.client.get(
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
        except Exception:
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
            except Exception:
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


# ── Singleton ──────────────────────────────────────────────

_gmgn_provider: GMGNProvider | None = None


def get_gmgn_provider() -> GMGNProvider:
    """Get or create the GMGN provider singleton."""
    global _gmgn_provider
    if _gmgn_provider is None:
        _gmgn_provider = GMGNProvider()
    return _gmgn_provider
