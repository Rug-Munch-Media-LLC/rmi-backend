"""
Oracle Manipulation Detector — Constants and Configuration
===========================================================
Chain configurations, feed addresses, and detection thresholds.
"""

from __future__ import annotations

from typing import Any

# Chainlink-specific constants
CHAINLINK_FEED_UPDATE_THRESHOLD_SECONDS = 7200  # 2 hours - stale after this
CHAINLINK_DEVIATION_THRESHOLD = 0.02  # 2% - max expected deviation
CHAINLINK_HEARTBEAT_SECONDS = 3600  # 1 hour - expected update interval

# TWAP manipulation thresholds
TWAP_MANIPULATION_THRESHOLD_PCT = 0.05  # 5% TWAP deviation = suspicious
TWAP_POISON_WINDOW_BLOCKS = 20  # Look for TWAP poisoning in last N blocks
MIN_TWAP_SAMPLES = 3  # Minimum samples for TWAP calc

# Price divergence thresholds
CROSS_POOL_DIVERGENCE_THRESHOLD = 0.03  # 3% = suspicious (same asset)
CEX_DEX_DIVERGENCE_THRESHOLD = 0.05  # 5% = suspicious (DEX vs CEX)
LIQUIDITY_IMPACT_THRESHOLD = 0.10  # 10% price impact = risky swap

# Flash loan thresholds
MIN_FLASHLOAN_VALUE_USD = 100000  # $100k minimum for concern
FLASHLOAN_PRICE_IMPACT_THRESHOLD = 0.02  # 2% price movement via flash loan

# Supported chains and their RPC endpoints (public fallbacks)
SUPPORTED_CHAINS: dict[str, dict[str, Any]] = {
    "ethereum": {"rpc": "https://eth.llamarpc.com", "id": 1, "explorer": "etherscan.io"},
    "base": {"rpc": "https://base.llamarpc.com", "id": 8453, "explorer": "basescan.org"},
    "arbitrum": {"rpc": "https://arbitrum.llamarpc.com", "id": 42161, "explorer": "arbiscan.io"},
    "optimism": {
        "rpc": "https://optimism.llamarpc.com",
        "id": 10,
        "explorer": "optimistic.etherscan.io",
    },
    "polygon": {"rpc": "https://polygon.llamarpc.com", "id": 137, "explorer": "polygonscan.com"},
    "bsc": {"rpc": "https://bsc.llamarpc.com", "id": 56, "explorer": "bscscan.com"},
}

# Known Chainlink price feed addresses (Ethereum mainnet)
CHAINLINK_FEEDS: dict[str, dict[str, Any]] = {
    "ETH/USD": {
        "address": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
        "decimals": 8,
        "heartbeat": 3600,
        "deviation": 0.005,
    },
    "BTC/USD": {
        "address": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
        "decimals": 8,
        "heartbeat": 3600,
        "deviation": 0.005,
    },
    "USDC/USD": {
        "address": "0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6",
        "decimals": 8,
        "heartbeat": 86400,
        "deviation": 0.001,
    },
    "USDT/USD": {
        "address": "0x3E7d1eAB13ad0104d2750B8863b489D65364e32D",
        "decimals": 8,
        "heartbeat": 86400,
        "deviation": 0.001,
    },
    "DAI/USD": {
        "address": "0xAed0c38402a5d19df6E4c03F4E2DceD6e29c1ee9",
        "decimals": 8,
        "heartbeat": 86400,
        "deviation": 0.01,
    },
    "WBTC/BTC": {
        "address": "0xfdFD9C85aD200c506Cf9e21F1FD8dd01932FBB23",
        "decimals": 8,
        "heartbeat": 3600,
        "deviation": 0.005,
    },
    "LINK/USD": {
        "address": "0x2c1d072e956AFFC0D435Cb7AC38EF18d24d9127c",
        "decimals": 8,
        "heartbeat": 3600,
        "deviation": 0.02,
    },
    "MATIC/USD": {
        "address": "0x7bAC85A8a13A4BcD8abb3eB7d6b4d632c5a57676",
        "decimals": 8,
        "heartbeat": 3600,
        "deviation": 0.02,
    },
    "SOL/USD": {
        "address": "0x4ffC43a60e009B551865A93d232E33Fce9f01507",
        "decimals": 8,
        "heartbeat": 3600,
        "deviation": 0.02,
    },
    "AAVE/USD": {
        "address": "0x547a514d5e3769680Ce22B2361c10Ea13619e8a9",
        "decimals": 8,
        "heartbeat": 3600,
        "deviation": 0.02,
    },
    "UNI/USD": {
        "address": "0x553303d460EE0afB37EdFf9bE42922D8FF63220e",
        "decimals": 8,
        "heartbeat": 3600,
        "deviation": 0.02,
    },
    "CRV/USD": {
        "address": "0xCd627aA160A6fA45Eb793D19Ef54f5062F20f33f",
        "decimals": 8,
        "heartbeat": 3600,
        "deviation": 0.02,
    },
}

# Known major DEX pool addresses for cross-reference (Ethereum)
KNOWN_LP_POOLS: dict[str, list[dict[str, Any]]] = {
    "WETH/USDC": [
        {
            "protocol": "Uniswap V3",
            "fee": 500,
            "address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        },
        {
            "protocol": "Uniswap V3",
            "fee": 3000,
            "address": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
        },
        {"protocol": "Uniswap V2", "address": "0xb4e16d0168e52d35cacd2c6185b44281ec28c9dc"},
        {"protocol": "Curve", "address": "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7"},
        {"protocol": "Balancer", "address": "0xBA12222222228d8Ba445958a75a0704d566BF2C8"},
    ],
    "WETH/USDT": [
        {
            "protocol": "Uniswap V3",
            "fee": 500,
            "address": "0x11b815efb8f581194ae79006d24e0d814b7697f6",
        },
        {
            "protocol": "Uniswap V3",
            "fee": 3000,
            "address": "0xcbcdf9626bc03e24f779434178a73a0b4bad62ed",
        },
    ],
    "WBTC/WETH": [
        {
            "protocol": "Uniswap V3",
            "fee": 500,
            "address": "0x4585fe77225b41b697c938b018e2ace67d0be5e2",
        },
        {
            "protocol": "Uniswap V3",
            "fee": 3000,
            "address": "0xcbcdf9626bc03e24f779434178a73a0b4bad62ed",
        },
    ],
}

__all__ = [
    "CEX_DEX_DIVERGENCE_THRESHOLD",
    "CHAINLINK_DEVIATION_THRESHOLD",
    "CHAINLINK_FEEDS",
    "CHAINLINK_FEED_UPDATE_THRESHOLD_SECONDS",
    "CHAINLINK_HEARTBEAT_SECONDS",
    "CROSS_POOL_DIVERGENCE_THRESHOLD",
    "FLASHLOAN_PRICE_IMPACT_THRESHOLD",
    "KNOWN_LP_POOLS",
    "LIQUIDITY_IMPACT_THRESHOLD",
    "MIN_FLASHLOAN_VALUE_USD",
    "MIN_TWAP_SAMPLES",
    "SUPPORTED_CHAINS",
    "TWAP_MANIPULATION_THRESHOLD_PCT",
    "TWAP_POISON_WINDOW_BLOCKS",
]
