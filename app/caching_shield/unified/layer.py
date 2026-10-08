"""Provider implementations for unified data layer."""

import hashlib
import json
import os
import time
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger
from app.caching_shield.unified.models import ToolResult

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("unified_data_providers")


class UnifiedDataProviders:
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

    # ── PRICE ──
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
        return None

    # ── TOKEN META ──
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

    # ── WALLET BALANCE ──
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

    # ── RISK SCAN ──
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

    # ── TX HISTORY ──
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

    # ── FUNDING SOURCE (EVM) ──
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
            await self._cache.set(cache_key, result, ttl)
            return ToolResult(
                data=result,
                source=getattr(chain, '_last_provider', 'unknown'),
                latency_ms=0,
            )

        return None

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
            "providers": {k: len(v._providers) for k, v in self._chains.items()},
            "data_types": len(self._chains),
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