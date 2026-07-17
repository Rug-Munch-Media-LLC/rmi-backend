"""
Moralis Connector - Data API + Auth API.
Key 1: Data API (wallets, tokens, NFTs, streams, whale tracking)
Key 2: Auth API (Sign-In With Ethereum/Solana - Phantom, MetaMask, etc.)

Free tier: 40,000 API credits/month (~1,333/day).
Rate-limited to 3 req/sec with caching.
"""

import asyncio
import os
import time
from typing import Any

import httpx

from app.core.circuit_breaker import AsyncCircuitBreaker, CircuitBreakerOpenError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Circuit breaker for Moralis APIs
_moralis_cb = AsyncCircuitBreaker(
    name="moralis",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)

# ── API Keys ────────────────────────────────────────────────

MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")
MORALIS_API_KEY_2 = os.getenv("MORALIS_API_KEY_2", "")  # Data+Auth (org 510042)
MORALIS_API_KEY_3 = os.getenv("MORALIS_API_KEY_3", "")  # Data+Auth (org 516732)

DATA_URL = "https://deep-index.moralis.io/api/v2.2"
AUTH_URL = "https://authapi.moralis.io"
STREAM_URL = "https://api.moralis.io/stream"

SUPPORTED_EVM_CHAINS = {
    "eth": "0x1",
    "goerli": "0x5",
    "sepolia": "0xaa36a7",
    "polygon": "0x89",
    "mumbai": "0x13881",
    "bsc": "0x38",
    "bsc_testnet": "0x61",
    "avalanche": "0xa86a",
    "fantom": "0xfa",
    "arbitrum": "0xa4b1",
    "optimism": "0xa",
    "base": "0x2105",
    "gnosis": "0x64",
    "celo": "0xa4ec",
    "palm": "0x2a15c308d",
}

CHAIN_ID_MAP = {
    "eth": "1",
    "goerli": "5",
    "sepolia": "11155111",
    "polygon": "137",
    "mumbai": "80001",
    "bsc": "56",
    "bsc_testnet": "97",
    "avalanche": "43114",
    "fantom": "250",
    "arbitrum": "42161",
    "optimism": "10",
    "base": "8453",
    "gnosis": "100",
}


class MoralisConnector:
    """Moralis Data + Auth API connector."""

    def __init__(self):
        # Key rotation: spread requests across 2 orgs for 2x rate limit
        self._data_keys = [k for k in [MORALIS_API_KEY, MORALIS_API_KEY_2, MORALIS_API_KEY_3] if k]
        self._auth_keys = [k for k in [MORALIS_API_KEY_2, MORALIS_API_KEY_3] if k]
        self.api_key = self._data_keys[0] if self._data_keys else ""
        self.auth_key = self._auth_keys[0] if self._auth_keys else ""
        self._data_idx = 0
        self._auth_idx = 0
        self._cache: dict[str, tuple] = {}  # key → (data, timestamp)
        self._cache_ttl = 300  # 5 min
        self._last_request = 0.0
        self._min_interval = 0.33  # 3 req/sec

    def _chain_param(self, chain: str) -> str:
        """Convert chain name to Moralis chain param."""
        return chain if chain in SUPPORTED_EVM_CHAINS else "eth"

    def _chain_id(self, chain: str) -> str:
        """Convert chain name to chainId for auth."""
        return CHAIN_ID_MAP.get(chain, "1")

    async def _rate_limit(self):
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()

    def _cached(self, key: str) -> Any | None:
        if key in self._cache:
            data, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return data
        return None

    def _set_cache(self, key: str, data: Any):
        self._cache[key] = (data, time.time())
        # Evict old entries
        if len(self._cache) > 500:
            oldest = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest]

    async def _get(
        self, url: str, key: str | None = None, use_auth_key: bool = False
    ) -> dict | None:
        """Rate-limited GET with caching, key rotation, and circuit breaker."""
        cache_key = f"get:{url}"
        if key is None:
            key = cache_key
        cached = self._cached(key)
        if cached is not None:
            return cached

        # Circuit breaker check — serve stale cache if OPEN
        if not await _moralis_cb.can_call():
            stale = _moralis_cb.get_stale_result()
            if stale is not None:
                return stale
            raise CircuitBreakerOpenError(
                _moralis_cb.name,
                _moralis_cb.state,
                f"Moralis circuit OPEN — failing fast",
            )

        await self._rate_limit()
        # Rotate keys for load balancing (2 orgs = 2x rate limit)
        keys = self._auth_keys if use_auth_key else self._data_keys
        if not keys:
            return None
        idx = self._auth_idx if use_auth_key else self._data_idx
        api_key = keys[idx % len(keys)]
        if use_auth_key:
            self._auth_idx = idx + 1
        else:
            self._data_idx = idx + 1

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    url,
                    headers={
                        "X-API-Key": api_key,
                        "Accept": "application/json",
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    self._set_cache(key, data)
                    _moralis_cb.set_stale_result(data)
                    await _moralis_cb.record_success()
                    return data
                elif r.status_code == 429:
                    logger.warning("Moralis rate limited")
                    await _moralis_cb.record_failure(is_server_error=False)
                    return None
                elif r.status_code >= 500:
                    logger.debug(f"Moralis {r.status_code}: {url[:80]}")
                    await _moralis_cb.record_failure(is_server_error=True)
                    return None
                else:
                    logger.debug(f"Moralis {r.status_code}: {url[:80]}")
                    await _moralis_cb.record_failure(is_server_error=False)
                    return None
        except Exception as e:
            logger.debug(f"Moralis request failed: {e}")
            await _moralis_cb.record_failure(is_server_error=True)
            return None

    # ── Auth API (Key 2) ──────────────────────────────────

    async def request_evm_challenge(
        self, address: str, chain: str = "eth", domain: str = "rugmunch.io"
    ) -> dict | None:
        """Request SIWE (Sign-In With Ethereum) challenge for MetaMask etc."""
        chain_id = self._chain_id(chain)
        url = f"{AUTH_URL}/challenge/request/evm"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    url,
                    headers={
                        "X-API-Key": self.auth_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "address": address,
                        "chainId": chain_id,
                        "domain": domain,
                        "uri": f"https://{domain}",
                        "timeout": 60,
                    },
                )
                if r.status_code in (200, 201):
                    return r.json()
                logger.debug(f"EVM challenge failed: {r.status_code} {r.text[:100]}")
                return None
        except Exception as e:
            logger.debug(f"EVM challenge error: {e}")
            return None

    async def request_solana_challenge(
        self, address: str, domain: str = "rugmunch.io"
    ) -> dict | None:
        """Request SIWS (Sign-In With Solana) challenge for Phantom etc."""
        url = f"{AUTH_URL}/challenge/request/solana"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    url,
                    headers={
                        "X-API-Key": self.auth_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "address": address,
                        "network": "mainnet",
                        "domain": domain,
                        "uri": f"https://{domain}",
                        "timeout": 60,
                    },
                )
                if r.status_code in (200, 201):
                    return r.json()
                logger.debug(f"Solana challenge failed: {r.status_code} {r.text[:100]}")
                return None
        except Exception as e:
            logger.debug(f"Solana challenge error: {e}")
            return None

    async def verify_evm_signature(self, message: str, signature: str) -> dict | None:
        """Verify EVM wallet signature, get JWT token."""
        url = f"{AUTH_URL}/challenge/verify/evm"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    url,
                    headers={
                        "X-API-Key": self.auth_key,
                        "Content-Type": "application/json",
                    },
                    json={"message": message, "signature": signature},
                )
                if r.status_code in (200, 201):
                    return r.json()  # Contains JWT token
                return None
        except Exception:
            return None

    async def verify_solana_signature(self, message: str, signature: str) -> dict | None:
        """Verify Solana wallet signature, get JWT token."""
        url = f"{AUTH_URL}/challenge/verify/solana"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    url,
                    headers={
                        "X-API-Key": self.auth_key,
                        "Content-Type": "application/json",
                    },
                    json={"message": message, "signature": signature},
                )
                if r.status_code in (200, 201):
                    return r.json()  # Contains JWT token
                return None
        except Exception:
            return None

    # ── Data API (Key 1) ──────────────────────────────────

    async def get_wallet_tokens(self, address: str, chain: str = "eth") -> list[dict]:
        """Get ERC-20 tokens for a wallet."""
        url = f"{DATA_URL}/{address}/erc20?chain={self._chain_param(chain)}&limit=100"
        result = await self._get(url)
        if isinstance(result, list):
            return result
        return result.get("result", result.get("data", [])) if result else []

    async def get_wallet_nfts(
        self, address: str, chain: str = "eth", limit: int = 20
    ) -> list[dict]:
        """Get NFTs owned by wallet."""
        url = f"{DATA_URL}/{address}/nft?chain={self._chain_param(chain)}&limit={limit}&normalizeMetadata=true"
        result = await self._get(url)
        if isinstance(result, list):
            return result
        return result.get("result", result.get("data", [])) if result else []

    async def get_wallet_native_balance(self, address: str, chain: str = "eth") -> dict:
        """Get native token balance (ETH/MATIC/BNB etc)."""
        url = f"{DATA_URL}/{address}/balance?chain={self._chain_param(chain)}"
        return await self._get(url) or {}

    async def get_wallet_token_transfers(
        self, address: str, chain: str = "eth", limit: int = 20
    ) -> list[dict]:
        """Get token transfer history for whale tracking."""
        url = f"{DATA_URL}/{address}/erc20/transfers?chain={self._chain_param(chain)}&limit={limit}"
        result = await self._get(url)
        if isinstance(result, list):
            return result
        return result.get("result", result.get("data", [])) if result else []

    async def get_token_price(self, token_address: str, chain: str = "eth") -> dict:
        """Get token price in USD."""
        url = f"{DATA_URL}/erc20/{token_address}/price?chain={self._chain_param(chain)}"
        return await self._get(url) or {}

    async def get_token_metadata(self, token_address: str, chain: str = "eth") -> dict:
        """Get token metadata (name, symbol, decimals)."""
        url = f"{DATA_URL}/erc20/{token_address}?chain={self._chain_param(chain)}"
        return await self._get(url) or {}

    async def get_block(self, block_number: int, chain: str = "eth") -> dict:
        """Get block data by number."""
        url = f"{DATA_URL}/block/{block_number}?chain={self._chain_param(chain)}"
        return await self._get(url) or {}

    async def get_token_holders(
        self, token_address: str, chain: str = "eth", limit: int = 20
    ) -> list[dict]:
        """Get top token holders (EVM chains)."""
        url = f"{DATA_URL}/erc20/{token_address}/owners?chain={self._chain_param(chain)}&limit={limit}"
        result = await self._get(url)
        if isinstance(result, list):
            return result
        return result.get("result", result.get("data", [])) if result else []

    # ── Stream Management ──────────────────────────────────

    async def list_streams(self) -> list[dict]:
        """List all Moralis streams."""
        url = f"{STREAM_URL}/streams"
        result = await self._get(url, use_auth_key=False)
        if isinstance(result, dict) and "result" in result:
            return result["result"]
        return result if isinstance(result, list) else []

    async def create_stream(
        self,
        webhook_url: str,
        description: str,
        chains: list[str] | None = None,
        address: str | None = None,
        topic0: list[str] | None = None,
    ) -> dict | None:
        """Create a Moralis stream (webhook for EVM events)."""
        if chains is None:
            chains = ["0x1"]  # Ethereum mainnet

        payload = {
            "webhookUrl": webhook_url,
            "description": description,
            "chainIds": chains,
            "tag": description.replace(" ", "_").lower(),
        }
        if address:
            payload["address"] = address
        if topic0:
            payload["topic0"] = topic0

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    f"{STREAM_URL}/stream",
                    headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
                    json=payload,
                )
                if r.status_code in (200, 201):
                    return r.json()
                logger.debug(f"Create stream failed: {r.status_code} {r.text[:200]}")
                return None
        except Exception as e:
            logger.debug(f"Create stream error: {e}")
            return None

    def status(self) -> dict:
        """Return connector status."""
        return {
            "data_api": bool(self.api_key),
            "auth_api": bool(self.auth_key),
            "data_key_prefix": self.api_key[:20] + "..." if self.api_key else "NOT SET",
            "auth_key_prefix": self.auth_key[:20] + "..." if self.auth_key else "NOT SET",
            "supported_chains": list(SUPPORTED_EVM_CHAINS.keys()),
            "cache_entries": len(self._cache),
        }


# Singleton
_moralis: MoralisConnector | None = None


def get_moralis_connector() -> MoralisConnector:
    global _moralis
    if _moralis is None:
        _moralis = MoralisConnector()
    return _moralis
