"""
Intel provider chains.
"""

from __future__ import annotations

from typing import Any

from ...base import Provider, ProviderChain, ProviderTier


def build_intel_chains(locals_dict: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build intel provider chains."""
    chains: dict[str, ProviderChain] = {}
    _g = locals_dict
    # ── Conditional imports for optional databus modules ──
    try:
        from app.databus.arkham_ws import arkham_ws_subscribe
    except ImportError:
        arkham_ws_subscribe = None  # type: ignore[assignment]  # optional module fallback

    chains["entity_intel"] = ProviderChain(
        data_type="entity_intel",
        description="Entity resolution: ClickHouse (~295k curated) → Redis (hot cache) → Arkham → Moralis",
        providers=[
            Provider(
                "clickhouse_wallet_memory",
                ProviderTier.LOCAL,
                _g["_clickhouse_wallet_labels"],
                weight=20.0,
                is_local=True,
                description="ClickHouse wallet_labels table — ~295k curated labels",
            ),
            Provider(
                "wallet_labels_local",
                ProviderTier.LOCAL,
                _g["_local_wallet_labels"],
                weight=10.0,
            ),
            Provider(
                "arkham_entity",
                ProviderTier.FREEMIUM,
                _g["_arkham_entity"],
                weight=8.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
            Provider(
                "moralis_labels",
                ProviderTier.FREEMIUM,
                _g["_moralis_wallet_tokens"],
                weight=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["arkham_labels"] = ProviderChain(
        data_type="arkham_labels",
        description="Entity labels with confidence scores (local → Arkham free trial)",
        providers=[
            Provider(
                "clickhouse_wallet_memory",
                ProviderTier.LOCAL,
                _g["_clickhouse_wallet_labels"],
                weight=20.0,
                is_local=True,
                description="ClickHouse wallet_labels table — ~295k curated labels",
            ),
            Provider("wallet_labels_local", ProviderTier.LOCAL, _g["_local_wallet_labels"], weight=10.0),
            Provider(
                "arkham_labels_api",
                ProviderTier.FREEMIUM,
                _g["_arkham_labels"],
                weight=8.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    chains["arkham_portfolio"] = ProviderChain(
        data_type="arkham_portfolio",
        description="Portfolio holdings with USD values (Arkham free trial)",
        providers=[
            Provider(
                "arkham_portfolio_api",
                ProviderTier.FREEMIUM,
                _g["_arkham_portfolio"],
                weight=10.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    chains["arkham_transfers"] = ProviderChain(
        data_type="arkham_transfers",
        description="Transfer history with counterparty mapping (Arkham free trial)",
        providers=[
            Provider(
                "arkham_transfers_api",
                ProviderTier.FREEMIUM,
                _g["_arkham_transfers"],
                weight=10.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    chains["arkham_counterparties"] = ProviderChain(
        data_type="arkham_counterparties",
        description="Counterparty network and relationship mapping (Arkham free trial)",
        providers=[
            Provider(
                "arkham_counterparties_api",
                ProviderTier.FREEMIUM,
                _g["_arkham_counterparties"],
                weight=10.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    chains["arkham_intel"] = ProviderChain(
        data_type="arkham_intel",
        description="Entity intelligence search (Arkham free trial)",
        providers=[
            Provider(
                "arkham_intel_search",
                ProviderTier.FREEMIUM,
                _g["_arkham_intel_search"],
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    chains["scraped_intel"] = ProviderChain(
        data_type="scraped_intel",
        description="Scraped intelligence from Pryscraper (scam reports, threat intel, blockchain data)",
        providers=[
            Provider(
                "scraped_intel_local",
                ProviderTier.LOCAL,
                _g["_scraped_intel_provider"],
                weight=10.0,
                is_local=True,
                description="Scraped intel from pryscraper Postgres",
            ),
        ],
    )

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

    return chains
