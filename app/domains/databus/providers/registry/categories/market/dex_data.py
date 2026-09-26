"""
Market provider chains — DEX pool data.

Chain: dex_data. Split out of ``market/token_intel.py`` so the market facade
can register it before ``defi_protocols``, which is where the pre-split
``market.py`` put it.
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_dex_data_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """DEX pool data — liquidity depth, volume, price impact."""
    return {
        "dex_data": ProviderChain(
            data_type="dex_data",
            description="DEX pool data — liquidity depth, volume, pairs (DexScreener → Birdeye)",
            providers=[
                Provider(
                    "dexscreener_meta",
                    ProviderTier.FREE_API,
                    g["_dexscreener_token_metadata"],
                    weight=10.0,
                    rate_limit_rps=3.0,
                ),
                Provider(
                    "dexscreener_price",
                    ProviderTier.FREE_API,
                    g["_dexscreener_price"],
                    weight=8.0,
                    rate_limit_rps=3.0,
                ),
                Provider(
                    "birdeye_overview_api",
                    ProviderTier.FREEMIUM,
                    g["_birdeye_overview"],
                    weight=5.0,
                    rate_limit_rps=2.0,
                    requires_key=True,
                    key_env="BIRDEYE_API_KEY",
                    monthly_quota=50000,
                ),
            ],
        ),
    }
