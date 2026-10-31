"""Free DEX data sources: scanner Redis cache, DexScreener, GeckoTerminal."""

from __future__ import annotations

from typing import Any

from app.providers.base import ProviderBase, logger


class DexSourcesMixin(ProviderBase):
    """Scanner-cache, DexScreener and GeckoTerminal fetchers."""

    async def _get_scanner_cache(self, mint: str, chain: str) -> dict[str, Any] | None:
        """Try to get data from existing scanner Redis cache."""
        try:
            import json

            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                raw = await r.get(f"sentinelscan:{chain}:{mint}")
                if raw:
                    data = json.loads(raw)
                    return {
                        "risk_score": data.get("risk_score"),
                        "liquidity_usd": data.get("liquidity_usd"),
                        "holders": data.get("total_holders", data.get("holder_count")),
                        "security": {
                            "is_honeypot": data.get("is_honeypot", data.get("is_potential_honeypot")),
                            "is_mintable": data.get("is_mintable"),
                            "is_renounced": data.get("is_renounced", data.get("is_mint_revoked")),
                            "scam_probability": data.get("scam_probability"),
                            "risk_flags": data.get("risk_flags", []),
                            "green_flags": data.get("green_flags", []),
                        },
                        "top_holders": (data.get("top_holders") or [])[:20],
                    }
        except Exception as e:
            logger.warning("Scanner cache error: %s", e)
        return None

    async def _fetch_dexscreener_full(self, mint: str, chain: str) -> dict[str, Any] | None:
        """Fetch full token data from DexScreener (free, unlimited)."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
                if resp.status_code == 200:
                    pairs = resp.json().get("pairs", [])
                    if pairs:
                        p = pairs[0]
                        return {
                            "name": p.get("baseToken", {}).get("name", ""),
                            "symbol": p.get("baseToken", {}).get("symbol", ""),
                            "price_usd": float(p.get("priceUsd", 0)),
                            "liquidity_usd": float(p.get("liquidity", {}).get("usd", 0)),
                            "volume_24h": float(p.get("volume", {}).get("h24", 0)),
                            "fdv": float(p.get("fdv", 0)),
                            "market_cap": float(p.get("marketCap", 0)),
                            "price_change_1h": float(p.get("priceChange", {}).get("h1", 0)),
                            "price_change_24h": float(p.get("priceChange", {}).get("h24", 0)),
                            "created_at": p.get("pairCreatedAt"),
                            "dex": p.get("dexId", ""),
                            "pair_address": p.get("pairAddress", ""),
                            "chain": p.get("chainId", chain),
                        }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("DexScreener full fetch failed: %s", e)
        return None

    async def _fetch_geckoterminal_ohlcv(
        self, mint: str, chain: str = "solana", limit: int = 24
    ) -> list[dict[str, Any]] | None:
        """Fetch OHLCV data from GeckoTerminal (free, no auth)."""
        try:
            import httpx

            network = "solana" if chain == "solana" else "eth"
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Get pool address first
                resp = await client.get(
                    f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{mint}/pools",
                    params={"limit": 1},
                )
                if resp.status_code != 200:
                    return None
                pools = resp.json().get("data", [])
                if not pools:
                    return None
                pool_addr = pools[0].get("id", "")
                if not pool_addr:
                    return None

                # Get OHLCV
                ohlcv_resp = await client.get(
                    f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_addr}/ohlcv/hour",
                    params={"limit": limit},
                )
                if ohlcv_resp.status_code == 200:
                    data = ohlcv_resp.json().get("data", {})
                    attrs = data.get("attributes", {})
                    ohlcv_list = attrs.get("ohlcv_list", [])
                    if ohlcv_list:
                        return [
                            {
                                "timestamp": c[0],
                                "open": c[1],
                                "high": c[2],
                                "low": c[3],
                                "close": c[4],
                                "volume": c[5],
                            }
                            for c in ohlcv_list
                        ]
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GeckoTerminal OHLCV failed: %s", e)
        return None
