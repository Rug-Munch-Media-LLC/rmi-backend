"""
Market provider chains — market data & sentiment.

Chains: market_overview, trending, hype_score. Split from ``market.py``
(see ``market/__init__.py``); ``market_movers`` moved to
``market/market_movers.py`` so the facade keeps the original order.
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_market_data_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build overview, trending, and sentiment fallback chains."""
    chains: dict[str, ProviderChain] = {}
    chains.update(_market_overview_chain(g))
    chains.update(_trending_chain(g))
    chains.update(_hype_score_chain(g))
    return chains


def _market_overview_chain(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Global market cap, BTC/ETH/SOL prices, fear & greed."""
    return {
        "market_overview": ProviderChain(
            data_type="market_overview",
            description="Global market cap, BTC/ETH/SOL prices, fear & greed",
            providers=[
                Provider(
                    "rmi_market_overview",
                    ProviderTier.LOCAL,
                    g["_passthrough_market_overview"],
                    weight=10.0,
                ),
                Provider(
                    "coingecko_global",
                    ProviderTier.FREEMIUM,
                    g["_coingecko_price"],
                    weight=5.0,
                    requires_key=True,
                    key_env="COINGECKO_API_KEY",
                ),
            ],
        ),
    }


def _trending_chain(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Trending tokens across chains."""
    return {
        "trending": ProviderChain(
            data_type="trending",
            description="Trending tokens across chains",
            providers=[
                Provider("rmi_trending", ProviderTier.LOCAL, g["_passthrough_trending"], weight=10.0),
                Provider(
                    "solana_tracker_trending",
                    ProviderTier.FREEMIUM,
                    g["_solana_tracker_trending"],
                    weight=8.0,
                    rate_limit_rps=3.0,
                    requires_key=True,
                    key_env="SOLANATRACKER_API_KEY",
                    monthly_quota=5000,
                ),
                Provider(
                    "gmgn_trending",
                    ProviderTier.FREE_API,
                    g["_gmgn_trending"],
                    weight=7.0,
                    rate_limit_rps=1.0,
                    requires_key=True,
                    key_env="GMGN_API_KEY",
                ),
                Provider(
                    "dexscreener_trending",
                    ProviderTier.FREE_API,
                    g["_dexscreener_price"],
                    weight=5.0,
                    rate_limit_rps=1.0,
                ),
            ],
        ),
    }


def _hype_score_chain(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """C5 — hype score (LOCAL tier: own ClickHouse + free trending)."""
    return {
        "hype_score": ProviderChain(
            data_type="hype_score",
            description="Composite token hype score (0-100) from free social signals + own on-chain velocity — CoinGecko/DexScreener trending, dex_events volume growth, evm_transfers holder growth, price oracle momentum",
            providers=[
                Provider(
                    "lite_chain_indexer_hype_score",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_hype_score"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse dex_events + evm_transfers + unified trending — weighted composite with 72h exponential decay",
                ),
            ],
        ),
    }
