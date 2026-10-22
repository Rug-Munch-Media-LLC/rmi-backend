"""Shared builders for the market-context test modules."""

from __future__ import annotations

from typing import Any

from app.domains.scanner.unified.model import MarketContext


def make_market_context(**overrides: Any) -> MarketContext:
    """Return a neutral ``MarketContext`` with ``overrides`` applied."""
    defaults: dict[str, Any] = {
        "fear_index": 50.0,
        "recent_exploits": 0,
        "chain_volume_24h": 0.0,
        "social_velocity": 0.0,
        "deployer_freshness": "unknown",
        "source": "rugbrain",
    }
    defaults.update(overrides)
    return MarketContext(**defaults)
