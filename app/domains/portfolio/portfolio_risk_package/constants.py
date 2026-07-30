"""
Portfolio Risk — Constants
Chain configurations, ERC20 ABI, address validation, and defaults.
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_TIMEOUT = 15
MAX_TOKENS_PER_WALLET = 100
RISK_WEIGHT_TOKENS = 0.6
RISK_WEIGHT_CONCENTRATION = 0.25
RISK_WEIGHT_CHAIN_DIVERSITY = 0.15

ERC20_ABI = json.loads("""
[
    {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]
""")


def validate_wallet_address(address: str) -> tuple[bool, str]:
    from app.core.shared_utils import validate_wallet_address as _check

    return _check(address)


CHAIN_CONFIGS: dict[str, dict[str, Any]] = {
    "ethereum": {
        "rpc": "https://ethereum-rpc.publicnode.com",
        "chain_id": 1,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://etherscan.io",
        "type": "evm",
    },
    "base": {
        "rpc": "https://base-rpc.publicnode.com",
        "chain_id": 8453,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://basescan.org",
        "type": "evm",
    },
    "arbitrum": {
        "rpc": "https://arbitrum-rpc.publicnode.com",
        "chain_id": 42161,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://arbiscan.io",
        "type": "evm",
    },
    "optimism": {
        "rpc": "https://optimism-rpc.publicnode.com",
        "chain_id": 10,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://optimistic.etherscan.io",
        "type": "evm",
    },
    "polygon": {
        "rpc": "https://polygon-rpc.publicnode.com",
        "chain_id": 137,
        "native_symbol": "MATIC",
        "decimals": 18,
        "explorer": "https://polygonscan.com",
        "type": "evm",
        "poa": True,
    },
    "bsc": {
        "rpc": "https://bsc-rpc.publicnode.com",
        "chain_id": 56,
        "native_symbol": "BNB",
        "decimals": 18,
        "explorer": "https://bscscan.com",
        "type": "evm",
        "poa": True,
    },
    "avalanche": {
        "rpc": "https://avalanche-rpc.publicnode.com",
        "chain_id": 43114,
        "native_symbol": "AVAX",
        "decimals": 18,
        "explorer": "https://snowtrace.io",
        "type": "evm",
        "poa": True,
    },
    "linea": {
        "rpc": "https://linea-rpc.publicnode.com",
        "chain_id": 59144,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://lineascan.build",
        "type": "evm",
    },
    "zksync": {
        "rpc": "https://zksync-rpc.publicnode.com",
        "chain_id": 324,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://explorer.zksync.io",
        "type": "evm",
    },
    "scroll": {
        "rpc": "https://scroll-rpc.publicnode.com",
        "chain_id": 534352,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://scrollscan.com",
        "type": "evm",
    },
    "mantle": {
        "rpc": "https://mantle-rpc.publicnode.com",
        "chain_id": 5000,
        "native_symbol": "MNT",
        "decimals": 18,
        "explorer": "https://mantlescan.xyz",
        "type": "evm",
    },
    "fantom": {
        "rpc": "https://fantom-rpc.publicnode.com",
        "chain_id": 250,
        "native_symbol": "FTM",
        "decimals": 18,
        "explorer": "https://ftmscan.com",
        "type": "evm",
        "poa": True,
    },
    "solana": {
        "rpc": "https://solana-rpc.publicnode.com",
        "chain_id": "solana",
        "native_symbol": "SOL",
        "decimals": 9,
        "explorer": "https://solscan.io",
        "type": "solana",
    },
}

COMMON_TOKENS: dict[str, dict[str, str]] = {
    "ethereum": {
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
        "LINK": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
        "UNI": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "PEPE": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        "SHIB": "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",
    },
    "base": {
        "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "AERO": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
    },
    "bsc": {
        "USDT": "0x55d398326f99059fF775485246999027B3197955",
        "CAKE": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
        "BNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    },
}
