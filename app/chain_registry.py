"""
SENTINEL - Multi-Chain Registry & Configuration
================================================
Centralized chain definitions, RPC endpoints, explorer APIs,
and data source configurations for all 13 supported chains.

Chains: solana, ethereum, base, bsc, arbitrum, polygon, avalanche,
        optimism, fantom, linea, zksync, scroll, mantle

This module is now a re-export facade. The implementation has been
split into the ``chain_registry/`` package:

- ``chain_registry/constants.py`` — ChainFamily, ChainConfig, helpers
- ``chain_registry/solana.py`` — Solana chain config
- ``chain_registry/evm.py`` — EVM chain configs (12 chains)
- ``chain_registry/tron.py`` — TRON placeholder (future use)
"""


from app.chain_registry.constants import (
    CHAINS,
    ChainConfig,
    ChainFamily,
    get_cex_wallets,
    get_chain,
    get_chain_display,
    get_chain_family,
    get_explorer_api_config,
    get_explorer_url,
    get_known_lockers,
    get_rpc_urls,
    is_evm,
    is_solana,
    list_chains,
    resolve_chain_id,
)

__all__ = [
    "CHAINS",
    "ChainConfig",
    "ChainFamily",
    "get_cex_wallets",
    "get_chain",
    "get_chain_display",
    "get_chain_family",
    "get_explorer_api_config",
    "get_explorer_url",
    "get_known_lockers",
    "get_rpc_urls",
    "is_evm",
    "is_solana",
    "list_chains",
    "resolve_chain_id",
]
