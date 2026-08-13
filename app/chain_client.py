"""
Multi-Provider Chain Client - Rate-limited with fallback rotation.
Helius (primary) → Birdeye → QuickNode → DexScreener.
Free tier: ~5 req/sec. Caching layer in unified_provider.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from app.core.circuit_breaker import AsyncCircuitBreaker, CircuitBreakerOpenError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Circuit breaker for Solana RPC (Helius + QuickNode)
_chain_cb = AsyncCircuitBreaker(
    name="chain_client",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)

# ── Provider Keys ──────────────────────────────────────────

HELIUS_KEYS = [
    os.getenv("HELIUS_API_KEY", ""),
    os.getenv("HELIUS_API_KEY_2", ""),
    os.getenv("HELIUS_API_KEY_3", ""),
]
HELIUS_KEYS = [k for k in HELIUS_KEYS if k and k != "your_helius_key_here"]

QUICKNODE_KEY = os.getenv("QUICKNODE_KEY", "")

# ── Rate Limiter ───────────────────────────────────────────


class TokenBucket:
    def __init__(self, rate: float = 5.0, burst: int = 10) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return 0.0
            wait = (1.0 - self.tokens) / self.rate
            self.tokens = 0.0
            return wait


class ChainClient:
    """Rate-limited Solana RPC with Helius primary + QuickNode fallback."""

    def __init__(self) -> None:
        self.bucket = TokenBucket(rate=5.0, burst=10)
        self._helius_idx = 0

    async def _rate_limit(self) -> None:
        wait = await self.bucket.acquire()
        if wait > 0:
            await asyncio.sleep(wait)

    def _next_key(self) -> str | None:
        if not HELIUS_KEYS:
            return None
        key = HELIUS_KEYS[self._helius_idx % len(HELIUS_KEYS)]
        self._helius_idx += 1
        return key

    async def rpc_call(self, method: str, params: list[Any]) -> dict[str, Any] | None:
        """Rate-limited RPC with Helius fallback and circuit breaker."""
        # Circuit breaker check — fail fast if both providers are down
        if not await _chain_cb.can_call():
            raise CircuitBreakerOpenError(
                _chain_cb.name,
                _chain_cb.state,
                "Chain RPC circuit OPEN — failing fast",
            )

        await self._rate_limit()

        all_failed = True

        # Try Helius
        if HELIUS_KEYS:
            for _ in range(len(HELIUS_KEYS)):
                key = self._next_key()
                if not key:
                    break
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        r = await client.post(
                            f"https://mainnet.helius-rpc.com/?api-key={key}",
                            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                        )
                        if r.status_code == 200:
                            all_failed = False
                            await _chain_cb.record_success()
                            return r.json()
                        if r.status_code == 429:
                            logger.debug("Helius rate limited, rotating key")
                            continue
                except (ConnectionError, TimeoutError, httpx.HTTPError):
                    continue

        # Try QuickNode fallback
        if QUICKNODE_KEY and QUICKNODE_KEY != "your_quicknode_key_here":
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    r = await client.post(
                        f"https://docs-demo.solana-mainnet.quiknode.pro/{QUICKNODE_KEY}/",
                        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    )
                    if r.status_code == 200:
                        all_failed = False
                        await _chain_cb.record_success()
                        return r.json()
            except httpx.HTTPError:
                pass

        if all_failed:
            await _chain_cb.record_failure(is_server_error=True)
        return None

    async def get_signatures(self, address: str, limit: int = 20) -> list[dict[str, Any]]:
        result = await self.rpc_call("getSignaturesForAddress", [address, {"limit": limit}])
        if result and "result" in result:
            return result["result"]
        return []

    async def get_transactions(self, signatures: list[str]) -> list[dict[str, Any]]:
        """Fetch parsed transactions for a batch of signatures (best-effort)."""
        txs: list[dict[str, Any]] = []
        for sig in signatures[:5]:
            result = await self.rpc_call("getTransaction", [sig, {"maxSupportedTransactionVersion": 0}])
            if result and "result" in result and result["result"] is not None:
                txs.append(result["result"])
        return txs

    async def get_token_accounts(self, address: str) -> list[dict[str, Any]]:
        """Fetch SPL token accounts owned by an address (jsonParsed)."""
        result = await self.rpc_call(
            "getTokenAccountsByOwner",
            [
                address,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
        )
        if result and "result" in result:
            return result["result"].get("value", [])
        return []

    async def get_balance(self, address: str) -> float:
        result = await self.rpc_call("getBalance", [address])
        if result and "result" in result:
            return result["result"].get("value", 0) / 1e9
        return 0.0

    async def get_token_metadata(self, mint: str) -> dict[str, Any] | None:
        if not HELIUS_KEYS:
            return None
        # Circuit breaker check
        if not await _chain_cb.can_call():
            stale = _chain_cb.get_stale_result()
            if stale is not None:
                return stale
            return None  # Non-critical endpoint, just return None instead of raising

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEYS[0]}",
                    json={
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "getAsset",
                        "params": {"id": mint},
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    result = data.get("result") if isinstance(data, dict) else None
                    if result and isinstance(result, dict):
                        await _chain_cb.record_success()
                        return result
                    await _chain_cb.record_failure(is_server_error=False)
                    return None
                elif r.status_code >= 500:
                    await _chain_cb.record_failure(is_server_error=True)
                    return None
                else:
                    await _chain_cb.record_failure(is_server_error=False)
                    return None
        except (ConnectionError, OSError, TimeoutError):
            await _chain_cb.record_failure(is_server_error=True)
        return None


# Singleton
_client: ChainClient | None = None


def get_chain_client() -> ChainClient:
    global _client
    if _client is None:
        _client = ChainClient()
    return _client
