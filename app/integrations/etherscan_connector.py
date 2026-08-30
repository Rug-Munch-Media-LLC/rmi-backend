"""
Etherscan Family Connector - EVM Block Explorers.
Single API works for: Ethereum, BSC, Polygon, Avalanche, Fantom, Arbitrum, Optimism, Base.
Free tier: 5 req/sec, 100,000 calls/day.

Supports:
- Account: balance, txlist, tokentx, tokenbalance
- Contract: abi, sourcecode, verify
- Transaction: status, receipt
- Block: blocknumber, blockreward
- Logs: getLogs
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.core.circuit_breaker import AsyncCircuitBreaker, CircuitBreakerOpenError
from app.core.logging import get_logger
from app.databus.vault import resolve_key

logger = get_logger(__name__)

# Circuit breaker for Etherscan family APIs (shared across all networks)
_etherscan_cb = AsyncCircuitBreaker(
    name="etherscan",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)

# ── API Keys ────────────────────────────────────────────────

ETHERSCAN_API_KEY = resolve_key("ETHERSCAN_API_KEY")
BSCSCAN_API_KEY = resolve_key("BSCSCAN_API_KEY")
POLYGONSCAN_API_KEY = resolve_key("POLYGONSCAN_API_KEY")
SNOWTRACE_API_KEY = resolve_key("SNOWTRACE_API_KEY")  # Avalanche
FTMSCAN_API_KEY = resolve_key("FTMSCAN_API_KEY")  # Fantom

# Network configurations
NETWORKS = {
    "eth": {"name": "Etherscan", "url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "bsc": {"name": "BscScan", "url": "https://api.bscscan.com/api", "key": BSCSCAN_API_KEY},
    "polygon": {
        "name": "PolygonScan",
        "url": "https://api.polygonscan.com/api",
        "key": POLYGONSCAN_API_KEY,
    },
    "avalanche": {
        "name": "SnowTrace",
        "url": "https://api.snowtrace.io/api",
        "key": SNOWTRACE_API_KEY,
    },
    "fantom": {"name": "FtmScan", "url": "https://api.ftmscan.com/api", "key": FTMSCAN_API_KEY},
    "arbitrum": {
        "name": "Arbiscan",
        "url": "https://api.arbiscan.io/api",
        "key": ETHERSCAN_API_KEY,
    },
    "optimism": {
        "name": "Optimism Etherscan",
        "url": "https://api-optimistic.etherscan.io/api",
        "key": ETHERSCAN_API_KEY,
    },
    "base": {"name": "Basescan", "url": "https://api.basescan.org/api", "key": ETHERSCAN_API_KEY},
}


class EtherscanConnector:
    """Etherscan family API connector with intelligent key rotation."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[Any, ...]] = {}
        self._cache_ttl = 300  # 5 min
        self._last_request: dict[str, float] = {}  # per-network rate limiting
        self._min_interval = 0.2  # 5 req/sec per network

    def _get_network(self, network: str) -> dict[str, Any]:
        """Get network config."""
        return NETWORKS.get(network, NETWORKS["eth"])

    def _network_key(self, network: str) -> str:
        """Get API key for network."""
        net = self._get_network(network)
        return net.get("key", ETHERSCAN_API_KEY)

    async def _rate_limit(self, network: str) -> None:
        now = time.monotonic()
        last = self._last_request.get(network, 0)
        elapsed = now - last
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request[network] = time.monotonic()

    def _cached(self, key: str) -> Any | None:
        if key in self._cache:
            data, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return data
        return None

    def _set_cache(self, key: str, data: Any) -> None:
        self._cache[key] = (data, time.time())
        if len(self._cache) > 500:
            oldest = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest]

    async def _get(self, network: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Make API call to Etherscan family with circuit breaker."""
        net = self._get_network(network)
        api_key = self._network_key(network)

        params["apikey"] = api_key
        cache_key = f"{network}:{params.get('module', '')}:{params.get('action', '')}:{str(params.get('address', params.get('contract', '')))[:30]}"

        cached = self._cached(cache_key)
        if cached is not None:
            return cached

        # Circuit breaker check — serve stale cache if OPEN
        if not await _etherscan_cb.can_call():
            stale = _etherscan_cb.get_stale_result()
            if stale is not None:
                return stale
            raise CircuitBreakerOpenError(
                _etherscan_cb.name,
                _etherscan_cb.state,
                "Etherscan circuit OPEN — failing fast",
            )

        await self._rate_limit(network)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(net["url"], params=params)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status") == "1":
                        result = data.get("result")
                        self._set_cache(cache_key, result)
                        _etherscan_cb.set_stale_result(result)
                        await _etherscan_cb.record_success()
                        return result
                    elif data.get("status") == "0":
                        logger.debug(f"Etherscan error: {data.get('message', '')}")
                        await _etherscan_cb.record_failure(is_server_error=False)
                        return None
                    else:
                        self._set_cache(cache_key, data)
                        _etherscan_cb.set_stale_result(data)
                        await _etherscan_cb.record_success()
                        return data
                elif r.status_code == 429:
                    logger.warning(f"{net['name']} rate limited")
                    await _etherscan_cb.record_failure(is_server_error=False)
                    return None
                elif r.status_code >= 500:
                    logger.debug(f"{net['name']} {r.status_code}")
                    await _etherscan_cb.record_failure(is_server_error=True)
                    return None
                else:
                    logger.debug(f"{net['name']} {r.status_code}")
                    await _etherscan_cb.record_failure(is_server_error=False)
                    return None
        except Exception as e:
            logger.debug(f"{net['name']} request failed: {e}")
            await _etherscan_cb.record_failure(is_server_error=True)
            return None

    # ── Account API ──────────────────────────────────────────

    async def get_balance(self, address: str, network: str = "eth") -> str:
        """Get native token balance (ETH/BNB/MATIC/etc)."""
        result = await self._get(
            network,
            {"module": "account", "action": "balance", "address": address, "tag": "latest"},
        )
        return result if isinstance(result, str) else "0"

    async def get_tx_list(
        self,
        address: str,
        network: str = "eth",
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 100,
        sort: str = "desc",
    ) -> list[dict[str, Any]]:
        """Get transaction list for address."""
        result = await self._get(
            network,
            {
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": start_block,
                "endblock": end_block,
                "page": page,
                "offset": offset,
                "sort": sort,
            },
        )
        return result if isinstance(result, list) else []

    async def get_token_transfers(
        self,
        address: str,
        network: str = "eth",
        contract_address: str | None = None,
        page: int = 1,
        offset: int = 100,
    ) -> list[dict[str, Any]]:
        """Get ERC-20 token transfer events."""
        params = {
            "module": "account",
            "action": "tokentx",
            "address": address,
            "page": page,
            "offset": offset,
            "sort": "desc",
        }
        if contract_address:
            params["contractaddress"] = contract_address
        result = await self._get(network, params)
        return result if isinstance(result, list) else []

    async def get_token_balance(self, contract_address: str, address: str, network: str = "eth") -> str:
        """Get ERC-20 token balance for address."""
        result = await self._get(
            network,
            {
                "module": "account",
                "action": "tokenbalance",
                "contractaddress": contract_address,
                "address": address,
                "tag": "latest",
            },
        )
        return result if isinstance(result, str) else "0"

    async def get_erc721_transfers(
        self, address: str, network: str = "eth", page: int = 1, offset: int = 100
    ) -> list[dict[str, Any]]:
        """Get ERC-721 NFT transfer events."""
        result = await self._get(
            network,
            {
                "module": "account",
                "action": "tokennfttx",
                "address": address,
                "page": page,
                "offset": offset,
                "sort": "desc",
            },
        )
        return result if isinstance(result, list) else []

    # ── Contract API ─────────────────────────────────────────

    async def get_contract_abi(self, contract: str, network: str = "eth") -> str:
        """Get contract ABI."""
        result = await self._get(network, {"module": "contract", "action": "getabi", "address": contract})
        return result if isinstance(result, str) else ""

    async def get_contract_source(self, contract: str, network: str = "eth") -> dict[str, Any]:
        """Get contract source code."""
        result = await self._get(network, {"module": "contract", "action": "getsourcecode", "address": contract})
        if isinstance(result, list) and len(result) > 0:
            return result[0]
        return result or {}

    async def verify_contract(self, contract: str, network: str = "eth") -> dict[str, Any]:
        """Check if contract is verified."""
        source = await self.get_contract_source(contract, network)
        return {
            "verified": bool(source.get("SourceCode")),
            "name": source.get("ContractName"),
            "compiler": source.get("CompilerVersion"),
        }

    # ── Transaction API ──────────────────────────────────────

    async def get_transaction_status(self, tx_hash: str, network: str = "eth") -> dict[str, Any]:
        """Get transaction status (success/fail)."""
        result = await self._get(network, {"module": "transaction", "action": "getstatus", "txhash": tx_hash})
        return result or {}

    async def get_transaction_receipt(self, tx_hash: str, network: str = "eth") -> dict[str, Any]:
        """Get transaction receipt."""
        result = await self._get(network, {"module": "transaction", "action": "gettxreceiptstatus", "txhash": tx_hash})
        return result or {}

    # ── Block API ────────────────────────────────────────────

    async def get_block_number(self, network: str = "eth") -> int:
        """Get latest block number."""
        result = await self._get(network, {"module": "proxy", "action": "eth_blockNumber"})
        try:
            raw = str(result or "")
            return int(raw, 16) if raw.startswith("0x") else int(raw or 0)
        except (ConnectionError, OSError, TimeoutError, ValueError, TypeError):
            return 0

    async def get_block_reward(self, block_number: int, network: str = "eth") -> dict[str, Any]:
        """Get block reward data."""
        return await self._get(network, {"module": "block", "action": "getblockreward", "blockno": block_number}) or {}

    # ── Logs API ─────────────────────────────────────────────

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | None = None,
        topic0: str | None = None,
        network: str = "eth",
    ) -> list[dict[str, Any]]:
        """Get event logs (filtered)."""
        params = {
            "module": "logs",
            "action": "getLogs",
            "fromBlock": from_block,
            "toBlock": to_block,
        }
        if address:
            params["address"] = address
        if topic0:
            params["topic0"] = topic0
        result = await self._get(network, params)
        return result if isinstance(result, list) else []

    # ── Utility ──────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return connector status."""
        active = [k for k, v in NETWORKS.items() if v.get("key")]
        return {
            "networks_configured": len(NETWORKS),
            "networks_active": len(active),
            "active_list": active,
            "cache_entries": len(self._cache),
        }


# Singleton
_etherscan: EtherscanConnector | None = None


def get_etherscan_connector() -> EtherscanConnector:
    global _etherscan
    if _etherscan is None:
        _etherscan = EtherscanConnector()
    return _etherscan
