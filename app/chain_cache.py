"""
Chain Data Cache - In-memory TTL cache for RPC results.
Minimizes Helius API calls on free tier (100k credits/month).
Includes startup warming for hot wallets.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import asdict
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Hot Wallets to Pre-Warm ──────────────────────────────
# Top wallets by volume + known infrastructure that get hit on every scan.
# Pre-warming saves 5+ API calls per scan that would otherwise be cold misses.

HOT_WALLETS: list[str] = [
    # Top Solana DEX/infrastructure (hit on every RugMaps/scan)
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium V4 AMM
    "JUP6LkbZbjS1jKKwapdHNy7bKNkD5Ka6TqPnVGFVeYf",  # Jupiter V6
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  # Orca Whirlpool
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",  # Binance 1
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",  # Binance 2
    "2ojv9BAiHghv5o9y2k6k3m7o9TdsrKJqXvWyCdkUWNmB",  # Binance 3
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # Pump.fun
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token Program
    # Top Ethereum infrastructure (cross-chain)
    "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",  # Uniswap V2
    "0xBE0eB53FC46b790099138e3d32C721856d41e865",  # Binance 7
    "0xF977814e90dA44bFA03b6295A0616a897441aceC",  # Binance 8
    "0x28C6c06298d514Db089934071355E5743bf21d60",  # Binance 14
]

HOT_TOKENS: list[str] = [
    # Top tokens scanned for rugs (Solana)
    "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",  # JTO
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G5wUGxvZq2moaomYR",  # USDC
    "So11111111111111111111111111111111111111112",  # SOL wrapped
    "mSoLzYCxHdYgbz9JUv7WoxX3LEdh3K8VZJIQz3EhR9J",  # mSOL
    "JUPyiwrYJF8UP9YuYuB8mH3WGfnCfNJiQ3W9vHJ7kKo",  # JUP
    # Top tokens (Ethereum chain IDs)
    "1",  # ETH chain
    "56",  # BSC chain
    "8453",  # Base chain
]


class ChainCache:
    """LRU cache with TTL for chain data. Max 500 entries, 5-min default TTL."""

    def __init__(self, max_size: int = 500, default_ttl: int = 300) -> None:
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._warmed = False

    def _make_key(self, method: str, *args) -> str:
        return f"{method}:{':'.join(str(a)[:100] for a in args)}"

    async def get(self, method: str, *args) -> Any | None:
        key = self._make_key(method, *args)
        async with self._lock:
            if key in self._cache:
                expiry, value = self._cache[key]
                if time.time() < expiry:
                    self._cache.move_to_end(key)
                    return value
                del self._cache[key]
        return None

    async def set(self, method: str, value: Any, *args, ttl: int | None = None) -> None:
        key = self._make_key(method, *args)
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[key] = (time.time() + (ttl or self.default_ttl), value)

    async def stats(self) -> dict[str, Any]:
        async with self._lock:
            # Count how many are from warming vs live
            warmed = sum(1 for k in self._cache if k.startswith("warm:"))
            return {
                "size": len(self._cache),
                "max": self.max_size,
                "warmed_entries": warmed,
                "utilization_pct": round(len(self._cache) / self.max_size * 100, 1),
            }

    async def warm(self) -> None:
        """Pre-populate cache with known hot addresses to avoid cold-start misses."""
        if self._warmed:
            return

        logger.info(f"Warming cache with {len(HOT_WALLETS)} wallets and {len(HOT_TOKENS)} tokens...")

        # Mark entity registry entries as known infrastructure (avoids repeated lookups)
        try:
            from app.entity_registry import get_entity_registry

            registry = get_entity_registry()
            for addr in HOT_WALLETS:
                match = registry.classify_address(addr)
                if match and match.entity_type != "unknown":
                    await self.set("warm_entity", asdict(match), addr, ttl=600)
        except Exception as e:
            logger.debug(f"Entity warm skipped: {e}")

        # Mark hot tokens as known-safe to avoid spam registry checks
        for token in HOT_TOKENS:
            await self.set("warm_token", {"address": token, "preloaded": True}, token, ttl=600)

        # Mark spam registry as loaded (it loads on first check anyway)
        try:
            from app.spam_registry import get_spam_registry

            sr = get_spam_registry()
            sr._ensure_loaded()  # Force load
            await self.set("warm_spam", {"loaded": True, "total": sr.stats()["total"]}, ttl=600)
        except Exception as e:
            logger.debug(f"Spam warm skipped: {e}")

        # Mark GNN as available
        try:
            from app.fraud_gnn import get_fraud_gnn

            gnn = get_fraud_gnn()
            gnn._ensure_loaded()
            await self.set("warm_gnn", {"loaded": True, "model": gnn.model_type}, ttl=600)
        except Exception as e:
            logger.debug(f"GNN warm skipped: {e}")

        # Warm CoinGecko market data if available
        try:
            from app.coingecko_connector import get_coingecko_connector

            cg = get_coingecko_connector()
            trending = await cg.get_trending()
            if trending:
                await self.set("warm_trending", trending, ttl=300)
                logger.info(f"Warmed trending: {len(trending)} tokens")
            global_data = await cg.get_global_metrics()
            if global_data:
                await self.set("warm_global", global_data, ttl=300)
                logger.info(f"Warmed global: BTC dom {global_data.get('btc_dominance', '?')}%")
        except Exception as e:
            logger.debug(f"CoinGecko warm skipped: {e}")

        self._warmed = True
        logger.info(f"Cache warmed: {len(self._cache)}/{self.max_size} entries")


# Singleton
_cache: ChainCache | None = None


def get_chain_cache() -> ChainCache:
    global _cache
    if _cache is None:
        _cache = ChainCache()
    return _cache
