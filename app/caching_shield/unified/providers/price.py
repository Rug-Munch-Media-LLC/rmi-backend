"""Price providers for unified data layer."""

import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("unified_data_providers.price")


class PriceProviders:
    """Price provider implementations."""

    async def _jupiter_price(self, mint: str, **kw: Any) -> dict[str, int | float] | None:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"https://quote-api.jup.ag/v6/quote?inputMint={mint}&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=1000000000"
            )
            if r.status_code == 200:
                return {"price_usd": float(r.json().get("outAmount", 0)) / 1e6}
        return None

    async def _st_price(self, mint: str, **kw: Any) -> Any:
        from app.caching_shield.solana_tracker import get_solana_tracker

        r = await get_solana_tracker().get_price(mint)
        return {"price_usd": r.get("price")} if r else None

    async def _dexscreener_price(self, mint: str, **kw: Any) -> dict[str, float] | None:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                if pairs:
                    return {"price_usd": float(pairs[0].get("priceUsd", 0))}
        return None

    async def _binance_price(self, mint: str, **kw: Any) -> dict[str, float] | None:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT")
            if r.status_code == 200:
                return {"price_usd": float(r.json().get("price", 0))}
        return None

    async def _cmc_price(self, mint: str, **kw: Any) -> dict[str, Any] | None:
        key = os.getenv("COINMARKETCAP_API_KEY", "")
        if not key:
            return None
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest",
                params={"symbol": "SOL", "convert": "USD"},
                headers={"X-CMC_PRO_API_KEY": key},
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                if "SOL" in data:
                    return {
                        "price_usd": data["SOL"]["quote"]["USD"]["price"],
                        "source": "coinmarketcap",
                    }
        return None