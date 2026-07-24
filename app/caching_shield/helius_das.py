"""
Helius DAS (Digital Asset Standard) Client
Uses existing Helius API keys to fetch indexed Solana token data.

The DAS API provides indexed/aggregated data - no need for Solana Tracker
or other third-party indexers for basic token metadata and holder queries.

Endpoints:
  - getAsset / getAssetBatch: Token/NFT metadata
  - getTokenAccounts: Token holdings for a wallet
  - searchAssets: Search tokens by criteria
  - getAssetsByOwner: All tokens/NFTs owned by address

Free tier: Uses same Helius keys, no additional cost. 25 RPS per key.

Usage:
    from app.caching_shield.helius_das import get_helius_das
    das = get_helius_das()
    meta = await das.get_token_metadata("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    holdings = await das.get_wallet_tokens("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV")
"""

from __future__ import annotations

import hashlib
import json
import os
import time

import httpx

from app.core.logging import get_logger

logger = get_logger("helius_das")

DAS_BASE = "https://mainnet.helius-rpc.com"

class HeliusDasClient:
    """Indexed Solana token data via Helius DAS API. Uses existing RPC keys."""

    def __init__(self) -> None:
        self._keys = []
        for suffix in ("", "_2"):
            k = os.getenv(f"HELIUS_API_KEY{suffix}", "")
            if k and len(k) > 10:
                self._keys.append(k)
        self._key_idx = 0
        self._http: httpx.AsyncClient | None = None
        self._l1: dict[str, tuple] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    def _next_key(self) -> str:
        key = self._keys[self._key_idx % len(self._keys)]
        self._key_idx += 1
        return key

    async def _post(self, method: str, params: dict, ttl: int = 60) -> dict | None:
        """Make a cached DAS API call."""
        cache_key = hashlib.sha256(
            f"{method}:{json.dumps(params, sort_keys=True)}".encode()
        ).hexdigest()[:24]

        # L1 cache
        entry = self._l1.get(cache_key)
        if entry:
            expiry, data = entry
            if time.monotonic() < expiry:
                self.cache_hits += 1
                return data
            del self._l1[cache_key]
        self.cache_misses += 1

        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)

        key = self._next_key()
        url = f"{DAS_BASE}/?api-key={key}"

        try:
            resp = await self._http.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result")
                if result:
                    self._l1[cache_key] = (time.monotonic() + ttl, result)
                    return result
        except Exception as e:
            logger.debug(f"DAS {method} error: {e}")

        return None

    async def get_token_metadata(self, mint: str) -> dict | None:
        """Get token metadata: name, symbol, decimals, image, supply."""
        return await self._post("getAsset", {"id": mint}, ttl=120)

    async def get_token_metadata_batch(self, mints: list[str]) -> dict | None:
        """Batch get metadata for up to 100 tokens."""
        ids = mints[:100]
        return await self._post("getAssetBatch", {"ids": ids}, ttl=120)

    async def get_wallet_tokens(self, address: str, limit: int = 100) -> dict | None:
        """Get all tokens held by a wallet."""
        return await self._post(
            "getTokenAccounts",
            {
                "owner": address,
                "page": 1,
                "limit": min(limit, 1000),
            },
            ttl=20,
        )

    async def search_assets(self, query: str, page: int = 1, limit: int = 20) -> dict | None:
        """Search for tokens by name/symbol."""
        return await self._post(
            "searchAssets",
            {
                "searchString": query,
                "page": page,
                "limit": min(limit, 100),
            },
            ttl=60,
        )

    async def get_assets_by_owner(self, address: str, limit: int = 100) -> dict | None:
        """Get all assets (tokens + NFTs) owned by an address."""
        return await self._post(
            "getAssetsByOwner",
            {
                "ownerAddress": address,
                "page": 1,
                "limit": min(limit, 1000),
            },
            ttl=20,
        )

    async def get_asset_proof(self, asset_id: str) -> dict | None:
        """Get merkle proof for compressed NFT."""
        return await self._post("getAssetProof", {"id": asset_id}, ttl=3600)

    def stats(self) -> dict:
        return {
            "keys": len(self._keys),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "l1_size": len(self._l1),
            "rps_per_key": 25,
            "total_rps": len(self._keys) * 25,
        }

_das_client: HeliusDasClient | None = None

def get_helius_das() -> HeliusDasClient:
    global _das_client
    if _das_client is None:
        _das_client = HeliusDasClient()
    return _das_client
