"""
Market provider chains — new token launch feed.

Chain: new_launches. Split out of ``market/token_intel.py`` so the market
facade can register it last, after ``defi_protocols``, which is where the
pre-split ``market.py`` put it.
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_new_launches_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """New token launch feed (external tier) — own data is served by the route."""
    return {
        "new_launches": ProviderChain(
            data_type="new_launches",
            description="New token launch feed (external tier) — GMGN pump.fun new_creation → GMGN bonded pairs → DexScreener boosts",
            providers=[
                Provider(
                    "gmgn_new_pairs",
                    ProviderTier.FREE_API,
                    g["_gmgn_new_pairs"],
                    weight=6.0,
                    rate_limit_rps=1.0,
                    description="GMGN pump.fun new_creation rank — fresh launches (free, no key)",
                ),
                Provider(
                    "gmgn_bonded_pairs",
                    ProviderTier.FREE_API,
                    g["_gmgn_bonded_pairs"],
                    weight=4.0,
                    rate_limit_rps=1.0,
                    description="GMGN pump.fun bonded pairs — bonding-curve-completed launches (free, no key)",
                ),
            ],
        ),
    }
