"""
ScannerBridge — Lightweight bot→scanner communication layer.

Provides a clean async interface for Telegram bots to query the RMI backend
scanner service and DataBus for token prices, security scans, and wallet analysis.

Usage:
    bridge = ScannerBridge()
    price = await bridge.get_price("0x...", "ethereum")
    scan  = await bridge.scan_token("0x...", "solana")
    wallet = await bridge.analyze_wallet("0x...")
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("scanner_bridge")

BACKEND_URL = "http://localhost:8000"
DATABUS_URL = f"{BACKEND_URL}/api/v1/databus"


class ScannerBridge:
    """Async bridge for bot→scanner communication via the RMI backend."""

    def __init__(self, timeout: int = 30, backend_url: str | None = None) -> None:
        self.timeout = timeout
        self.backend_url = backend_url or BACKEND_URL
        self.databus_url = f"{self.backend_url}/api/v1/databus"
        self.scanner_url = f"{self.backend_url}/api/v2/scanner"

    async def get_price(self, address: str, chain: str = "ethereum") -> dict[str, Any]:
        """Get token price from DataBus (DexScreener-backed cached endpoint).

        Returns:
            dict with keys: price_usd, change_24h, volume_24h, source
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.databus_url}/fetch/token_price",
                    params={"mint": address},
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", resp.json())
                    pairs = data.get("pairs", []) if isinstance(data, dict) else []
                    if pairs:
                        p = pairs[0]
                        return {
                            "price_usd": float(p.get("priceUsd", 0)),
                            "change_24h": p.get("priceChange", {}).get("h24", 0),
                            "volume_24h": float(p.get("volume", {}).get("h24", 0)),
                            "source": "databus",
                        }
        except httpx.HTTPError as e:
            logger.warning("ScannerBridge.get_price HTTP error: %s", e)
        except (ValueError, KeyError, TypeError) as e:
            logger.warning("ScannerBridge.get_price parse error: %s", e)
        return {"price_usd": 0, "change_24h": 0, "volume_24h": 0, "source": "unavailable"}

    async def scan_token(self, address: str, chain: str = "ethereum") -> dict[str, Any]:
        """Trigger a token scan via the backend scanner v2 API.

        Returns:
            dict with scan results (risk_score, threats, market_data, etc.)
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.scanner_url}/token/scan",
                    json={"address": address, "chain": chain},
                    timeout=60,
                )
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(
                    "ScannerBridge.scan_token returned %d for %s",
                    resp.status_code,
                    address[:10],
                )
        except httpx.HTTPError as e:
            logger.warning("ScannerBridge.scan_token HTTP error: %s", e)
        return {"status": "error", "error": "scan_failed", "risk_score": 0, "threats": []}

    async def analyze_wallet(self, address: str, chain: str | None = None) -> dict[str, Any]:
        """Get wallet analysis from DataBus.

        Returns:
            dict with balance_usd, tx_count, risk_score, flags, etc.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.databus_url}/wallet/{address}",
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data if isinstance(data, dict) else {}
        except httpx.HTTPError as e:
            logger.warning("ScannerBridge.analyze_wallet HTTP error: %s", e)
        return {
            "address": address,
            "balance_usd": 0,
            "tx_count": 0,
            "risk_score": 0,
            "flags": [],
            "errors": ["backend_unreachable"],
        }

    async def health(self) -> dict[str, Any]:
        """Check if the backend/scanner is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.backend_url}/api/v1/health")
                if resp.status_code == 200:
                    return {"status": "ok", "backend": self.backend_url}
                return {"status": "degraded", "code": resp.status_code}
        except httpx.HTTPError as e:
            return {"status": "unreachable", "error": str(e)[:100]}
