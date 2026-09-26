"""
Market provider chains — discovery, on-chain & external API.

Chains: token_trades, top_traders, dune_early_buyers, defillama_tvl,
defillama_chains, blockchair_address, blockchair_stats, birdeye_overview.
Split from ``market.py`` (see ``market/__init__.py``).
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_discovery_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build trade/intel discovery and external API lookup chains."""
    chains: dict[str, ProviderChain] = {}
    chains.update(_trade_intel_chains(g))
    chains.update(_defi_tvl_chains(g))
    chains.update(_blockchair_chains(g))
    chains.update(_birdeye_overview_chain(g))
    return chains


def _trade_intel_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Live trades, top traders, and Dune early buyers."""
    return {
        "token_trades": ProviderChain(
            data_type="token_trades",
            description="Live token trades (buys/sells) from DexScreener",
            providers=[
                Provider(
                    "dexscreener_trades",
                    ProviderTier.FREE_API,
                    g["_dexscreener_trades"],
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
            ],
        ),
        "top_traders": ProviderChain(
            data_type="top_traders",
            description="Top profitable traders and win rates for a token",
            providers=[
                Provider(
                    "dexscreener_top_traders",
                    ProviderTier.FREE_API,
                    g["_dexscreener_top_traders"],
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
            ],
        ),
        "dune_early_buyers": ProviderChain(
            data_type="dune_early_buyers",
            description="First 5-minute buyers of a token (Dune Analytics, low-CU cached query)",
            providers=[
                Provider(
                    "dune_early_buyers_api",
                    ProviderTier.FREEMIUM,
                    g["_dune_early_buyers"],
                    weight=10.0,
                    rate_limit_rps=0.5,
                    requires_key=True,
                    key_env="DUNE_API_KEY",
                    monthly_quota=10000,
                ),
            ],
        ),
    }


def _defi_tvl_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """DeFiLlama global TVL and per-chain TVL breakdown."""
    return {
        "defillama_tvl": ProviderChain(
            data_type="defillama_tvl",
            description="Global DeFi TVL and top protocols (completely free)",
            providers=[
                Provider(
                    "defillama_tvl_api",
                    ProviderTier.FREE_API,
                    g["_defillama_tvl"],
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
            ],
        ),
        "defillama_chains": ProviderChain(
            data_type="defillama_chains",
            description="TVL breakdown by blockchain (completely free)",
            providers=[
                Provider(
                    "defillama_chains_api",
                    ProviderTier.FREE_API,
                    g["_defillama_chains"],
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
            ],
        ),
    }


def _blockchair_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Blockchair multi-chain balances and network statistics."""
    return {
        "blockchair_address": ProviderChain(
            data_type="blockchair_address",
            description="Multi-chain address balance and tx count (BTC, ETH, SOL, etc.)",
            providers=[
                Provider(
                    "blockchair_addr_api",
                    ProviderTier.FREE_API,
                    g["_blockchair_address"],
                    weight=10.0,
                    rate_limit_rps=3.0,
                ),
            ],
        ),
        "blockchair_stats": ProviderChain(
            data_type="blockchair_stats",
            description="Multi-chain network statistics and mempool data",
            providers=[
                Provider(
                    "blockchair_stats_api",
                    ProviderTier.FREE_API,
                    g["_blockchair_stats"],
                    weight=10.0,
                    rate_limit_rps=3.0,
                ),
            ],
        ),
    }


def _birdeye_overview_chain(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Birdeye token overview, liquidity, and holder stats."""
    return {
        "birdeye_overview": ProviderChain(
            data_type="birdeye_overview",
            description="Token overview, liquidity, and holder stats (Solana/EVM)",
            providers=[
                Provider(
                    "birdeye_overview_api",
                    ProviderTier.FREEMIUM,
                    g["_birdeye_overview"],
                    weight=10.0,
                    rate_limit_rps=2.0,
                    requires_key=True,
                    key_env="BIRDEYE_API_KEY",
                    monthly_quota=50000,
                ),
            ],
        ),
    }
