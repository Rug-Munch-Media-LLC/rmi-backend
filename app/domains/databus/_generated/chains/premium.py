"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

Premium and security provider chains: premium scanner (10 chains),
webhook system, Arkham WebSocket, volume authenticity, OHLCV engine,
and token security matrix.

Source: app/domains/databus/_generated/provider_chains.py (original god file).
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.databus.provider_core import Provider, ProviderChain, ProviderTier

logger = get_logger("databus.providers.chains.premium")


def _premium_provider(name: str, fn: Any, rps: float = 3.0) -> Provider:
    """Factory for premium scanner providers."""
    return Provider(name, ProviderTier.LOCAL, fn, weight=10.0, rate_limit_rps=rps)


def build_premium_chains(chains: dict[str, ProviderChain]) -> None:
    """Build premium scanner, webhook, WS, volume, OHLCV, and token security chains."""

    # ── PREMIUM SCANNER - 10 high-value detection chains ──
    try:
        from app.databus.premium_scanner import (
            detect_bot_farms,
            detect_bundles,
            detect_copy_trading,
            detect_fresh_wallets,
            detect_insider_signals,
            detect_mev_sandwich,
            detect_snipers,
            detect_wash_trading,
            find_dev_wallets,
            map_clusters,
        )

        chains["bundle_detect"] = ProviderChain(
            "bundle_detect",
            description="Coordinated wallet bundle detection (RugMaps-style cluster analysis)",
            providers=[_premium_provider("bundle_scanner", detect_bundles)],
        )

        chains["cluster_map"] = ProviderChain(
            "cluster_map",
            description="Full wallet cluster mapping - funders, recipients, counterparties (graph-ready)",
            providers=[_premium_provider("cluster_mapper", map_clusters, rps=1.0)],
        )

        chains["dev_finder"] = ProviderChain(
            "dev_finder",
            description="Find developer/creator wallets behind tokens (deployer\u2192funder\u2192team)",
            providers=[_premium_provider("dev_finder_scanner", find_dev_wallets)],
        )

        chains["sniper_detect"] = ProviderChain(
            "sniper_detect",
            description="Sniper detection - first-block buyers with fast dump patterns",
            providers=[_premium_provider("sniper_scanner", detect_snipers)],
        )

        chains["bot_farm_detect"] = ProviderChain(
            "bot_farm_detect",
            description="Bot farm detection - identical behavior patterns across wallets",
            providers=[_premium_provider("bot_farm_scanner", detect_bot_farms)],
        )

        chains["copy_trade_detect"] = ProviderChain(
            "copy_trade_detect",
            description="Copy trading pattern detection - wallets mirroring trades with delay",
            providers=[_premium_provider("copy_trade_scanner", detect_copy_trading)],
        )

        chains["insider_detect"] = ProviderChain(
            "insider_detect",
            description="Insider trading signals - large buys before major announcements",
            providers=[_premium_provider("insider_scanner", detect_insider_signals)],
        )

        chains["wash_trade_detect"] = ProviderChain(
            "wash_trade_detect",
            description="Wash trading detection - circular transactions, self-trading",
            providers=[_premium_provider("wash_trade_scanner", detect_wash_trading)],
        )

        chains["mev_detect"] = ProviderChain(
            "mev_detect",
            description="MEV sandwich attack detection - frontrun/backrun patterns",
            providers=[_premium_provider("mev_scanner", detect_mev_sandwich)],
        )

        chains["fresh_wallet_analysis"] = ProviderChain(
            "fresh_wallet_analysis",
            description="Fresh wallet concentration analysis - high new-wallet % = rug risk",
            providers=[_premium_provider("fresh_wallet_scanner", detect_fresh_wallets, rps=2.0)],
        )

        logger.info(
            "Premium scanner chains registered: bundle_detect, cluster_map, dev_finder, "
            "sniper_detect, bot_farm, copy_trade, insider, wash_trade, mev, fresh_wallets"
        )
    except ImportError as e:
        logger.warning(f"Premium scanner not available: {e}")

    # ── WEBHOOK SYSTEM ──
    try:
        from app.databus.webhooks import (
            handle_webhook,
            list_webhooks,
            setup_webhook,
        )

        chains["webhook_handler"] = ProviderChain(
            "webhook_handler",
            description="Intelligent webhook receiver - Arkham, Helius, Moralis, Alchemy + custom",
            providers=[
                Provider(
                    "webhook_processor",
                    ProviderTier.LOCAL,
                    lambda **kw: handle_webhook(
                        kw.get("service", ""),
                        kw.get("payload", {}),
                        kw.get("headers", {}),
                        kw.get("raw_body", b""),
                    ),
                    weight=10.0,
                )
            ],
        )
    except ImportError:
        pass

    # ── ARKHAM WEBSOCKET - real-time intelligence streaming ──
    try:
        from app.databus.arkham_ws import arkham_ws_subscribe

        chains["arkham_ws"] = ProviderChain(
            "arkham_ws",
            description="Arkham Intelligence WebSocket - real-time entity updates, transfers, labels",
            providers=[
                Provider(
                    "arkham_ws_stream",
                    ProviderTier.FREEMIUM,
                    arkham_ws_subscribe,
                    weight=10.0,
                    rate_limit_rps=10.0,
                    requires_key=True,
                    key_env="ARKHAM_WS_KEY",
                    monthly_quota=500000,
                )
            ],
        )
        logger.info("Arkham WebSocket chain registered")
    except ImportError:
        logger.info("Arkham WS module not found - skipping WS chain")

    # ── RUGCHARTS: Volume Authenticity (Fake Volume %) ──
    try:
        from app.databus.volume_authenticity import analyze_volume_authenticity

        chains["volume_authenticity"] = ProviderChain(
            "volume_authenticity",
            description="Fake volume detection - 4-layer analysis (statistical, graph, heuristic, ML) with bootstrap CI",
            providers=[
                Provider(
                    "volume_auth_scorer",
                    ProviderTier.LOCAL,
                    analyze_volume_authenticity,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        logger.info("Volume Authenticity chain registered")
    except ImportError:
        logger.info("Volume Authenticity module not found")

    # ── RUGCHARTS: OHLCV Engine ──
    try:
        from app.databus.ohlcv_engine import fetch_ohlcv, ingest_trade_data

        chains["ohlcv"] = ProviderChain(
            "ohlcv",
            description="Real-time OHLCV candle aggregation (1m/5m/15m/1h/4h/1d) with authenticity scoring",
            providers=[
                Provider(
                    "ohlcv_fetcher",
                    ProviderTier.LOCAL,
                    fetch_ohlcv,
                    weight=10.0,
                    rate_limit_rps=20.0,
                ),
                Provider(
                    "ohlcv_ingest",
                    ProviderTier.LOCAL,
                    ingest_trade_data,
                    weight=5.0,
                    rate_limit_rps=50.0,
                ),
            ],
        )
        logger.info("OHLCV Engine chain registered")
    except ImportError:
        logger.info("OHLCV Engine module not found")

    # ── RUGCHARTS: Token Security Matrix (37+ checks) ──
    try:
        from app.databus.token_security import get_check_matrix_endpoint, run_full_scan

        chains["token_security"] = ProviderChain(
            "token_security",
            description="37+ security checks - GoPlus, honeypot, contract, liquidity, holders, deployer, tokenomics, rug pull",
            providers=[
                Provider(
                    "security_scanner",
                    ProviderTier.LOCAL,
                    run_full_scan,
                    weight=10.0,
                    rate_limit_rps=10.0,
                ),
                Provider(
                    "check_matrix",
                    ProviderTier.LOCAL,
                    get_check_matrix_endpoint,
                    weight=1.0,
                    rate_limit_rps=60.0,
                ),
            ],
        )
        logger.info("Token Security Matrix chain registered (37+ checks)")
    except ImportError:
        logger.info("Token Security module not found")
