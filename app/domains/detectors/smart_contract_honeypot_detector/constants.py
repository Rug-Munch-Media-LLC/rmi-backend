"""Constants and thresholds for the honeypot detector."""
from __future__ import annotations

from typing import Any

from .models import HoneypotType

KNOWN_MALICIOUS_SELECTORS: dict[str, tuple[str, HoneypotType, str]] = {
    "0x24b6d0c4": ("blacklist(address)", HoneypotType.BLACKLIST, "Owner can blacklist addresses from trading"),
    "0x9b3b0a8d": ("addBlackList(address)", HoneypotType.BLACKLIST, "Owner can blacklist addresses"),
    "0x3d0de014": ("removeBlackList(address)", HoneypotType.BLACKLIST, ""),
    "0x1f641f72": ("isBlackListed(address)", HoneypotType.BLACKLIST, "Blacklist lookup function"),
    "0x8da7ad2a": ("setCooldownEnabled(bool)", HoneypotType.COOLDOWN, "Owner can enable/disable trading cooldown"),
    "0xdd0655fc": ("setTradingCooldown(uint256)", HoneypotType.COOLDOWN, "Owner can set trading cooldown duration"),
    "0x4261f4d8": ("setMaxTxPercent(uint256)", HoneypotType.MAX_TX, "Owner can change max transaction amount"),
    "0x4a3b3a27": ("setMaxWalletPercent(uint256)", HoneypotType.MAX_WALLET, "Owner can change max wallet holding"),
    "0x8f0f4b3a": ("setBuyFee(uint256)", HoneypotType.BUY_TAX, "Owner can change buy fee"),
    "0xf0b9cf38": ("setSellFee(uint256)", HoneypotType.HIGH_SELL_TAX, "Owner can change sell fee"),
    "0x8456cb59": ("pause()", HoneypotType.TRADING_PAUSE, "Owner can pause all trading"),
    "0x3f4ba83a": ("unpause()", HoneypotType.TRADING_PAUSE, "Owner can unpause trading"),
    "0xbfb231d2": ("setPaused(bool)", HoneypotType.TRADING_PAUSE, "Owner can pause/unpause trading"),
    "0x40c10f19": ("mint(address,uint256)", HoneypotType.OWNER_MINT, "Owner can mint tokens to any address"),
    "0x9dc29fac": ("burn(address,uint256)", HoneypotType.OWNER_BURN, "Owner can burn tokens from any address"),
    "0x79cc6790": ("burnFrom(address,uint256)", HoneypotType.OWNER_BURN, "Owner can burn from any address"),
    "0x42966c68": ("burn(uint256)", HoneypotType.UNKNOWN, "Token burn function"),
    "0x40c10f18": ("mintTo(address,uint256)", HoneypotType.OWNER_MINT, ""),
    "0x2e1a7d4d": ("withdraw(uint256)", HoneypotType.LIQUIDITY_DRAIN, "Owner can withdraw ETH/LP from contract"),
    "0xe1f21c67": ("removeLiquidity(uint256,uint256)", HoneypotType.LIQUIDITY_DRAIN, ""),
    "0x02751cec": ("skim(address)", HoneypotType.UNKNOWN, ""),
    "0xbc25cf77": ("sync()", HoneypotType.UNKNOWN, "Sync reserves - can manipulate price"),
    "0x41c0e1b5": ("selfdestruct()", HoneypotType.SELFDESTRUCT, "Contract can selfdestruct - permanent loss"),
    "0x00f714ce": ("kill()", HoneypotType.SELFDESTRUCT, "Kill function - can destroy contract"),
    "0x3659cfe6": ("upgradeTo(address)", HoneypotType.PROXY_UPGRADE, "Proxy upgrade to new implementation"),
    "0xf2fde38b": ("renounceOwnership()", HoneypotType.UNKNOWN, "Ownership renouncement function"),
}

BENIGN_SELECTORS: set[str] = {
    "0x18160ddd", "0x70a08231", "0x313ce567", "0x06fdde03", "0x95d89b41",
    "0xdd62ed3e", "0xa9059cbb", "0x23b872dd", "0x095ea7b3", "0x54fd4d50", "0x01ffc9a7",
}

EVM_CHAINS: dict[str, dict[str, Any]] = {
    "ethereum": {"rpc": "https://eth.llamarpc.com", "chain_id": 1, "explorer": "https://api.etherscan.io/api"},
    "bsc": {"rpc": "https://bsc-dataseed.binance.org", "chain_id": 56, "explorer": "https://api.bscscan.com/api"},
    "polygon": {"rpc": "https://polygon-rpc.com", "chain_id": 137, "explorer": "https://api.polygonscan.com/api"},
    "arbitrum": {"rpc": "https://arb1.arbitrum.io/rpc", "chain_id": 42161, "explorer": "https://api.arbiscan.io/api"},
    "optimism": {"rpc": "https://mainnet.optimism.io", "chain_id": 10, "explorer": "https://api-optimistic.etherscan.io/api"},
    "base": {"rpc": "https://mainnet.base.org", "chain_id": 8453, "explorer": "https://api.basescan.org/api"},
    "avalanche": {"rpc": "https://api.avax.network/ext/bc/C/rpc", "chain_id": 43114, "explorer": "https://api.snowtrace.io/api"},
}

HONEYPOT_THRESHOLDS = {
    "critical_sell_tax": 25.0, "high_sell_tax": 15.0, "medium_sell_tax": 10.0,
    "critical_buy_tax": 20.0, "high_buy_tax": 10.0,
    "critical_lp_unlocked": 100.0, "high_lp_unlocked": 75.0,
    "critical_supply_concentration": 90.0, "high_supply_concentration": 50.0,
    "low_liquidity_threshold": 1000, "tiny_liquidity_threshold": 100,
}
