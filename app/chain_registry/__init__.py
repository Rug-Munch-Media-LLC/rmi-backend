"""
SENTINEL - Multi-Chain Registry & Configuration
================================================
Centralized chain definitions, RPC endpoints, explorer APIs,
and data source configurations for all supported chains.

This package was split from the original chain_registry.py monolith.
See the individual modules for chain-specific configs.
"""

# Import constants and types first (no circular deps)
# Import chain-specific modules to trigger register() calls
from app.chain_registry import (
    evm,
    solana,
    tron,
)
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
    "evm",
    "solana",
    "tron",
]
