"""
Market provider chains — price & quote fallbacks.

Chains: birdeye_price, price_fallback. Split from ``market.py``
(see ``market/__init__.py``). Token price/meta live in
``market/token_price.py``; these two are registered later, after the
discovery chains, which is where the pre-split ``market.py`` put them.
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_quote_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build price, quote, and token-metadata fallback chains."""
    chains: dict[str, ProviderChain] = {}
    chains.update(_birdeye_price_chain(g))
    chains.update(_price_fallback_chain(g))
    return chains


def _birdeye_price_chain(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Birdeye-sourced real-time price with a DexScreener fallback."""
    return {
        "birdeye_price": ProviderChain(
            data_type="birdeye_price",
            description="Real-time token price from Birdeye",
            providers=[
                Provider(
                    "birdeye_price_api",
                    ProviderTier.FREEMIUM,
                    g["_birdeye_price"],
                    weight=5.0,
                    rate_limit_rps=5.0,
                    requires_key=True,
                    key_env="BIRDEYE_API_KEY",
                    monthly_quota=50000,
                    health_url="https://public-api.birdeye.so/defi/price?address=So11111111111111111111111111111111111111112",
                ),
                Provider(
                    "dexscreener_price",
                    ProviderTier.FREE_API,
                    g["_dexscreener_price"],
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
            ],
        ),
    }


def _price_fallback_chain(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """CoinGecko → DexScreener → Birdeye dedicated price fallback.

    Each provider returns None on failure so ``ProviderChain.fetch()`` falls
    through to the next tier automatically.
    """
    return {
        "price_fallback": ProviderChain(
            data_type="price_fallback",
            description="Price fallback chain: CoinGecko → DexScreener → Birdeye",
            providers=[
                Provider(
                    "coingecko_price_fallback",
                    ProviderTier.FREE_API,
                    g["_coingecko_price"],
                    weight=10.0,
                    rate_limit_rps=0.5,
                    health_url="https://api.coingecko.com/api/v3/ping",
                    description="CoinGecko simple/token_price — free tier 10-30 req/min",
                ),
                Provider(
                    "dexscreener_price_fallback",
                    ProviderTier.FREE_API,
                    g["_dexscreener_price"],
                    weight=8.0,
                    rate_limit_rps=2.0,
                    health_url="https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112",
                    description="DexScreener latest/dex/tokens — live pair price (free)",
                ),
                Provider(
                    "birdeye_price_fallback",
                    ProviderTier.FREEMIUM,
                    g["_birdeye_price"],
                    weight=5.0,
                    rate_limit_rps=5.0,
                    requires_key=True,
                    key_env="BIRDEYE_API_KEY",
                    monthly_quota=50000,
                    health_url="https://public-api.birdeye.so/defi/price?address=So11111111111111111111111111111111111111112",
                    description="Birdeye real-time token price (freemium, key required)",
                ),
            ],
        ),
    }
