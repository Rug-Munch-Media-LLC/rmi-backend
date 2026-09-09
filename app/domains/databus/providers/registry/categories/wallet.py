"""
Wallet provider chains.
"""

from __future__ import annotations

from typing import Any

from ...base import Provider, ProviderChain, ProviderTier


def build_wallet_chains(locals_dict: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build wallet provider chains."""
    chains: dict[str, ProviderChain] = {}
    _g = locals_dict

    chains["wallet_labels"] = ProviderChain(
        data_type="wallet_labels",
        description="Address labels: ClickHouse (~140k curated) → Redis (hot cache) → external APIs",
        providers=[
            Provider(
                "clickhouse_wallet_memory",
                ProviderTier.LOCAL,
                _g["_clickhouse_wallet_labels"],
                weight=20.0,
                is_local=True,
                description="ClickHouse wallet_labels table — ~140k curated labels",
            ),
            Provider(
                "wallet_memory_bank",
                ProviderTier.LOCAL,
                _g["_local_wallet_labels"],
                weight=10.0,
                is_local=True,
                description="Redis hot cache for curated wallet labels",
            ),
            Provider(
                "nansen_labels",
                ProviderTier.PAID,
                _g["_moralis_price"],
                weight=5.0,
                requires_key=True,
                key_env="NANSEN_API_KEY",
            ),
        ],
    )

    # ── wallet_labels_free — free multi-tier fallback (Arkham entity-label replacement) ──
    # Replaces Arkham's entity labeling with a stacked free API chain:
    #   Tier 1: own ClickHouse wallet labels (~140k curated)
    #   Tier 2: Blockscout address tags (free, no auth)
    #   Tier 3: Etherscan labels via V2 API (free tier with key)
    #   Tier 4: DexScreener top traders (free)
    #   Tier 5: pattern-based heuristics (whale, bot, DEX router, CEX from own data)
    chains["wallet_labels_free"] = ProviderChain(
        data_type="wallet_labels_free",
        description="Free wallet labeling: ClickHouse → Blockscout tags → Etherscan → DexScreener traders → heuristics",
        providers=[
            Provider(
                "clickhouse_wallet_memory",
                ProviderTier.LOCAL,
                _g["_clickhouse_wallet_labels"],
                weight=20.0,
                is_local=True,
                description="ClickHouse wallet_labels table — ~140k curated labels",
            ),
            Provider(
                "free_blockscout_labels",
                ProviderTier.FREE_API,
                _g["_free_blockscout_labels"],
                weight=8.0,
                rate_limit_rps=5.0,
                description="Blockscout address tags — contract name, public/private tags, ENS, scam flag (free)",
            ),
            Provider(
                "free_etherscan_labels",
                ProviderTier.FREEMIUM,
                _g["_free_etherscan_labels"],
                weight=5.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ETHERSCAN_API_KEY",
                description="Etherscan-family V2 — verified contract name labels (free tier with key)",
            ),
            Provider(
                "dexscreener_top_traders",
                ProviderTier.FREE_API,
                _g["_dexscreener_top_traders"],
                weight=4.0,
                rate_limit_rps=2.0,
                description="DexScreener top traders — identify active DEX trader addresses (free)",
            ),
            Provider(
                "free_wallet_pattern_heuristics",
                ProviderTier.LOCAL,
                _g["_free_wallet_pattern_heuristics"],
                weight=3.0,
                is_local=True,
                description="Pattern heuristics — whale/bot/DEX router/CEX/mixer detection from known entities + transfer volume",
            ),
        ],
    )

    chains["wallet_tokens"] = ProviderChain(
        data_type="wallet_tokens",
        description="Token balances for any address (Alchemy + Moralis)",
        providers=[
            Provider(
                "alchemy_balances",
                ProviderTier.FREEMIUM,
                _g["_alchemy_token_balances"],
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ALCHEMY_API_KEY",
                monthly_quota=300000,
            ),
            Provider(
                "moralis_tokens",
                ProviderTier.FREEMIUM,
                _g["_moralis_wallet_tokens"],
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["token_metadata"] = ProviderChain(
        data_type="token_metadata",
        description="Token metadata lookup (local price oracle → DexScreener free → Alchemy freemium)",
        providers=[
            Provider(
                "token_meta_local",
                ProviderTier.LOCAL,
                _g["token_meta_local"],
                weight=10.0,
                rate_limit_rps=30.0,
            ),
            Provider(
                "dexscreener_meta",
                ProviderTier.FREE_API,
                _g["_dexscreener_token_metadata"],
                weight=5.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "alchemy_metadata",
                ProviderTier.FREEMIUM,
                _g["_alchemy_token_metadata"],
                weight=3.0,
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
                _g["_moralis_wallet_nfts"],
                weight=5.0,
                rate_limit_rps=1.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["wallet_transactions"] = ProviderChain(
        data_type="wallet_transactions",
        description="Wallet transaction history (Etherscan free → Moralis freemium → GMGN activity)",
        providers=[
            Provider(
                "etherscan_wallet",
                ProviderTier.FREE_API,
                _g["_etherscan_tx_trace"],
                weight=10.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "moralis_txns",
                ProviderTier.FREEMIUM,
                _g["_moralis_wallet_transactions"],
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
            Provider(
                "gmgn_wallet_activity",
                ProviderTier.FREE_API,
                _g["_gmgn_wallet_tx_history"],
                weight=3.0,
                rate_limit_rps=1.0,
                description="GMGN smart-money wallet stats — PnL, winrate, buy activity (free, no key; solana-focused)",
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
                _g["_moralis_wallet_net_worth"],
                weight=10.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["token_transfers"] = ProviderChain(
        data_type="token_transfers",
        description="ERC-20 transfer history from chain-indexer ClickHouse (73M+ transfers, 8 EVM chains)",
        providers=[
            Provider(
                "lite_chain_indexer_clickhouse",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_token_transfers"],
                weight=20.0,
                is_local=True,
                description="ClickHouse evm_transfers — every ERC-20 transfer on 8 chains",
            ),
        ],
    )

    # ── C1 — wallet-to-wallet flow visualization (Arkham replacement, LOCAL) ──
    chains["wallet_flow"] = ProviderChain(
        data_type="wallet_flow",
        description="Wallet-to-wallet fund flow graph from ClickHouse evm_transfers — labeled nodes, USD edges (C1, Arkham replacement)",
        providers=[
            Provider(
                "lite_chain_indexer_wallet_flow",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_wallet_flow"],
                weight=20.0,
                is_local=True,
                description="ClickHouse evm_transfers + wallet_labels + dex_events — 1-2 hop labeled USD graph around any wallet",
            ),
        ],
    )

    chains["token_holders"] = ProviderChain(
        data_type="token_holders",
        description="Token holder analysis from chain-indexer ClickHouse (transfer aggregation)",
        providers=[
            Provider(
                "lite_chain_indexer_holders",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_holders"],
                weight=20.0,
                is_local=True,
                description="ClickHouse evm_transfers aggregation — holder distribution from transfer activity",
            ),
        ],
    )

    chains["solana_balance_changes"] = ProviderChain(
        data_type="solana_balance_changes",
        description="Solana SPL token balance deltas from chain-indexer ClickHouse (16M+ changes)",
        providers=[
            Provider(
                "lite_chain_indexer_solana",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_solana_balance_changes"],
                weight=20.0,
                is_local=True,
                description="ClickHouse solana_spl_balance_changes — SPL token balance deltas",
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
                _g["_moralis_search_tokens"],
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["wallet_balance"] = ProviderChain(
        data_type="wallet_balance",
        description="Token balances for any address (Alchemy → Moralis → Blockchair)",
        providers=[
            Provider(
                "alchemy_balances",
                ProviderTier.FREEMIUM,
                _g["_alchemy_token_balances"],
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ALCHEMY_API_KEY",
                monthly_quota=300000,
            ),
            Provider(
                "moralis_tokens",
                ProviderTier.FREEMIUM,
                _g["_moralis_wallet_tokens"],
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
            Provider(
                "blockchair_addr_api",
                ProviderTier.FREE_API,
                _g["_blockchair_address"],
                weight=3.0,
                rate_limit_rps=3.0,
            ),
        ],
    )

    chains["wallet_profile"] = ProviderChain(
        data_type="wallet_profile",
        description="Wallet profile: labels (local) → Arkham entity → Moralis holdings",
        providers=[
            Provider(
                "clickhouse_wallet_memory",
                ProviderTier.LOCAL,
                _g["_clickhouse_wallet_labels"],
                weight=20.0,
                is_local=True,
                description="ClickHouse wallet_labels table — ~140k curated labels",
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
                "moralis_tokens",
                ProviderTier.FREEMIUM,
                _g["_moralis_wallet_tokens"],
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
            Provider(
                "moralis_net_worth",
                ProviderTier.FREEMIUM,
                _g["_moralis_wallet_net_worth"],
                weight=3.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["wallet_pnl"] = ProviderChain(
        data_type="wallet_pnl",
        description="Wallet value baseline: net worth (Moralis) → portfolio holdings (Arkham)",
        providers=[
            Provider(
                "moralis_net_worth",
                ProviderTier.FREEMIUM,
                _g["_moralis_wallet_net_worth"],
                weight=10.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
            Provider(
                "arkham_portfolio",
                ProviderTier.FREEMIUM,
                _g["_arkham_portfolio"],
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    # ── C4 — cross-chain entity resolution (LOCAL tier, ClickHouse-only) ──
    chains["cross_chain_entity"] = ProviderChain(
        data_type="cross_chain_entity",
        description="Cross-chain entity resolution — same-deployer and same-funder links from ClickHouse deployer_events + evm_transfers (C4 v1, deploy tx / funder tx evidence)",
        providers=[
            Provider(
                "lite_chain_indexer_cross_chain_entity",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_cross_chain_entity"],
                weight=20.0,
                is_local=True,
                description="ClickHouse deployer_events + evm_transfers — link a wallet to addresses on other chains with deployer/funder tx evidence (C4)",
            ),
        ],
    )

    return chains
