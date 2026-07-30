"""
SENTINEL - Multi-Chain Registry & Configuration
================================================
Centralized chain definitions, RPC endpoints, explorer APIs,
and data source configurations for all supported chains.

This package was split from the original chain_registry.py monolith.
See the individual modules for chain-specific configs.
"""

# Import constants and types first (no circular deps)
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

# Import chain-specific modules to trigger register() calls
from app.chain_registry import evm  # noqa: F401  (triggers register)
from app.chain_registry import solana  # noqa: F401  (triggers register)
from app.chain_registry import tron  # noqa: F401  (triggers register)

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
