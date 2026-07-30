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
# ruff: noqa: F401 — re-exports

from app.chain_registry.constants import (
    ChainFamily,
    ChainConfig,
    CHAINS,
    get_chain,
    get_chain_family,
    is_solana,
    is_evm,
    resolve_chain_id,
    list_chains,
    get_chain_display,
    get_explorer_url,
    get_rpc_urls,
    get_explorer_api_config,
    get_cex_wallets,
    get_known_lockers,
)

__all__ = [
    "ChainFamily",
    "ChainConfig",
    "CHAINS",
    "get_chain",
    "get_chain_family",
    "is_solana",
    "is_evm",
    "resolve_chain_id",
    "list_chains",
    "get_chain_display",
    "get_explorer_url",
    "get_rpc_urls",
    "get_explorer_api_config",
    "get_cex_wallets",
    "get_known_lockers",
]
