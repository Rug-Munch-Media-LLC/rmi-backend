"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

Alchemy, Moralis, and Arkham provider chains: token balances, metadata,
NFTs, transactions, net worth, token search, entity intelligence,
labels, portfolio, transfers, counterparties, and MCP bridge.

Source: app/domains/databus/_generated/provider_chains.py (original god file).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.databus.provider_core import Provider, ProviderChain, ProviderTier
from app.databus.provider_implementations import (
    _alchemy_token_balances,
    _alchemy_token_metadata,
    _arkham_counterparties,
    _arkham_entity,
    _arkham_intel_search,
    _arkham_labels,
    _arkham_portfolio,
    _arkham_transfers,
    _dexscreener_token_metadata,
    _etherscan_tx_trace,
    _local_wallet_labels,
    _mcp_bridge,
    _moralis_search_tokens,
    _moralis_wallet_net_worth,
    _moralis_wallet_nfts,
    _moralis_wallet_tokens,
    _moralis_wallet_transactions,
)

logger = get_logger("databus.providers.chains.alchemy_arkham")

def build_alchemy_arkham_chains(chains: dict[str, ProviderChain]) -> None:
    """Build Alchemy, Moralis, Arkham, and MCP bridge chains."""
    # ── Alchemy chains ──
    chains["wallet_tokens"] = ProviderChain(
        data_type="wallet_tokens",
        description="Token balances for any address (Alchemy + Moralis)",
        providers=[
            Provider(
                "alchemy_balances",
                ProviderTier.FREEMIUM,
                _alchemy_token_balances,
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ALCHEMY_API_KEY",
                monthly_quota=300000,
            ),
            Provider(
                "moralis_tokens",
                ProviderTier.FREEMIUM,
                _moralis_wallet_tokens,
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )
    chains["token_metadata"] = ProviderChain(
        data_type="token_metadata",
        description="Token metadata lookup (DexScreener free \u2192 Alchemy freemium)",
        providers=[
            Provider(
                "dexscreener_meta",
                ProviderTier.FREE_API,
                _dexscreener_token_metadata,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "alchemy_metadata",
                ProviderTier.FREEMIUM,
                _alchemy_token_metadata,
                weight=5.0,
                rate_limit_rps=10.0,
                requires_key=True,
                key_env="ALCHEMY_API_KEY",
                monthly_quota=300000,
            ),
        ],
    )
    chains["wallet_nfts"] = ProviderChain(
        data_type="wallet_nfts",
        description="NFT holdings for any address (Moralis - free tier available)",
        providers=[
            Provider(
                "moralis_nfts",
                ProviderTier.FREEMIUM,
                _moralis_wallet_nfts,
                weight=5.0,
                rate_limit_rps=1.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    # ── Moralis expanded chains ──
    chains["wallet_transactions"] = ProviderChain(
        data_type="wallet_transactions",
        description="Wallet transaction history (Etherscan free \u2192 Moralis freemium)",
        providers=[
            Provider(
                "etherscan_wallet",
                ProviderTier.FREE_API,
                _etherscan_tx_trace,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "moralis_txns",
                ProviderTier.FREEMIUM,
                _moralis_wallet_transactions,
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["wallet_net_worth"] = ProviderChain(
        data_type="wallet_net_worth",
        description="Wallet net worth across chains (Moralis)",
        providers=[
            Provider(
                "moralis_net_worth",
                ProviderTier.FREEMIUM,
                _moralis_wallet_net_worth,
                weight=10.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["token_search"] = ProviderChain(
        data_type="token_search",
        description="Search tokens by name/symbol/address (Moralis + MCP)",
        providers=[
            Provider(
                "moralis_search",
                ProviderTier.FREEMIUM,
                _moralis_search_tokens,
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    # ── Arkham Intelligence - Free trial month (Nov 2026) ──
    # Priority: LOCAL labels \u2192 Arkham (free trial) \u2192 paid alternatives
    chains["entity_intel"] = ProviderChain(
        data_type="entity_intel",
        description="Entity resolution and risk scoring (local labels \u2192 Arkham free \u2192 Moralis)",
        providers=[
            Provider("wallet_labels_local", ProviderTier.LOCAL, _local_wallet_labels, weight=10.0),
            Provider(
                "arkham_entity",
                ProviderTier.FREEMIUM,
                _arkham_entity,
                weight=8.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
            Provider(
                "moralis_labels",
                ProviderTier.FREEMIUM,
                _moralis_wallet_tokens,
                weight=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["arkham_labels"] = ProviderChain(
        data_type="arkham_labels",
        description="Entity labels with confidence scores (local \u2192 Arkham free trial)",
        providers=[
            Provider("wallet_labels_local", ProviderTier.LOCAL, _local_wallet_labels, weight=10.0),
            Provider(
                "arkham_labels_api",
                ProviderTier.FREEMIUM,
                _arkham_labels,
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
                _arkham_portfolio,
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
                _arkham_transfers,
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
                _arkham_counterparties,
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
                _arkham_intel_search,
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    # ── MCP Bridge - all installed MCP servers ──
    chains["mcp_bridge"] = ProviderChain(
        data_type="mcp_bridge",
        description="Universal MCP bridge - calls any local/remote MCP server tool",
        providers=[
            Provider("mcp_bridge", ProviderTier.LOCAL, _mcp_bridge, weight=10.0),
        ],
    )

    logger.info(
        "Alchemy/Arkham chains built: wallet_tokens, token_metadata, wallet_nfts, "
        "wallet_transactions, wallet_net_worth, token_search, entity_intel, "
        "arkham_labels, arkham_portfolio, arkham_transfers, arkham_counterparties, "
        "arkham_intel, mcp_bridge"
    )
