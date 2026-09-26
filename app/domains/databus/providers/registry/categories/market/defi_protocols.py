"""
Market provider chains — DeFi protocol tracker.

Chain: defi_protocols. Split out of ``market/token_intel.py`` so the market
facade can register it between ``dex_data`` and ``token_detail``, which is
where the pre-split ``market.py`` put it.
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_defi_protocols_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """DeFi protocol tracker — TVL, chains, categories."""
    return {
        "defi_protocols": ProviderChain(
            data_type="defi_protocols",
            description="DeFi protocol tracker — TVL, chains, categories (DeFiLlama free)",
            providers=[
                Provider(
                    "defillama_tvl_api",
                    ProviderTier.FREE_API,
                    g["_defillama_tvl"],
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
                Provider(
                    "defillama_chains_api",
                    ProviderTier.FREE_API,
                    g["_defillama_chains"],
                    weight=5.0,
                    rate_limit_rps=2.0,
                ),
            ],
        ),
    }
