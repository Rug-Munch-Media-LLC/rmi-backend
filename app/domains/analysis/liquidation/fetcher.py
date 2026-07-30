"""
Liquidation Cascade — On-Chain Data Fetching
==============================================
Constants (chain configs, protocol configs, LTV ranges) and data-fetch helpers.
"""

from __future__ import annotations

import re
from typing import Any

# Supported chains and their RPC endpoints (public fallbacks)
SUPPORTED_CHAINS = {
    "ethereum": {"rpc": "https://eth.llamarpc.com", "id": 1, "explorer": "etherscan.io"},
    "base": {"rpc": "https://base.llamarpc.com", "id": 8453, "explorer": "basescan.org"},
    "arbitrum": {"rpc": "https://arbitrum.llamarpc.com", "id": 42161, "explorer": "arbiscan.io"},
    "optimism": {"rpc": "https://optimism.llamarpc.com", "id": 10, "explorer": "optimistic.etherscan.io"},
    "polygon": {"rpc": "https://polygon.llamarpc.com", "id": 137, "explorer": "polygonscan.com"},
    "bsc": {"rpc": "https://bsc.llamarpc.com", "id": 56, "explorer": "bscscan.com"},
}

PROTOCOL_CONFIGS = {
    "aave_v3": {
        "name": "Aave V3",
        "pools": {
            "ethereum": "0x87870Bca3F3fD5675F3E9Ca6CBaC1aE6bF7E8f4c",
            "base": "0xA238Dd80C259a72e81d7e4664a209aEfF1B8F1eA",
            "arbitrum": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
            "optimism": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
            "polygon": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        },
        "type": "overcollateralized",
    },
    "aave_v2": {
        "name": "Aave V2",
        "pools": {
            "ethereum": "0x7d2768dE32b0b80b7a3454c06BdAc94A69DDc7A9",
            "polygon": "0x8dFf5E27EA6b7AC08EbFdf9eb090F32ee9a30fcf",
        },
        "type": "overcollateralized",
    },
    "compound_v3": {
        "name": "Compound III",
        "pools": {
            "ethereum": "0xc3d688B66703497DAA19211EEdFF47f25384cdc3",
            "base": "0x46e6b214b5243e12C12B3e7C8e9B5D7f8D4c8F1e",
            "arbitrum": "0xA5E6D3C9B9E5E5B5b5B5b5b5b5b5b5b5b5b5b5b5",
        },
        "type": "overcollateralized",
    },
    "radiant_v2": {
        "name": "Radiant V2",
        "pools": {
            "arbitrum": "0xF4B1486DD74D7D5F49aF9F6eD4cA1C8bE3d9E4b2",
            "base": "0xF4B1486DD74D7D5F49aF9F6eD4cA1C8bE3d9E4b2",
            "bsc": "0xd50Cf00b6e600Dd036Ba8eF475677d816d6c4281",
        },
        "type": "overcollateralized",
    },
}

LTV_RANGES = {
    "stablecoin": 0.85,
    "eth": 0.80,
    "wsteth": 0.75,
    "major_asset": 0.75,
    "mid_asset": 0.65,
    "risky_asset": 0.50,
}


def _try_import_web3() -> Any | None:
    """Try to import web3, return None if not available."""
    try:
        import web3  # type: ignore[import-untyped]

        return web3
    except ImportError:
        return None


def _estimate_asset_ltv(asset_symbol: str) -> tuple[float, float]:
    """Estimate LTV and liquidation threshold for a given asset type."""
    sym = asset_symbol.lower()
    if sym in ("usdc", "usdt", "dai", "frax", "lusd", "crvusd"):
        return 0.80, 0.85
    elif sym in ("weth", "eth"):
        return 0.80, 0.83
    elif sym in ("wsteth", "steth", "reth", "sfrxeth"):
        return 0.75, 0.79
    elif sym in ("wbtc", "cbeth"):
        return 0.75, 0.78
    elif sym in ("wmatic", "matic", "wbnb", "bnb"):
        return 0.65, 0.70
    elif sym in ("aave", "uni", "link", "op", "arb"):
        return 0.60, 0.65
    else:
        return 0.50, 0.55


def _estimate_asset_price(asset_symbol: str) -> float:
    """Return a rough price estimate for common assets."""
    prices = {
        "eth": 2800.0,
        "weth": 2800.0,
        "steth": 2740.0,
        "wsteth": 3200.0,
        "reth": 2850.0,
        "sfrxeth": 2950.0,
        "wbtc": 68000.0,
        "cbeth": 2750.0,
        "usdc": 1.0,
        "usdt": 1.0,
        "dai": 1.0,
        "frax": 1.0,
        "lusd": 1.0,
        "crvusd": 1.0,
        "wmatic": 0.50,
        "matic": 0.50,
        "wbnb": 580.0,
        "bnb": 580.0,
        "aave": 140.0,
        "uni": 7.5,
        "link": 14.0,
        "op": 2.0,
        "arb": 0.80,
    }
    return prices.get(asset_symbol.lower(), 1.0)


def _resolve_asset_symbol(address: str, chain: str) -> str:
    """Map common token addresses to symbols."""
    address_map = {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
        "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
        "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
        "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": "wstETH",
        "0xae78736cd615f374d3085123a210448e74fc6393": "rETH",
        "0x514910771af9ca656af840dff83e8264ecf986ca": "LINK",
        "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": "UNI",
        "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": "AAVE",
        "0x4200000000000000000000000000000000000006": "WETH",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": "DAI",
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": "WETH",
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": "USDC",
        "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": "WBTC",
        "0x7f5c764cbc14f9669b88837ca1490cca17c31607": "USDC",
        "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": "WETH",
        "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC",
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "USDT",
        "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": "DAI",
        "0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6": "WBTC",
        "0x2170ed0880ac9a755fd29b2688956bd959f933f8": "WETH",
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",
        "0x55d398326f99059ff775485246999027b3197955": "USDT",
        "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": "WBTC",
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",
    }
    addr = address.lower()
    if addr in address_map:
        return address_map[addr]
    for known_addr, sym in address_map.items():
        if known_addr.lower() == addr:
            return sym
    return f"TOKEN({addr[:10]}...)"


def _validate_address(address: str) -> bool:
    """Validate an Ethereum or Solana address."""
    return bool(re.match(r"^0x[a-fA-F0-9]{40}$", address) or re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address))
