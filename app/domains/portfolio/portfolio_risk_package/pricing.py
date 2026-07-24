"""
Portfolio Risk — Price Oracle
Simple Coingecko-based price estimation with in-memory caching.
"""
from typing import Any

from app.core.logging import get_logger
from app.domains.portfolio.portfolio_risk_package.constants import CHAIN_CONFIGS

logger = get_logger(__name__)


class PriceOracle:
    def __init__(self) -> None:
        self._cache: dict[str, float] = {
            "ETH": 2800.0, "BTC": 68000.0, "SOL": 145.0, "BNB": 580.0,
            "MATIC": 0.55, "AVAX": 32.0, "MNT": 0.85, "FTM": 0.68,
            "USDC": 1.0, "USDT": 1.0, "DAI": 1.0, "WBTC": 68000.0,
            "LINK": 14.0, "UNI": 7.5, "PEPE": 0.000012, "SHIB": 0.000025,
            "AERO": 0.85, "CAKE": 2.10,
        }
        self._cache_time: float = 0
        self._cache_ttl = 300

    async def get_price(self, symbol: str) -> float:
        upper = symbol.upper()
        cached = self._cache.get(upper)
        if cached is not None:
            return cached
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                url = f"https://api.coingecko.com/api/v3/simple/price?ids={upper.lower()}&vs_currencies=usd"
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    price = data.get(upper.lower(), {}).get("usd")
                    if price:
                        self._cache[upper] = float(price)
                        return float(price)
        except (httpx.HTTPError):
            pass
        return 0.0

    def estimate_native_price(self, chain: str) -> float:
        symbol = CHAIN_CONFIGS[chain]["native_symbol"]
        return self._cache.get(symbol.upper(), 0.0)
