"""
Market provider chains — token intelligence.

Chain: token_detail. Split from ``market.py`` (see ``market/__init__.py``);
its former neighbours moved out so the facade can register every chain in the
pre-split order — see ``market/__init__.py`` for the full sequence.
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_token_intel_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build the rich token-detail chain."""
    chains: dict[str, ProviderChain] = {}
    chains.update(_token_detail_chain(g))
    return chains


def _token_detail_chain(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Rich token detail — market cap, volume, liquidity, holders, risk."""
    return {
        "token_detail": ProviderChain(
            data_type="token_detail",
            description="Full token intelligence — market cap, volume, liquidity, holders, risk flags",
            providers=[
                Provider(
                    "dexscreener_meta",
                    ProviderTier.FREE_API,
                    g["_dexscreener_token_metadata"],
                    weight=10.0,
                    rate_limit_rps=3.0,
                ),
                Provider(
                    "solana_tracker_token",
                    ProviderTier.FREEMIUM,
                    g["_solana_tracker_token"],
                    weight=6.0,
                    rate_limit_rps=3.0,
                    requires_key=True,
                    key_env="SOLANATRACKER_API_KEY",
                    monthly_quota=5000,
                ),
                Provider(
                    "birdeye_overview_api",
                    ProviderTier.FREEMIUM,
                    g["_birdeye_overview"],
                    weight=4.0,
                    rate_limit_rps=2.0,
                    requires_key=True,
                    key_env="BIRDEYE_API_KEY",
                    monthly_quota=50000,
                ),
                Provider(
                    "gmgn_token_info",
                    ProviderTier.FREE_API,
                    g["_gmgn_token_info"],
                    weight=3.0,
                    rate_limit_rps=1.0,
                    description="GMGN public API — token overview, holders, launchpad progress (free, no key)",
                ),
            ],
        ),
    }
