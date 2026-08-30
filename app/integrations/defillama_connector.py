"""
DeFiLlama API Integration - DeFi Data for AI Agents
====================================================

Real-time DeFi data from DeFiLlama:
- TVL by chain/protocol
- Volume metrics
- Stablecoin supply
- Lending data
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.circuit_breaker import CircuitBreakerOpenError, SimpleCircuitBreaker
from app.core.logging import get_logger

logger = get_logger(__name__)

# TODO: migrate to async httpx.AsyncClient — this class uses sync httpx.get()
# but may be called from async contexts. See async-http-patterns skill for pattern.

# Circuit breaker for DeFiLlama API (sync — uses SimpleCircuitBreaker pending async migration)
_defillama_cb = SimpleCircuitBreaker(
    name="defillama",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)

# ─── DEFI LLAMA API ENDPOINTS ─────────────────────────────────────

DEFI_LLAMA_BASE = "https://api.llama.fi"

ENDPOINTS = {
    "protocols": "/protocols",
    "protocols_data": "/protocols/data",
    "chains": "/chains",
    "chain": "/chain/{chain}",
    "pools": "/v2/historicalTvl/{chain}",
    "lending": "/lending/{protocol}",
}

# ─── HELPER — wrap a sync HTTP call with CB + stale cache ─────────


def _cb_http_get(
    url: str,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
    cb: SimpleCircuitBreaker | None = None,
) -> httpx.Response:
    """Make a sync ``httpx.get()`` call under circuit-breaker protection.

    Args:
        url: Full URL to fetch.
        params: Optional query params.
        timeout: Request timeout in seconds.
        cb: Circuit breaker instance; falls back to ``_defillama_cb`` if None.

    Returns:
        ``httpx.Response`` on success.

    Raises:
        CircuitBreakerOpenError: If circuit is OPEN and no stale result is available.
        httpx.HTTPError: Re-raised from httpx on network errors.
    """
    cb = cb or _defillama_cb
    if not cb.can_call():
        stale = cb.get_stale_result()
        if stale is not None:
            # Wrap stale dict result as a fake response
            raise _StaleResultAvailable(stale)
        raise CircuitBreakerOpenError(cb.name, cb.state, f"{cb.name} circuit OPEN — failing fast")
    return httpx.get(url, params=params, timeout=timeout)


class _StaleResultAvailable(Exception):
    """Internal sentinel — a stale cached result was found during CB check."""

    def __init__(self, result: Any) -> None:
        self.result = result
        super().__init__("stale result")


# ─── DEFI LLAMA CLIENT ────────────────────────────────────────────


class DeFiLlamaClient:
    """Client for DeFiLlama API."""

    def __init__(self, base_url: str = DEFI_LLAMA_BASE, timeout: int = 30) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        """Check if DeFiLlama API is accessible."""
        try:
            response = httpx.get(f"{self.base}/protocols", timeout=5)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def _request(self, endpoint_template: str, **fmt_kwargs: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Internal: sync GET with circuit breaker, returns parsed JSON or None."""
        path = ENDPOINTS[endpoint_template].format(**fmt_kwargs)
        full_url = f"{self.base}{path}"
        try:
            response = _cb_http_get(full_url, timeout=self.timeout)
            _defillama_cb.record_success()
            return response.json()
        except _StaleResultAvailable as stale:
            return stale.result
        except httpx.HTTPError:
            _defillama_cb.record_failure(is_server_error=True)
            return None

    def get_protocols(self) -> list[dict[str, Any]]:
        """
        Get all protocols data.

        Returns:
            List of protocol objects
        """
        result = self._request("protocols")
        if result is not None:
            return result if isinstance(result, list) else self._stub_protocols()
        return self._stub_protocols()

    def get_protocols_data(self) -> dict[str, Any]:
        """Get comprehensive protocols data with TVL."""
        result = self._request("protocols_data")
        if result is not None:
            return result if isinstance(result, dict) else {"protocols": self._stub_protocols()}
        return {"protocols": self._stub_protocols()}

    def get_chains(self) -> list[dict[str, Any]]:
        """Get all chain data."""
        result = self._request("chains")
        if isinstance(result, list):
            return result
        return []

    def get_chain_data(self, chain: str) -> dict[str, Any] | None:
        """Get chain-specific data."""
        result = self._request("chain", chain=chain)
        if isinstance(result, dict):
            return result
        return None

    def get_pool_data(self, chain: str) -> list[dict[str, Any]]:
        """Get pool data for a chain."""
        result = self._request("pools", chain=chain)
        if isinstance(result, list):
            return result
        return []

    def get_lending_data(self, protocol: str) -> dict[str, Any]:
        """Get lending data for a protocol."""
        result = self._request("lending", protocol=protocol)
        if isinstance(result, dict):
            return result
        return {}

    def _stub_protocols(self) -> list[dict[str, Any]]:
        """Stub protocols data when API unavailable."""
        return [
            {
                "name": "stub_protocol",
                "tokenPrice": 0,
                "chain": "stub",
                "protocol": "stub",
                "tvl": 0,
            }
        ]


# ─── GLOBAL SINGLETON ─────────────────────────────────────────────

_client: DeFiLlamaClient | None = None


def get_defillama_client() -> DeFiLlamaClient:
    """Get or create DeFiLlama client instance."""
    global _client
    if _client is None:
        _client = DeFiLlamaClient()
    return _client


def get_defi_tvl() -> dict[str, Any]:
    """Get current DeFi TVL totals."""
    client = get_defillama_client()
    return client.get_protocols_data()


def get_defi_protocols() -> list[dict[str, Any]]:
    """Get all DeFi protocols."""
    client = get_defillama_client()
    return client.get_protocols()


def get_chain_tvls() -> dict[str, float]:
    """Get TVL by chain."""
    client = get_defillama_client()
    data = client.get_chains()
    return {chain.get("tvl", 0): chain.get("name", "unknown") for chain in data} if data else {}
