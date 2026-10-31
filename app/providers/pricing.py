"""Token price and token metadata lookups."""

from __future__ import annotations

from typing import Any

from app.providers.base import ProviderBase, logger


class PricingMixin(ProviderBase):
    """Price (Birdeye → DexScreener) and metadata (Helius → DexScreener)."""

    async def get_token_price(self, mint: str) -> float | None:
        """Get token price - Birdeye → DexScreener → Jupiter."""
        cache = self._get_cache()
        cached = await cache.get("price", mint)
        if cached is not None:
            return cached

        # 1. Birdeye
        try:
            if self.birdeye:
                data = await self.birdeye.get_price(mint)
                price = data.get("data", {}).get("value")
                if price:
                    await cache.set("price", price, mint, ttl=60)
                    return price
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.debug("Unified provider connection error: %s", e)

        # 2. DexScreener
        try:
            import httpx

            async with httpx.AsyncClient(timeout=8.0) as http_client:
                r = await http_client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        price = float(pairs[0].get("priceUsd", 0))
                        await cache.set("price", price, mint, ttl=60)
                        return price
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("Unified provider HTTP error: %s", e)

        await cache.set("price", None, mint, ttl=60)
        return None

    async def get_token_metadata(self, mint: str) -> dict[str, Any]:
        """Get token metadata - Helius → Birdeye → DexScreener."""
        cache = self._get_cache()
        cached = await cache.get("meta", mint)
        if cached:
            return cached

        # 1. Helius
        try:
            from app.chain_client import get_chain_client

            client = get_chain_client()
            meta = await client.get_token_metadata(mint)
            if meta:
                result = {
                    "name": meta.get("name", ""),
                    "symbol": meta.get("symbol", ""),
                    "source": "helius",
                }
                await cache.set("meta", result, mint, ttl=600)
                return result
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.debug("Unified provider connection error: %s", e)

        # 2. DexScreener
        try:
            import httpx

            async with httpx.AsyncClient(timeout=8.0) as http_client:
                r = await http_client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        base = pairs[0].get("baseToken", {})
                        result = {
                            "name": base.get("name", ""),
                            "symbol": base.get("symbol", ""),
                            "source": "dexscreener",
                        }
                        await cache.set("meta", result, mint, ttl=600)
                        return result
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("Unified provider HTTP error: %s", e)

        result = {"name": mint[:8], "symbol": "???", "source": "fallback"}
        await cache.set("meta", result, mint, ttl=600)
        return result
