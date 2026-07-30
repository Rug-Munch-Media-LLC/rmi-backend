"""
Blockchair API Integration - Bitcoin/Litecoin/Ethereum/Solana Blockchain API
============================================================================

Access blockchain data for:
- Transaction lookups
- Address balance checks
- Block information
- Transaction status
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.circuit_breaker import CircuitBreakerOpenError, SimpleCircuitBreaker
from app.core.logging import get_logger

logger = get_logger(__name__)

# TODO: migrate to async httpx.AsyncClient — this class uses sync httpx.get()
# but may be called from async contexts. See async-http-patterns skill for pattern.

# Circuit breaker for Blockchair API (sync — pending async migration)
_blockchair_cb = SimpleCircuitBreaker(
    name="blockchair",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)


class _StaleResultAvailable(Exception):
    """Internal sentinel — circuit-breaker returned a stale cached result."""

    def __init__(self, result: Any) -> None:
        self.result = result


def _cb_call(url: str, params: dict | None = None, timeout: int = 30) -> Any:
    """Sync HTTP GET wrapped with circuit breaker. Returns JSON data or raises sentinel."""
    if not _blockchair_cb.can_call():
        stale = _blockchair_cb.get_stale_result()
        if stale is not None:
            raise _StaleResultAvailable(stale)
        raise CircuitBreakerOpenError(
            _blockchair_cb.name,
            _blockchair_cb.state,
            "Blockchair circuit OPEN — failing fast",
        )
    resp = httpx.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    _blockchair_cb.record_success()
    return resp.json()


# ─── BLOCKCHAIR API ENDPOINTS ─────────────────────────────────────

BLOCKCHAIR_API = "https://api.blockchair.com"

ENDPOINTS = {
    "bitcoin": "/bitcoin",
    "ethereum": "/ethereum",
    "solana": "/solana",
    "litecoin": "/litecoin",
    "bitcoin_cash": "/bitcoin-cash",
    "bitcoin_sv": "/bitcoin-sv",
    "search": "/v2/search",
    "stats": "/v2/stats",
    "block": "/v2/block/{chain}/{id}",
    "address": "/v2/address/{chain}/{address}",
    "transaction": "/v2/transaction/{chain}/{hash}",
    "mempool": "/v2/mempool/{chain}",
}

# ─── BLOCKCHAIR CLIENT ────────────────────────────────────────────


class BlockchairClient:
    """Client for Blockchair API."""

    def __init__(self, api_key: str | None = None, timeout: int = 30) -> None:
        self.api_key = api_key or ""
        self.timeout = timeout
        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        """Check if Blockchair API is accessible."""
        try:
            response = httpx.get(f"{BLOCKCHAIR_API}/bitcoin/stats", timeout=5)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def _extract(self, data: Any, *keys: str) -> Any:
        """Safely traverse nested dict from API response."""
        for key in keys:
            if isinstance(data, dict):
                data = data.get(key)
            else:
                return None
        return data

    def get_chain_stats(self, chain: str = "bitcoin") -> dict[str, Any]:
        """Get blockchain statistics."""
        endpoint = ENDPOINTS.get(chain, "/bitcoin")
        url = f"{BLOCKCHAIR_API}{endpoint}/stats"
        try:
            data = _cb_call(url, timeout=self.timeout)
            _blockchair_cb.set_stale_result(data)
            return self._extract(data, "data", "stats") or {}
        except _StaleResultAvailable as stale:
            return stale.result.get("data", {}).get("stats", {})
        except CircuitBreakerOpenError:
            raise
        except Exception as e:
            logger.error(f"Error fetching stats for {chain}: {e}")
            _blockchair_cb.record_failure(is_server_error=True)
            return {}

    def get_address_info(self, address: str, chain: str = "bitcoin") -> dict[str, Any] | None:
        """Get address information."""
        url = f"{BLOCKCHAIR_API}{ENDPOINTS['address'].format(chain=chain, address=address)}"
        try:
            data = _cb_call(url, timeout=self.timeout)
            _blockchair_cb.set_stale_result(data)
            return data["data"][address]
        except _StaleResultAvailable as stale:
            return _extract_data(stale.result, address)
        except CircuitBreakerOpenError:
            raise
        except Exception as e:
            logger.error(f"Error fetching address {address} info: {e}")
            _blockchair_cb.record_failure(is_server_error=True)
            return None

    def get_transaction(self, tx_hash: str, chain: str = "bitcoin") -> dict[str, Any] | None:
        """Get transaction details."""
        url = f"{BLOCKCHAIR_API}{ENDPOINTS['transaction'].format(chain=chain, hash=tx_hash)}"
        try:
            data = _cb_call(url, timeout=self.timeout)
            _blockchair_cb.set_stale_result(data)
            return data["data"][tx_hash]
        except _StaleResultAvailable as stale:
            return _extract_data(stale.result, tx_hash)
        except CircuitBreakerOpenError:
            raise
        except Exception as e:
            logger.error(f"Error fetching transaction {tx_hash}: {e}")
            _blockchair_cb.record_failure(is_server_error=True)
            return None

    def search(self, query: str) -> dict[str, Any]:
        """Search for addresses, transactions, blocks."""
        url = f"{BLOCKCHAIR_API}{ENDPOINTS['search']}"
        try:
            data = _cb_call(url, params={"query": query}, timeout=self.timeout)
            _blockchair_cb.set_stale_result(data)
            return data.get("data", {})
        except _StaleResultAvailable as stale:
            return _extract_data(stale.result, "data") or {}
        except CircuitBreakerOpenError:
            raise
        except Exception as e:
            logger.error(f"Error during search: {e}")
            _blockchair_cb.record_failure(is_server_error=True)
            return {}

    def get_blocks(self, chain: str = "bitcoin", limit: int = 10) -> list[dict[str, Any]]:
        """Get recent blocks."""
        url = f"{BLOCKCHAIR_API}{ENDPOINTS.get(chain, '/bitcoin')}/blocks"
        try:
            data = _cb_call(url, params={"limit": limit}, timeout=self.timeout)
            _blockchair_cb.set_stale_result(data)
            return data.get("data", [])
        except _StaleResultAvailable as stale:
            return stale.result.get("data", [])
        except CircuitBreakerOpenError:
            raise
        except Exception as e:
            logger.error(f"Error fetching blocks for {chain}: {e}")
            _blockchair_cb.record_failure(is_server_error=True)
            return []

    def get_mempool(self, chain: str) -> dict[str, Any]:
        """Get mempool status."""
        url = f"{BLOCKCHAIR_API}{ENDPOINTS['mempool'].format(chain=chain)}"
        try:
            data = _cb_call(url, timeout=self.timeout)
            _blockchair_cb.set_stale_result(data)
            return data.get("data", {})
        except _StaleResultAvailable as stale:
            return stale.result.get("data", {})
        except CircuitBreakerOpenError:
            raise
        except Exception as e:
            logger.error(f"Error fetching mempool for {chain}: {e}")
            _blockchair_cb.record_failure(is_server_error=True)
            return {}

    def get_block(self, chain: str, block_id: int) -> dict[str, Any] | None:
        """Get specific block data."""
        url = f"{BLOCKCHAIR_API}{ENDPOINTS['block'].format(chain=chain, id=block_id)}"
        try:
            data = _cb_call(url, timeout=self.timeout)
            _blockchair_cb.set_stale_result(data)
            return data.get("data")
        except _StaleResultAvailable as stale:
            return stale.result.get("data")
        except CircuitBreakerOpenError:
            raise
        except Exception as e:
            logger.error(f"Error fetching block {block_id} for {chain}: {e}")
            _blockchair_cb.record_failure(is_server_error=True)
            return None


def _extract_data(result: Any, key: str) -> Any:
    """Get key from stale result safely."""
    if isinstance(result, dict):
        return result.get("data", {}).get(key) if key else result.get("data")
    return None


# ─── GLOBAL SINGLETON ─────────────────────────────────────────────

_client: BlockchairClient | None = None


def get_blockchair_client(chain: str = "bitcoin") -> BlockchairClient:
    """Get or create Blockchair client instance."""
    global _client
    if _client is None:
        _client = BlockchairClient()
    return _client


def get_address_balance(address: str, chain: str = "bitcoin") -> dict[str, Any] | None:
    """Get address balance from Blockchair."""
    client = get_blockchair_client(chain)
    return client.get_address_info(address)


def search_blockchain(query: str) -> dict[str, Any]:
    """Search Blockchair for blockchain data."""
    client = get_blockchair_client()
    return client.search(query)
