"""Market provider chains — core token price tiers."""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def _token_price_providers(g: dict[str, Any]) -> list[Provider]:
    """Build the ordered token-price provider list."""
    return [
        Provider(
            "token_price_local",
            ProviderTier.LOCAL,
            g["token_price_local"],
            weight=25.0,
            is_local=True,
            timeout=1.5,
            description="F2 price oracle — own dex_events + free APIs (Redis/ClickHouse cached)",
        ),
        Provider("local_price", ProviderTier.LOCAL, g["_local_token_price"], weight=10.0),
        Provider(
            "solana_tracker",
            ProviderTier.FREEMIUM,
            g["_solana_tracker_price"],
            weight=8.0,
            rate_limit_rps=3.0,
            requires_key=True,
            key_env="SOLANATRACKER_API_KEY",
            monthly_quota=5000,
        ),
        Provider(
            "dexscreener",
            ProviderTier.FREE_API,
            g["_dexscreener_price"],
            weight=5.0,
            rate_limit_rps=2.0,
            health_url="https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112",
        ),
        Provider(
            "coingecko",
            ProviderTier.FREE_API,
            g["_coingecko_price"],
            weight=5.0,
            health_url="https://api.coingecko.com/api/v3/ping",
        ),
        Provider(
            "moralis",
            ProviderTier.PAID,
            g["_moralis_price"],
            weight=1.0,
            requires_key=True,
            key_env="MORALIS_API_KEY",
            monthly_quota=5000,
        ),
    ]


def _token_price_chain(g: dict[str, Any]) -> ProviderChain:
    """Token price across DEXs and CEXs."""
    return ProviderChain(
        data_type="token_price", description="Token price across DEXs and CEXs", providers=_token_price_providers(g)
    )


def _token_meta_chain(g: dict[str, Any]) -> ProviderChain:
    """Token metadata — decimals, symbol, name, last known price."""
    return ProviderChain(
        data_type="token_meta",
        description="Token metadata — decimals, symbol, name, last known price",
        providers=[
            Provider(
                "token_meta_local",
                ProviderTier.LOCAL,
                g["token_meta_local"],
                weight=10.0,
                is_local=True,
                description="F2 price oracle token_meta (ClickHouse token_meta + DexScreener decimals)",
            )
        ],
    )


def _market_price_free_providers(g: dict[str, Any]) -> list[Provider]:
    """Build the ordered free market-price fallback list."""
    return [
        Provider(
            "free_market_clickhouse_twap",
            ProviderTier.LOCAL,
            g["_free_market_clickhouse_twap"],
            weight=20.0,
            is_local=True,
            description="Own ClickHouse dex_events TWAP via own_price_oracle — zero cost",
        ),
        Provider(
            "dexscreener_price_free",
            ProviderTier.FREE_API,
            g["_dexscreener_price"],
            weight=10.0,
            rate_limit_rps=2.0,
            description="DexScreener latest/dex/tokens — live pair price (free)",
        ),
        Provider(
            "free_market_coingecko",
            ProviderTier.FREE_API,
            g["_free_market_coingecko"],
            weight=6.0,
            rate_limit_rps=0.5,
            description="CoinGecko simple/token_price — free tier 10-30 req/min",
        ),
        Provider(
            "free_market_geckoterminal",
            ProviderTier.FREE_API,
            g["_free_market_geckoterminal"],
            weight=5.0,
            rate_limit_rps=2.0,
            description="GeckoTerminal token price (free, no auth)",
        ),
        Provider("free_market_paraswap_quote", ProviderTier.FREE_API, g["_free_market_paraswap_quote"], weight=3.0, rate_limit_rps=2.0, description="ParaSwap prices — best-route DEX quote (free, no auth)"),
        Provider("free_market_oneinch_quote", ProviderTier.FREEMIUM, g["_free_market_oneinch_quote"], weight=2.0, rate_limit_rps=2.0, requires_key=True, key_env="ONEINCH_API_KEY", description="1inch swap quote — freemium (API key required as of 2026)"),
    ]
def _market_price_free_chain(g: dict[str, Any]) -> ProviderChain:
    """Free market price fallback: ClickHouse TWAP → DexScreener → CoinGecko → GeckoTerminal → DEX quotes."""
    return ProviderChain(
        data_type="market_price_free",
        description="Free market price fallback: ClickHouse TWAP → DexScreener → CoinGecko → GeckoTerminal → DEX quotes",
        providers=_market_price_free_providers(g),
    )


def build_token_price_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Return token price, metadata, and free market-price chains."""
    return {
        "token_price": _token_price_chain(g),
        "token_meta": _token_meta_chain(g),
        "market_price_free": _market_price_free_chain(g),
    }
