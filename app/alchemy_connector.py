"""
Alchemy Connector - NFT API, Enhanced APIs, Transaction API.
Free tier: 300M compute credits/month (~10M/day).
Supports: Ethereum, Polygon, Arbitrum, Optimism, Base, Solana (via partnerships).

Key features:
- NFT API: getNFTs, getNFTMetadata, getOwnersForCollection
- Enhanced API: getTokenBalances, getAssetTransfers
- Transaction API: getTransactionReceipts, debugTraceTransaction
- WebSocket: Real-time event streaming (not implemented here)
"""

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── API Keys ────────────────────────────────────────────────

ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY", "")

# Network endpoints
NETWORKS = {
    "eth": "https://eth-mainnet.g.alchemy.com/v2",
    "eth_goerli": "https://eth-goerli.g.alchemy.com/v2",
    "eth_sepolia": "https://eth-sepolia.g.alchemy.com/v2",
    "polygon": "https://polygon-mainnet.g.alchemy.com/v2",
    "polygon_mumbai": "https://polygon-mumbai.g.alchemy.com/v2",
    "arbitrum": "https://arb-mainnet.g.alchemy.com/v2",
    "optimism": "https://opt-mainnet.g.alchemy.com/v2",
    "base": "https://base-mainnet.g.alchemy.com/v2",
}


class AlchemyConnector:
    """Alchemy API connector for NFTs, enhanced APIs, and transactions."""

    def __init__(self):
        self.api_key = ALCHEMY_API_KEY
        self._cache: dict[str, tuple] = {}
        self._cache_ttl = 300  # 5 min
        self._last_request = 0.0
        self._min_interval = 0.1  # 10 req/sec (Alchemy is generous)

    def _network_url(self, network: str) -> str:
        """Get base URL for network."""
        return NETWORKS.get(network, NETWORKS["eth"])

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
        if len(self._cache) > 500:
            oldest = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest]

    async def _rpc_call(self, network: str, method: str, params: list[Any]) -> dict | None:
        """Make JSON-RPC call to Alchemy."""
        url = f"{self._network_url(network)}/{self.api_key}"
        cache_key = f"rpc:{network}:{method}:{str(params)[:100]}"

        cached = self._cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code == 200:
                    data = r.json()
                    if "error" in data:
                        logger.debug(f"Alchemy error: {data['error'].get('message', '')}")
                        return None
                    result = data.get("result")
                    self._set_cache(cache_key, result)
                    return result
                elif r.status_code == 429:
                    logger.warning("Alchemy rate limited")
                    return None
                else:
                    logger.debug(f"Alchemy {r.status_code}: {url[:80]}")
                    return None
        except Exception as e:
            logger.debug(f"Alchemy request failed: {e}")
            return None

    async def _get(self, endpoint: str, network: str = "eth", params: dict | None = None) -> dict | None:
        """REST API call to Alchemy."""
        base = self._network_url(network)
        url = f"{base}/{self.api_key}/{endpoint}"
        cache_key = f"rest:{network}:{endpoint}:{params or {}!s}"

        cached = self._cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url, params=params)
                if r.status_code == 200:
                    data = r.json()
                    self._set_cache(cache_key, data)
                    return data
                elif r.status_code == 429:
                    logger.warning("Alchemy rate limited")
                    return None
                else:
                    logger.debug(f"Alchemy REST {r.status_code}: {endpoint}")
                    return None
        except Exception as e:
            logger.debug(f"Alchemy REST failed: {e}")
            return None

    # ── NFT API ──────────────────────────────────────────────

    async def get_nfts(
        self, owner: str, network: str = "eth", page_size: int = 50, page_key: str | None = None
    ) -> dict:
        """Get NFTs owned by an address."""
        params = {"owner": owner, "pageSize": page_size}
        if page_key:
            params["pageKey"] = page_key
        return await self._get("getNFTs", network, params) or {}

    async def get_nft_metadata(self, contract: str, token_id: str, network: str = "eth") -> dict:
        """Get metadata for a specific NFT."""
        params = {"contractAddress": contract, "tokenId": token_id}
        return await self._get("getNFTMetadata", network, params) or {}

    async def get_owners_for_collection(self, contract: str, network: str = "eth", page_size: int = 50) -> dict:
        """Get all owners of an NFT collection."""
        params = {"contractAddress": contract, "pageSize": page_size}
        return await self._get("getOwnersForCollection", network, params) or {}

    async def get_nft_sales(self, contract: str | None = None, network: str = "eth", limit: int = 50) -> dict:
        """Get recent NFT sales (optional: filter by contract)."""
        params = {"limit": limit}
        if contract:
            params["contractAddress"] = contract
        return await self._get("getNFTSales", network, params) or {}

    async def get_contract_metadata(self, contract: str, network: str = "eth") -> dict:
        """Get NFT contract metadata (name, symbol, totalSupply)."""
        params = {"contractAddress": contract}
        result = await self._get("getContractMetadata", network, params) or {}
        # Alchemy returns {address, contractMetadata: {...}}
        if "contractMetadata" in result:
            return result["contractMetadata"]
        return result

    # ── Enhanced API ─────────────────────────────────────────

    async def get_token_balances(self, address: str, network: str = "eth") -> dict:
        """Get all ERC-20 token balances for an address."""
        params = {"address": address}
        return await self._get("getTokenBalances", network, params) or {}

    async def get_token_metadata(self, contract: str, network: str = "eth") -> dict:
        """Get ERC-20 token metadata."""
        params = {"contractAddress": contract}
        return await self._get("getTokenMetadata", network, params) or {}

    async def get_asset_transfers(
        self,
        from_address: str | None = None,
        to_address: str | None = None,
        network: str = "eth",
        category: list[str] | None = None,
        max_count: int = 100,
    ) -> dict:
        """Get asset transfers (tokens, NFTs, internal)."""
        params = {"maxCount": max_count}
        if from_address:
            params["fromAddress"] = from_address
        if to_address:
            params["toAddress"] = to_address
        if category:
            params["category"] = category
        return await self._get("getAssetTransfers", network, params) or {}

    # ── Transaction API ──────────────────────────────────────

    async def get_transaction_receipt(self, tx_hash: str, network: str = "eth") -> dict:
        """Get transaction receipt with enhanced data."""
        return await self._rpc_call(network, "eth_getTransactionReceipt", [tx_hash]) or {}

    async def get_block_by_number(self, block_number: int, network: str = "eth", include_txs: bool = False) -> dict:
        """Get block data."""
        return await self._rpc_call(network, "eth_getBlockByNumber", [hex(block_number), include_txs]) or {}

    async def get_balance(self, address: str, network: str = "eth", block: str = "latest") -> str:
        """Get native token balance (hex wei)."""
        return await self._rpc_call(network, "eth_getBalance", [address, block]) or "0x0"

    async def get_code(self, address: str, network: str = "eth") -> str:
        """Get contract bytecode."""
        return await self._rpc_call(network, "eth_getCode", [address, "latest"]) or "0x"

    async def call_contract(
        self, contract: str, data: str, network: str = "eth", from_address: str | None = None
    ) -> str:
        """Call a contract read function."""
        params = {"to": contract, "data": data}
        if from_address:
            params["from"] = from_address
        return await self._rpc_call(network, "eth_call", [params, "latest"]) or "0x"

    # ── Utility ──────────────────────────────────────────────

    def status(self) -> dict:
        """Return connector status."""
        return {
            "api_key_set": bool(self.api_key),
            "key_prefix": self.api_key[:12] + "..." if self.api_key else "NOT SET",
            "supported_networks": list(NETWORKS.keys()),
            "cache_entries": len(self._cache),
        }


# Singleton
_alchemy: AlchemyConnector | None = None


def get_alchemy_connector() -> AlchemyConnector:
    global _alchemy
    if _alchemy is None:
        _alchemy = AlchemyConnector()
    return _alchemy
