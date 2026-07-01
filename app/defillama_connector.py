"""
DeFiLlama API Integration - DeFi Data for AI Agents
====================================================

Real-time DeFi data from DeFiLlama:
- TVL by chain/protocol
- Volume metrics
- Stablecoin supply
- Lending data
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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


# ─── DEFI LLAMA CLIENT ────────────────────────────────────────────


class DeFiLlamaClient:
    """Client for DeFiLlama API."""

    def __init__(self, base_url: str = DEFI_LLAMA_BASE, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        """Check if DeFiLlama API is accessible."""
        try:
            response = httpx.get(f"{self.base}/protocols", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def get_protocols(self) -> list[dict[str, Any]]:
        """
        Get all protocols data.

        Returns:
            List of protocol objects
        """
        try:
            response = httpx.get(f"{self.base}{ENDPOINTS['protocols']}", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching protocols: {e}")
            return self._stub_protocols()

    def get_protocols_data(self) -> dict[str, Any]:
        """Get comprehensive protocols data with TVL."""
        try:
            response = httpx.get(f"{self.base}{ENDPOINTS['protocols_data']}", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching protocols data: {e}")
            return {"protocols": self._stub_protocols()}

    def get_chains(self) -> list[dict[str, Any]]:
        """Get all chain data."""
        try:
            response = httpx.get(f"{self.base}{ENDPOINTS['chains']}", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching chains: {e}")
            return []

    def get_chain_data(self, chain: str) -> dict[str, Any] | None:
        """Get chain-specific data."""
        try:
            response = httpx.get(f"{self.base}{ENDPOINTS['chain'].format(chain=chain)}", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching chain {chain} data: {e}")
            return None

    def get_pool_data(self, chain: str) -> list[dict[str, Any]]:
        """Get pool data for a chain."""
        try:
            response = httpx.get(f"{self.base}{ENDPOINTS['pools'].format(chain=chain)}", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching pool data for {chain}: {e}")
            return []

    def get_lending_data(self, protocol: str) -> dict[str, Any]:
        """Get lending data for a protocol."""
        try:
            response = httpx.get(
                f"{self.base}{ENDPOINTS['lending'].format(protocol=protocol)}", timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching lending data for {protocol}: {e}")
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
