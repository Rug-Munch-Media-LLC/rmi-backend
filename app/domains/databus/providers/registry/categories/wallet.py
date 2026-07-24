"""
Wallet provider chains.
"""

from __future__ import annotations
from ...base import Provider, ProviderChain, ProviderTier

def build_wallet_chains(locals_dict: dict) -> dict[str, ProviderChain]:
    """Build wallet provider chains."""
    chains: dict[str, ProviderChain] = {}
    _g = locals_dict

    chains["wallet_labels"] = ProviderChain(
        data_type="wallet_labels",
        description="Address labels and entity identification (190K local + external)",
        providers=[
            Provider(
                "wallet_memory_bank", ProviderTier.LOCAL, _g["_local_wallet_labels"], weight=10.0
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
        description="Token metadata lookup (DexScreener free → Alchemy freemium)",
        providers=[
            Provider(
                "dexscreener_meta",
                ProviderTier.FREE_API,
                _g["_dexscreener_token_metadata"],
                weight=10.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "alchemy_metadata",
                ProviderTier.FREEMIUM,
                _g["_alchemy_token_metadata"],
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
        description="Wallet transaction history (Etherscan free → Moralis freemium)",
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

    return chains
