"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

Miscellaneous passthrough provider chains:
- Hyperliquid perp markets and gain/loss porn
- Insider wallets
- Prediction signals

These use inline HTTP passthrough functions to the local RMI backend API.

Source: app/domains/databus/_generated/provider_chains.py (original god file).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.databus.provider_core import Provider, ProviderChain, ProviderTier

logger = get_logger("databus.providers.chains.misc")


def build_misc_chains(chains: dict[str, ProviderChain]) -> None:
    """Build passthrough chains for Hyperliquid, insider wallets, and prediction signals."""

    # ── HYPERLIQUID - Perp markets and funding rates ──
    async def _passthrough_hyperliquid(**kwargs) -> dict[str, Any] | None:
        try:
            limit = kwargs.get("limit", 10)
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"http://localhost:8000/api/v1/market/hyperliquid?limit={limit}")
                if r.status_code == 200:
                    return r.json()
        except httpx.HTTPError:
            pass
        return None

    chains["hyperliquid"] = ProviderChain(
        data_type="hyperliquid",
        description="Hyperliquid perp markets and funding rates",
        providers=[
            Provider("rmi_hyperliquid", ProviderTier.LOCAL, _passthrough_hyperliquid, weight=10.0),
        ],
    )

    # ── HYPERLIQUID ACTION - Gain/Loss Porn & Squeezes ──
    async def _passthrough_hyperliquid_action(**kwargs) -> dict[str, Any] | None:
        try:
            limit = kwargs.get("limit", 5)
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"http://localhost:8000/api/v1/market/hyperliquid-action?limit={limit}")
                if r.status_code == 200:
                    return r.json()
        except httpx.HTTPError:
            pass
        return None

    chains["hyperliquid_action"] = ProviderChain(
        data_type="hyperliquid_action",
        description="Hyperliquid gain/loss porn: top movers, funding squeezes, highest volume",
        providers=[
            Provider(
                "rmi_hyperliquid_action",
                ProviderTier.LOCAL,
                _passthrough_hyperliquid_action,
                weight=10.0,
            ),
        ],
    )

    # ── INSIDER WALLETS - Premium intelligence ──
    async def _passthrough_insider_wallets(**kwargs) -> dict[str, Any] | None:
        try:
            limit = kwargs.get("limit", 10)
            tier = kwargs.get("tier", "free")
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"http://localhost:8000/api/v1/market/insider-wallets?limit={limit}&tier={tier}")
                if r.status_code == 200:
                    return r.json()
        except httpx.HTTPError:
            pass
        return None

    chains["insider_wallets"] = ProviderChain(
        data_type="insider_wallets",
        description="Likely insider trader wallets to watch (Premium)",
        providers=[
            Provider("rmi_insider_wallets", ProviderTier.LOCAL, _passthrough_insider_wallets, weight=10.0),
        ],
    )

    # ── PREDICTION SIGNALS - Market intelligence layer ──
    async def _passthrough_prediction_signals(**kwargs) -> dict[str, Any] | None:
        try:
            limit = kwargs.get("limit", 5)
            tier = kwargs.get("tier", "free")
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"http://localhost:8000/api/v1/market/prediction-signals?limit={limit}&tier={tier}")
                if r.status_code == 200:
                    return r.json()
        except httpx.HTTPError:
            pass
        return None

    chains["prediction_signals"] = ProviderChain(
        data_type="prediction_signals",
        description="Prediction market intelligence signals (Polymarket, Kalshi, etc.)",
        providers=[
            Provider(
                "rmi_prediction_signals",
                ProviderTier.LOCAL,
                _passthrough_prediction_signals,
                weight=10.0,
            ),
        ],
    )

    logger.info("Misc passthrough chains built: hyperliquid, hyperliquid_action, insider_wallets, prediction_signals")
