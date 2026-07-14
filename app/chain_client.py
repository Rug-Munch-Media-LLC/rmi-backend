"""
Multi-Provider Chain Client - Rate-limited with fallback rotation.
Helius (primary) → Birdeye → QuickNode → DexScreener.
Free tier: ~5 req/sec. Caching layer in unified_provider.
"""

import asyncio
from app.core.logging import get_logger
import os
import time
from typing import Any

import httpx

logger = get_logger(__name__)

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
    def __init__(self, rate: float = 5.0, burst: int = 10):
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

    def __init__(self):
        self.bucket = TokenBucket(rate=5.0, burst=10)
        self._helius_idx = 0

    async def _rate_limit(self):
        wait = await self.bucket.acquire()
        if wait > 0:
            await asyncio.sleep(wait)

    def _next_key(self) -> str | None:
        if not HELIUS_KEYS:
            return None
        key = HELIUS_KEYS[self._helius_idx % len(HELIUS_KEYS)]
        self._helius_idx += 1
        return key

    async def rpc_call(self, method: str, params: list[Any]) -> dict | None:
        """Rate-limited RPC with Helius fallback."""
        await self._rate_limit()

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
                            return r.json()
                        if r.status_code == 429:
                            logger.debug("Helius rate limited, rotating key")
                            continue
                except Exception:
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
                        return r.json()
            except Exception:
                pass

        return None

    async def get_signatures(self, address: str, limit: int = 20) -> list[dict]:
        result = await self.rpc_call("getSignaturesForAddress", [address, {"limit": limit}])
        if result and "result" in result:
            return result["result"]
        return []

    async def get_balance(self, address: str) -> float:
        result = await self.rpc_call("getBalance", [address])
        if result and "result" in result:
            return result["result"].get("value", 0) / 1e9
        return 0.0

    async def get_token_metadata(self, mint: str) -> dict | None:
        if not HELIUS_KEYS:
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"https://api.helius.xyz/v0/token-metadata/?api-key={HELIUS_KEYS[0]}",
                    json={"mintAccounts": [mint]},
                )
                if r.status_code == 200:
                    data = r.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        return data[0]
        except Exception:
            pass
        return None


# Singleton
_client: ChainClient | None = None


def get_chain_client() -> ChainClient:
    global _client
    if _client is None:
        _client = ChainClient()
    return _client
