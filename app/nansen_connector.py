"""
Nansen API Integration - Wallet Labels, Smart Money, Token Flow
==============================================================

Real-time on-chain intelligence from Nansen:
- Wallet labeling (exchange, smart money, fund, etc.)
- Smart money tracking and profiling
- Transaction history with entity labels

Auth: API key passed as ?api_key=KEY query parameter.
"""

import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)
# TODO: migrate to async httpx.AsyncClient — this class uses sync httpx.get()
# but may be called from async contexts. See async-http-patterns skill for pattern.


# ─── NANSEN API CONFIGURATION ─────────────────────────────────────

NANSEN_BASE = "https://api.nansen.ai"

ENDPOINTS = {
    "wallet_labels": "/v2/wallet/labels/{address}",
    "smart_money": "/v2/smart-money",
    "wallet_activity": "/v2/wallet/{address}/activity",
}


def _load_api_key() -> str:
    """Load Nansen API key from secrets file or environment."""
    secret_path = "/root/.secrets/nansen_api_key"
    if os.path.isfile(secret_path):
        try:
            with open(secret_path) as f:
                key = f.read().strip()
                if key:
                    return key
        except OSError:
            pass
    env_key = os.environ.get("NANSEN_API_KEY", "")
    if env_key:
        return env_key
    logger.warning("Nansen API key not found - requests will fail without auth")
    return ""


# ─── NANSEN CLIENT ────────────────────────────────────────────────


class NansenClient:
    """Client for Nansen REST API v2."""

    def __init__(
        self,
        base_url: str = NANSEN_BASE,
        api_key: str | None = None,
        timeout: int = 30,
    ):
        self.base = base_url.rstrip("/")
        self.api_key = api_key or _load_api_key()
        self.timeout = timeout
        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        """Verify Nansen API is reachable and authenticated."""
        if not self.api_key:
            return False
        try:
            response = httpx.get(
                f"{self.base}/v2/smart-money",
                params={"api_key": self.api_key},
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            return False

    # ── request helpers ───────────────────────────────────────────

    def _get(self, path: str, extra_params: dict[str, str] | None = None) -> dict[str, Any] | None:
        """Authenticated GET request returning JSON or None on error."""
        params = {"api_key": self.api_key}
        if extra_params:
            params.update(extra_params)
        try:
            response = httpx.get(
                f"{self.base}{path}",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error("Nansen API error (%s): %s", path, e)
            return None

    # ── public API methods ────────────────────────────────────────

    def get_wallet_labels(self, address: str) -> dict[str, Any] | None:
        """
        Fetch on-chain labels for a wallet address.

        Returns label metadata: entity type (exchange, smart money,
        fund, whale, etc.), confidence, and related tags.
        """
        path = ENDPOINTS["wallet_labels"].format(address=address)
        return self._get(path)

    def get_smart_money(self) -> list[dict[str, Any]] | None:
        """
        Retrieve the list of tracked smart-money wallets.

        Returns list of wallet profiles with win-rate, PnL, and
        recent activity summaries.
        """
        path = ENDPOINTS["smart_money"]
        return self._get(path)

    def get_wallet_activity(self, address: str) -> dict[str, Any] | None:
        """
        Get transaction history for a wallet with entity labels.

        Each transaction is enriched with counterparty labels so you
        can see who the wallet is interacting with.
        """
        path = ENDPOINTS["wallet_activity"].format(address=address)
        return self._get(path)


# ─── GLOBAL SINGLETON ─────────────────────────────────────────────

_client: NansenClient | None = None


def get_nansen_client() -> NansenClient:
    """Get or create the Nansen client singleton."""
    global _client
    if _client is None:
        _client = NansenClient()
    return _client


# ─── CONVENIENCE FUNCTIONS ────────────────────────────────────────


def get_wallet_labels(address: str) -> dict[str, Any] | None:
    """Return on-chain labels for *address*."""
    return get_nansen_client().get_wallet_labels(address)


def get_smart_money() -> list[dict[str, Any]] | None:
    """Return the current smart-money wallet list."""
    return get_nansen_client().get_smart_money()


def get_wallet_activity(address: str) -> dict[str, Any] | None:
    """Return labelled transaction history for *address*."""
    return get_nansen_client().get_wallet_activity(address)
