"""Unified data layer providers package."""

import time

from app.caching_shield.unified.providers.funding_source import FundingSourceProviders
from app.caching_shield.unified.providers.price import PriceProviders
from app.caching_shield.unified.providers.risk_scan import RiskScanProviders
from app.caching_shield.unified.providers.token_meta import TokenMetaProviders
from app.caching_shield.unified.providers.tx_history import TxHistoryProviders
from app.caching_shield.unified.providers.wallet_balance import WalletBalanceProviders

__all__ = [
    "FundingSourceProviders",
    "PriceProviders",
    "RiskScanProviders",
    "TokenMetaProviders",
    "TxHistoryProviders",
    "WalletBalanceProviders",
]


class UnifiedDataProviders(
    PriceProviders,
    TokenMetaProviders,
    WalletBalanceProviders,
    RiskScanProviders,
    TxHistoryProviders,
    FundingSourceProviders,
):
    """All provider implementations grouped by data type."""

    def __init__(self) -> None:
        self._token_buckets: dict[str, tuple[float, float]] = {}  # provider -> (tokens, last_refill)
        self._bucket_lock = None  # lazy init

    def _get_lock(self):
        import asyncio

        if self._bucket_lock is None:
            self._bucket_lock = asyncio.Lock()
        return self._bucket_lock

    async def _check_rate(self, provider: str, rps: float) -> bool:
        async with self._get_lock():
            now = time.monotonic()
            bucket = self._token_buckets.get(provider)
            if not bucket:
                self._token_buckets[provider] = (rps, now)
                return True
            tokens, last = bucket
            tokens = min(rps, tokens + (now - last) * rps)
            self._token_buckets[provider] = (tokens - 1, now) if tokens >= 1 else (tokens, now)
            return tokens >= 1
