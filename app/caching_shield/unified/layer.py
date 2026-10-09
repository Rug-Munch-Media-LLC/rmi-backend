"""Unified Data Access Layer."""

import asyncio
import hashlib
import json
from typing import Any

from app.caching_shield.unified.cache import TieredCache
from app.caching_shield.unified.chains import ProviderChain
from app.caching_shield.unified.models import ToolResult
from app.caching_shield.unified.providers import UnifiedDataProviders
from app.core.logging import get_logger

logger = get_logger("unified_data")


class UnifiedDataLayer(UnifiedDataProviders):
    """Single entry point for ALL tool data calls.

    Provider implementations are inherited from UnifiedDataProviders mixins
    (price, token_meta, wallet_balance, risk_scan, tx_history, funding_source).
    This class owns orchestration: setup, fetch, rate limiting, TTL, stats.

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
        super().__init__()
        self._cache = TieredCache()
        self._providers: dict[str, ProviderChain] = {}
        self._setup_providers()
        self._token_buckets: dict[str, tuple[Any, ...]] = {}  # provider -> (tokens, last_refill)
        self._bucket_lock = asyncio.Lock()

    def _setup_providers(self) -> None:
        """Define fallback chains for every data type."""

        # ── PRICE ──
        self._providers["token_price"] = (
            ProviderChain("token_price", hedge_after_s=1.0)
            .add("jupiter", self._jupiter_price, 10)
            .add("solana_tracker", self._st_price, 3)
            .add("dexscreener", self._dexscreener_price, 5)
            .add("binance", self._binance_price, 20)
        )

        # ── TOKEN META ──
        self._providers["token_meta"] = (
            ProviderChain("token_meta")
            .add("helius_das", self._helius_das, 50)
            .add("solana_tracker", self._st_token, 3)
            .add("jupiter_list", self._jupiter_list, 10)
        )

        # ── WALLET BALANCE ──
        self._providers["wallet_balance"] = (
            ProviderChain("wallet_balance")
            .add("helius", self._helius_balance, 50)
            .add("quicknode", self._quicknode_balance, 25)
            .add("alchemy", self._alchemy_balance, 25)
            .add("publicnode", self._publicnode_balance, 15)
        )

        # ── RISK SCAN ──
        self._providers["risk_scan"] = (
            ProviderChain("risk_scan")
            .add("goplus", self._goplus, 5)
            .add("rugcheck", self._rugcheck, 5)
            .add("honeypot", self._honeypot, 3)
            .add("local_labels", self._local_labels, 999)
        )

        # ── TX HISTORY ──
        self._providers["tx_history"] = (
            ProviderChain("tx_history")
            .add("helius", self._helius_txs, 50)
            .add("solana_tracker", self._st_wallet, 3)
            .add("publicnode", self._publicnode_txs, 15)
        )

        # ── FUNDING SOURCE (EVM) ──
        self._providers["funding_source"] = (
            ProviderChain("funding_source")
            .add("blockscout", self._blockscout, 5)
            .add("etherscan", self._etherscan, 5)
            .add("public_rpc", self._evm_rpc, 10)
        )

    async def fetch(self, data_type: str, **kwargs: Any) -> Any | None:
        """Fetch data with cache → rate limit → provider chain.

        Args:
            data_type: One of the registered provider chains
            **kwargs: Passed to each provider function

        Returns:
            ToolResult with data, source, caching info
        """
        chain = self._providers.get(data_type)
        if not chain:
            return None

        # Cache key
        cache_key = hashlib.sha256(
            f"{data_type}:{json.dumps(kwargs, sort_keys=True, default=str)}".encode()
        ).hexdigest()[:24]

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
            ttl = self._ttl_for(data_type)
            await self._cache.set(cache_key, result.data, ttl)

        return result

    def _ttl_for(self, dt: str) -> int:
        return {
            "token_price": 15,
            "token_meta": 120,
            "wallet_balance": 10,
            "risk_scan": 300,
            "tx_history": 30,
            "funding_source": 3600,
        }.get(dt, 60)

    # ═══════════════════════════════════════════════════════════════════════
    # STATS
    # ═══════════════════════════════════════════════════════════════════════

    def stats(self) -> dict[str, Any]:
        return {
            "cache_hits": self._cache.hits,
            "cache_misses": self._cache.misses,
            "providers": {k: len(v._providers) for k, v in self._providers.items()},
            "data_types": len(self._providers),
        }


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_layer: UnifiedDataLayer | None = None


def get_data_layer() -> UnifiedDataLayer:
    global _layer
    if _layer is None:
        _layer = UnifiedDataLayer()
    return _layer
