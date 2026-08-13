"""
Unified Data Access Layer - Single entry point for ALL tool data calls.

Every x402 tool, MCP tool, scanner, and API endpoint routes through here.
Cache-first, rate-limited, multi-provider fallback. Never hit an API raw.

Architecture:
  Tool call → UnifiedDataLayer → cache check → rate limiter → provider chain → result

No tool anywhere in the system makes a raw HTTP call. Period.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("unified_data")

# ═══════════════════════════════════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════════════════════════════════


class TieredCache:
    """L1 in-memory cache."""

    def __init__(self, max_l1: int = 2048) -> None:
        self._l1 = {}
        self._max = max_l1
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> Any | None:
        entry = self._l1.get(key)
        if entry:
            expiry, data = entry
            if time.monotonic() < expiry:
                self.hits += 1
                return data
            del self._l1[key]
        self.misses += 1
        return None

    async def set(self, key: str, data: Any, ttl: float) -> None:
        self._l1[key] = (time.monotonic() + ttl, data)
        if len(self._l1) > self._max:
            oldest = min(self._l1.keys(), key=lambda k: self._l1[k][0])
            del self._l1[oldest]


@dataclass
class ToolResult:
    """Result from a tool data call with provenance tracking."""

    data: Any
    source: str  # which provider returned the data
    cached: bool = False
    fallback_used: bool = False
    latency_ms: float = 0
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "source": self.source,
            "cached": self.cached,
            "fallback": self.fallback_used,
            "latency_ms": round(self.latency_ms, 1),
            "confidence": round(self.confidence, 2),
        }


# ═══════════════════════════════════════════════════════════════════════════
# PROVIDER CHAINS
# ═══════════════════════════════════════════════════════════════════════════


class ProviderChain:
    """Ordered list of data providers with fallback."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._providers: list[tuple[Any, ...]] = []  # (name, fn, rate_rps)

    def add(self, name: str, fn: Callable[..., Any], rate_rps: float = 10) -> ProviderChain:
        self._providers.append((name, fn, rate_rps))
        return self

    async def fetch(self, **kwargs: Any) -> ToolResult | None:
        start = time.monotonic()
        for prov_name, fn, _rps in self._providers:
            try:
                data = await fn(**kwargs)
                if data is not None:
                    return ToolResult(
                        data=data,
                        source=prov_name,
                        latency_ms=(time.monotonic() - start) * 1000,
                        fallback_used=(prov_name != self._providers[0][0]),
                    )
            except Exception as e:
                logger.debug(f"Provider {prov_name} failed: {e}")
                continue
        return None


# ═══════════════════════════════════════════════════════════════════════════
# UNIFIED LAYER
# ═══════════════════════════════════════════════════════════════════════════


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
        self._providers: dict[str, ProviderChain] = {}
        self._setup_providers()
        self._token_buckets: dict[str, tuple[Any, ...]] = {}  # provider -> (tokens, last_refill)
        self._bucket_lock = asyncio.Lock()

    def _setup_providers(self) -> None:
        """Define fallback chains for every data type."""

        # ── PRICE ──
        self._providers["token_price"] = (
            ProviderChain("token_price")
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

    async def fetch(self, data_type: str, **kwargs: Any) -> ToolResult | None:
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
            "token_price": 8,
            "token_meta": 120,
            "wallet_balance": 10,
            "risk_scan": 300,
            "tx_history": 30,
            "funding_source": 3600,
        }.get(dt, 60)

    # ═══════════════════════════════════════════════════════════════════════
    # RATE LIMITER
    # ═══════════════════════════════════════════════════════════════════════

    async def _check_rate(self, provider: str, rps: float) -> bool:
        async with self._bucket_lock:
            now = time.monotonic()
            bucket = self._token_buckets.get(provider)
            if not bucket:
                self._token_buckets[provider] = (rps, now)
                return True
            tokens, last = bucket
            tokens = min(rps, tokens + (now - last) * rps)
            self._token_buckets[provider] = (tokens - 1, now) if tokens >= 1 else (tokens, now)
            return tokens >= 1

    # ═══════════════════════════════════════════════════════════════════════
    # PROVIDER IMPLEMENTATIONS (same as data_fallback.py but simplified)
    # ═══════════════════════════════════════════════════════════════════════

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

    async def _helius_das(self, mint: str, **kw: Any) -> dict[str, Any | None] | None:
        from app.caching_shield.helius_das import get_helius_das

        r = await get_helius_das().get_token_metadata(mint)
        if r:
            c = r.get("content", {}).get("metadata", {})
            t = r.get("token_info", {})
            return {"name": c.get("name"), "symbol": c.get("symbol"), "decimals": t.get("decimals")}
        return None

    async def _st_token(self, mint: str, **kw: Any) -> Any:
        from app.caching_shield.solana_tracker import get_solana_tracker

        r = await get_solana_tracker().get_token(mint)
        return {"name": r.get("token", {}).get("name")} if r else None

    async def _jupiter_list(self, mint: str, **kw: Any) -> dict[str, Any | None] | None:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get("https://token.jup.ag/strict")
            if r.status_code == 200:
                for t in r.json():
                    if t.get("address") == mint:
                        return {"name": t.get("name"), "symbol": t.get("symbol")}
        return None

    async def _helius_balance(self, address: str, **kw: Any) -> dict[str, Any] | None:
        from app.consensus_rpc import get_consensus_rpc

        r = await get_consensus_rpc().get_balance(address)
        if r and r.value:
            v = r.value if isinstance(r.value, (int, float)) else r.value.get("value", 0)
            return {"balance_lamports": v}
        return None

    async def _quicknode_balance(self, address: str, **kw: Any) -> Any:
        return await self._helius_balance(address)

    async def _alchemy_balance(self, address: str, **kw: Any) -> Any:
        return await self._helius_balance(address)

    async def _publicnode_balance(self, address: str, **kw: Any) -> dict[str, Any | None] | None:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.post(
                "https://solana-rpc.publicnode.com",
                json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]},
            )
            if r.status_code == 200:
                return {"balance_lamports": r.json().get("result", {}).get("value", 0)}
        return None

    async def _goplus(self, address: str, chain: str = "solana", **kw: Any) -> dict[str, bool] | None:
        key = os.getenv("GOPLUS_API_KEY", "")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.gopluslabs.io/api/v1/token_security/{chain}",
                params={"contract_addresses": address},
                headers={"Authorization": f"Bearer {key}"} if key else {},
            )
            if r.status_code == 200:
                d = r.json().get("result", {}).get(address.lower(), {})
                return {
                    "is_honeypot": d.get("is_honeypot") == "1",
                    "risk_score": 100 - int(d.get("trust_score", 50)),
                }
        return None

    async def _rugcheck(self, address: str, **kw: Any) -> dict[str, Any | None] | None:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.rugcheck.xyz/v1/tokens/{address}/report")
            if r.status_code == 200:
                d = r.json()
                return {
                    "score": d.get("score", 0),
                    "risks": [r.get("name") for r in d.get("risks", []) if r.get("score", 0) > 1000],
                }
        return None

    async def _honeypot(self, address: str, **kw: Any) -> dict[str, Any | None] | None:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.honeypot.is/v2/IsHoneypot?address={address}&chainID=1")
            if r.status_code == 200:
                return {"is_honeypot": r.json().get("honeypotResult", {}).get("isHoneypot", False)}
        return None

    async def _local_labels(self, address: str, **kw: Any) -> Any | None:
        try:
            from app.wallet_label_loader import lookup_wallet_label

            label = lookup_wallet_label(address)
            return {"label": label} if label else None
        except (AttributeError, ImportError, httpx.HTTPError):
            return None

    async def _helius_txs(self, address: str, **kw: Any) -> dict[str, Any | None] | None:
        key = os.getenv("HELIUS_API_KEY", "")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://mainnet.helius-rpc.com/?api-key={key}",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [address, {"limit": 20}],
                },
            )
            if r.status_code == 200:
                return {"signatures": r.json().get("result", [])}
        return None

    async def _st_wallet(self, address: str, **kw: Any) -> Any:
        from app.caching_shield.solana_tracker import get_solana_tracker

        r = await get_solana_tracker().get_wallet(address)
        return {"total_value": r.get("total")} if r else None

    async def _publicnode_txs(self, address: str, **kw: Any) -> Any:
        return await self._helius_txs(address)

    async def _blockscout(self, address: str, chain_id: int = 1, **kw: Any) -> dict[str, Any] | None:
        from app.caching_shield.funding_tracer import trace_funding_source

        r = await trace_funding_source(address, chain_id)
        if r and r.source_address:
            return {"source": r.source_address, "type": r.source_type, "confidence": r.confidence}
        return None

    async def _etherscan(self, address: str, chain_id: int = 1, **kw: Any) -> dict[str, int] | None:
        key = os.getenv("ETHERSCAN_API_KEY", "")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.etherscan.io/api",
                params={
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "apikey": key,
                    "page": 1,
                    "offset": 5,
                },
            )
            if r.status_code == 200 and r.json().get("status") == "1":
                return {"txs": len(r.json().get("result", []))}
        return None

    async def _evm_rpc(self, address: str, chain_id: int = 1, **kw: Any) -> dict[str, Any] | None:
        from app.consensus_rpc import get_consensus_rpc

        r = await get_consensus_rpc().evm_query_with_consensus(chain_id, "eth_getBalance", [address, "latest"])
        if r and r.value:
            return {"balance": int(r.value, 16) / 1e18 if isinstance(r.value, str) else r.value}
        return None

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

    async def _cmc_price(self, mint: str, **kw: Any) -> dict[str, Any] | None:
        import os

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
