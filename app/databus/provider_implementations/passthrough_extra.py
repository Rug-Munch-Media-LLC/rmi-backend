"""Extra passthrough providers — Hyperliquid, insider wallets, prediction signals."""

from __future__ import annotations

import httpx

from app.config import settings
from sdks.python.x402_frameworks.autogen_adapter import logger


async def _passthrough_hyperliquid(**kwargs) -> dict | None:
    """Hyperliquid perp markets from our own /api/v1/market/hyperliquid."""
    try:
        limit = kwargs.get("limit", 10)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{settings.backend_url}/api/v1/market/hyperliquid?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_hyperliquid_action(**kwargs) -> dict | None:
    """Hyperliquid gain/loss porn from our own /api/v1/market/hyperliquid-action."""
    try:
        limit = kwargs.get("limit", 5)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{settings.backend_url}/api/v1/market/hyperliquid-action?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_insider_wallets(**kwargs) -> dict | None:
    """Likely insider trader wallets from our own /api/v1/market/insider-wallets."""
    try:
        limit = kwargs.get("limit", 10)
        tier = kwargs.get("tier", "free")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{settings.backend_url}/api/v1/market/insider-wallets?limit={limit}&tier={tier}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_prediction_signals(**kwargs) -> dict | None:
    """Prediction market intelligence from our own /api/v1/market/prediction-signals."""
    try:
        limit = kwargs.get("limit", 5)
        tier = kwargs.get("tier", "free")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{settings.backend_url}/api/v1/market/prediction-signals?limit={limit}&tier={tier}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
