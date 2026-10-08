"""Unified data layer with tiered caching and provider fallback chains."""

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger
from app.caching_shield.unified.models import ToolResult
from app.caching_shield.unified.providers import (
    PriceProviders,
    TokenMetaProviders,
    WalletBalanceProviders,
    RiskScanProviders,
    TxHistoryProviders,
    FundingSourceProviders,
)
from app.caching_shield.unified.core import (
    TieredCache,
    ProviderChain,
    TokenBucketRateLimiter,
    make_cache_key,
    ttl_for,
)

logger = get_logger("unified_data_layer")


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
        self._rate_limiter = TokenBucketRateLimiter()

    async def _check_rate(self, provider: str, rps: float) -> bool:
        return await self._rate_limiter.check_rate(provider, rps)


class UnifiedDataLayer:
    """Single entry point for ALL tool data calls.

    Usage:
        layer = get_data_layer()

        # Token price with multi-provider fallback
        result = await layer.fetch("token_price", mint="So111...")

        # Wallet balance
        result = await layer.fetch("wallet_balance", address="7EcD...")

        # Risk scan
        result = await layer.fetch("risk_scan", address="0x...", chain="ethereum")
    """

    def __init__(self) -> None:
        self._cache = TieredCache()
        self._providers = UnifiedDataProviders()
        self._chains: dict[str, ProviderChain] = {}
        self._setup_providers()

    def _setup_providers(self) -> None:
        """Define fallback chains for every data type."""

        # ── PRICE ──
        self._chains["token_price"] = (
            ProviderChain("token_price", hedge_after_s=1.0)
            .add("jupiter", self._providers._jupiter_price, 10)
            .add("solana_tracker", self._providers._st_price, 3)
            .add("dexscreener", self._providers._dexscreener_price, 5)
            .add("binance", self._providers._binance_price, 20)
            .add("coinmarketcap", self._providers._cmc_price, 5)
        )

        # ── TOKEN META ──
        self._chains["token_meta"] = (
            ProviderChain("token_meta")
            .add("helius_das", self._providers._helius_das, 50)
            .add("solana_tracker", self._providers._st_token, 3)
            .add("jupiter_list", self._providers._jupiter_list, 10)
        )

        # ── WALLET BALANCE ──
        self._chains["wallet_balance"] = (
            ProviderChain("wallet_balance")
            .add("helius", self._providers._helius_balance, 50)
            .add("quicknode", self._providers._quicknode_balance, 25)
            .add("alchemy", self._providers._alchemy_balance, 25)
            .add("publicnode", self._providers._publicnode_balance, 15)
        )

        # ── RISK SCAN ──
        self._chains["risk_scan"] = (
            ProviderChain("risk_scan")
            .add("goplus", self._providers._goplus, 5)
            .add("rugcheck", self._providers._rugcheck, 5)
            .add("honeypot", self._providers._honeypot, 3)
            .add("local_labels", self._providers._local_labels, 999)
        )

        # ── TX HISTORY ──
        self._chains["tx_history"] = (
            ProviderChain("tx_history")
            .add("helius", self._providers._helius_txs, 50)
            .add("solana_tracker", self._providers._st_wallet, 3)
            .add("publicnode", self._providers._publicnode_txs, 15)
        )

        # ── FUNDING SOURCE (EVM) ──
        self._chains["funding_source"] = (
            ProviderChain("funding_source")
            .add("blockscout", self._providers._blockscout, 5)
            .add("etherscan", self._providers._etherscan, 5)
            .add("public_rpc", self._providers._evm_rpc, 10)
        )

    async def fetch(self, data_type: str, **kwargs: Any) -> ToolResult | None:
        """Fetch data with cache → rate limit → provider chain.

        Args:
            data_type: One of the registered provider chains
            **kwargs: Passed to each provider function

        Returns:
            ToolResult with data, source, caching info
        """
        chain = self._chains.get(data_type)
        if not chain:
            return None

        # Cache key
        cache_key = make_cache_key(data_type, **kwargs)

        # Check cache
        cached = await self._cache.get(cache_key)
        if cached is not None:
            self._cache.hits += 1
            return ToolResult(data=cached, source="cache", cached=True, latency_ms=0)

        self._cache.misses += 1

        # Fetch from provider chain
        result = await chain.fetch(**kwargs)
        if result:
            # Cache with TTL based on data type
            await self._cache.set(cache_key, result.data, ttl_for(data_type))
            return ToolResult(
                data=result.data,
                source=getattr(chain, "_last_provider", "unknown"),
                latency_ms=result.latency_ms,
                fallback_used=result.fallback_used,
                confidence=result.confidence,
            )

        return None

    # ════════════════════════════════════════════════════════════════════════════
    # STATS
    # ════════════════════════════════════════════════════════════════════════════

    def stats(self) -> dict[str, Any]:
        return {
            "cache_hits": self._cache.hits,
            "cache_misses": self._cache.misses,
            "providers": {k: len(v._providers) for k, v in self._chains.items()},
            "data_types": len(self._chains),
        }


# ═════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═════════════════════════════════════════════════════════════════════════════

_layer: UnifiedDataLayer | None = None


def get_data_layer() -> UnifiedDataLayer:
    global _layer
    if _layer is None:
        _layer = UnifiedDataLayer()
    return _layer