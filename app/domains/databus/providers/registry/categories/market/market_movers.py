"""
Market provider chains — top movers.

Chain: market_movers. Split out of ``market/market_data.py`` so the market
facade can register it in its original position (after ``rag_search``)
without interleaving two modules' chains.
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_market_movers_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Top gainers/losers — local market overview + trending feeds."""
    return {
        "market_movers": ProviderChain(
            data_type="market_movers",
            description="Top market movers — local market overview → trending feeds",
            providers=[
                Provider(
                    "rmi_market_overview",
                    ProviderTier.LOCAL,
                    g["_passthrough_market_overview"],
                    weight=10.0,
                ),
                Provider(
                    "rmi_trending",
                    ProviderTier.LOCAL,
                    g["_passthrough_trending"],
                    weight=8.0,
                ),
                Provider(
                    "solana_tracker_trending",
                    ProviderTier.FREEMIUM,
                    g["_solana_tracker_trending"],
                    weight=5.0,
                    rate_limit_rps=3.0,
                    requires_key=True,
                    key_env="SOLANATRACKER_API_KEY",
                    monthly_quota=5000,
                ),
            ],
        ),
    }
