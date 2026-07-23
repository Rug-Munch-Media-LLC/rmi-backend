"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

Core data provider chains: token pricing, market overview, trending tokens,
token trades, top traders, Dune early buyers, news feed, alerts,
developer activity, and URL security scanning.

Source: app/domains/databus/_generated/provider_chains.py (original god file).
"""

from app.core.logging import get_logger
from app.databus.provider_core import Provider, ProviderChain, ProviderTier
from app.databus.provider_implementations import (
    _coindesk_news,
    _coingecko_price,
    _dexscreener_holders,
    _dexscreener_price,
    _dexscreener_top_traders,
    _dexscreener_trades,
    _dune_early_buyers,
    _local_token_price,
    _messari_news,
    _moralis_price,
    _passthrough_alerts,
    _passthrough_market_overview,
    _passthrough_news,
    _passthrough_scanner,
    _passthrough_trending,
    _santiment_dev_activity,
    _solana_tracker_price,
    _solana_tracker_trending,
    _virustotal_url_scan,
)

logger = get_logger("databus.providers.chains.core")


def build_core_chains(chains: dict[str, ProviderChain]) -> None:
    """Build core pricing, trading, market, and security chains."""
    # Token Price
    chains["token_price"] = ProviderChain(
        data_type="token_price",
        description="Token price across DEXs and CEXs",
        providers=[
            Provider("local_price", ProviderTier.LOCAL, _local_token_price, weight=10.0),
            Provider(
                "solana_tracker",
                ProviderTier.FREEMIUM,
                _solana_tracker_price,
                weight=8.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="SOLANATRACKER_API_KEY",
                monthly_quota=5000,
            ),
            Provider(
                "dexscreener",
                ProviderTier.FREE_API,
                _dexscreener_price,
                weight=5.0,
                rate_limit_rps=2.0,
            ),
            Provider("coingecko", ProviderTier.FREE_API, _coingecko_price, weight=5.0),
            Provider(
                "moralis",
                ProviderTier.PAID,
                _moralis_price,
                weight=1.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
                monthly_quota=5000,
            ),
        ],
    )

    # Market Overview
    chains["market_overview"] = ProviderChain(
        data_type="market_overview",
        description="Global market cap, BTC/ETH/SOL prices, fear & greed",
        providers=[
            Provider(
                "rmi_market_overview", ProviderTier.LOCAL, _passthrough_market_overview, weight=10.0
            ),
            Provider(
                "coingecko_global",
                ProviderTier.FREEMIUM,
                _coingecko_price,
                weight=5.0,
                requires_key=True,
                key_env="COINGECKO_API_KEY",
            ),
        ],
    )

    # Trending
    chains["trending"] = ProviderChain(
        data_type="trending",
        description="Trending tokens across chains",
        providers=[
            Provider("rmi_trending", ProviderTier.LOCAL, _passthrough_trending, weight=10.0),
            Provider(
                "solana_tracker_trending",
                ProviderTier.FREEMIUM,
                _solana_tracker_trending,
                weight=8.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="SOLANATRACKER_API_KEY",
                monthly_quota=5000,
            ),
            Provider(
                "dexscreener_trending",
                ProviderTier.FREE_API,
                _dexscreener_price,
                weight=5.0,
                rate_limit_rps=1.0,
            ),
        ],
    )

    # Token Trades (Live buys/sells)
    chains["token_trades"] = ProviderChain(
        data_type="token_trades",
        description="Live token trades (buys/sells) from DexScreener",
        providers=[
            Provider(
                "dexscreener_trades",
                ProviderTier.FREE_API,
                _dexscreener_trades,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # Top Traders (Profitable wallets for a token)
    chains["top_traders"] = ProviderChain(
        data_type="top_traders",
        description="Top profitable traders and win rates for a token",
        providers=[
            Provider(
                "dexscreener_top_traders",
                ProviderTier.FREE_API,
                _dexscreener_top_traders,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # ── Dune Analytics (Free Tier: 10k CU/mo) ──
    chains["dune_early_buyers"] = ProviderChain(
        data_type="dune_early_buyers",
        description="First 5-minute buyers of a token (Dune Analytics, low-CU cached query)",
        providers=[
            Provider(
                "dune_early_buyers_api",
                ProviderTier.FREEMIUM,
                _dune_early_buyers,
                weight=10.0,
                rate_limit_rps=0.5,
                requires_key=True,
                key_env="DUNE_API_KEY",
                monthly_quota=10000,
            ),
        ],
    )

    # News
    chains["news"] = ProviderChain(
        data_type="news",
        description="Real-time crypto news from 30+ sources + Messari + CoinDesk institutional feeds",
        providers=[
            Provider("rmi_news", ProviderTier.LOCAL, _passthrough_news, weight=10.0),
            Provider(
                "messari_news",
                ProviderTier.FREEMIUM,
                _messari_news,
                weight=8.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="MESSARI_API_KEY",
                monthly_quota=10000,
            ),
            Provider(
                "coindesk_news",
                ProviderTier.FREEMIUM,
                _coindesk_news,
                weight=8.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="COINDESK_API_KEY",
                monthly_quota=10000,
            ),
        ],
    )

    # Alerts / Live Intel
    chains["alerts"] = ProviderChain(
        data_type="alerts",
        description="Active threat alerts from scanner pipeline",
        providers=[
            Provider("rmi_alerts", ProviderTier.LOCAL, _passthrough_alerts, weight=10.0),
        ],
    )

    # ── Advanced Security & Dev Activity ──
    chains["dev_activity"] = ProviderChain(
        data_type="dev_activity",
        description="GitHub developer activity and social volume (Santiment free tier)",
        providers=[
            Provider(
                "santiment_dev",
                ProviderTier.FREEMIUM,
                _santiment_dev_activity,
                weight=10.0,
                rate_limit_rps=1.0,
                requires_key=True,
                key_env="SANTIMENT_API_KEY",
                monthly_quota=3000,
            ),
        ],
    )
    chains["url_security_scan"] = ProviderChain(
        data_type="url_security_scan",
        description="Phishing and malware scan for token websites (VirusTotal free tier)",
        providers=[
            Provider(
                "virustotal_scan",
                ProviderTier.FREEMIUM,
                _virustotal_url_scan,
                weight=10.0,
                rate_limit_rps=0.5,
                requires_key=True,
                key_env="VIRUSTOTAL_API_KEY",
                monthly_quota=15000,
            ),
        ],
    )

    logger.info(
        "Core chains built: token_price, market_overview, trending, token_trades, "
        "top_traders, dune_early_buyers, news, alerts, dev_activity, url_security_scan"
    )
