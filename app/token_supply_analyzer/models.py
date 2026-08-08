"""
Token Supply Analyzer — Data models, enums, and constants.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Known burn addresses
KNOWN_BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x000000000000000000000000000000000000dEaD",
    "dead00000000000000000000000000000000000000000000000000000000000000",
    "0x0000000000000000000000000000000000000001",
}

MAX_HOLDER_SAMPLE = 50
CONCENTRATION_THRESHOLD_PCT = 90.0
SUPPLY_RISK_RATIO = 0.3

# URL safety regex
_URL_SAFE = re.compile(
    r"^https?://[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*(?::\d{1,5})?(?:/[^\s\"<>]*)?$"
)


class SupplyRisk(Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LockStatus(Enum):
    LOCKED = "locked"
    PARTIALLY_LOCKED = "partially_locked"
    UNLOCKED = "unlocked"
    REMOVED = "removed"
    UNKNOWN = "unknown"


@dataclass
class TokenSupplyProfile:
    """Complete supply and liquidity profile for a token."""

    address: str
    chain: str = ""
    symbol: str = ""
    name: str = ""
    decimals: int = 18

    total_supply_raw: int = 0
    circulating_supply_raw: int = 0
    max_supply_raw: int = 0
    total_supply: float = 0.0
    circulating_supply: float = 0.0
    max_supply: float = 0.0
    burned_supply: float = 0.0

    has_mint_function: bool = False
    mint_function_type: str = "none"
    mint_authority: str = ""
    has_burn_function: bool = False
    burn_function_type: str = "none"
    is_deflationary: bool = False

    top_10_holder_pct: float = 0.0
    top_50_holder_pct: float = 0.0
    deployer_hold_pct: float = 0.0
    holder_count: int = 0
    whale_holdings: list[dict[str, Any]] = field(default_factory=list)

    lock_status: LockStatus = LockStatus.UNKNOWN
    liquidity_lock_address: str = ""
    liquidity_lock_expiry: str = ""
    lp_token_pct_held_by_deployer: float = 0.0
    pair_age_days: float = 0.0
    pair_liquidity_usd: float = 0.0

    buy_tax_pct: float = 0.0
    sell_tax_pct: float = 0.0
    has_dynamic_tax: bool = False
    has_blacklist: bool = False
    has_max_wallet_limit: bool = False

    is_renounced: bool = False
    owner_address: str = ""
    has_proxy_admin: bool = False
    has_multisig: bool = False
    has_timelock: bool = False

    supply_risk: SupplyRisk = SupplyRisk.SAFE
    supply_risk_score: float = 0.0
    concentration_risk: SupplyRisk = SupplyRisk.SAFE
    concentration_score: float = 0.0
    liquidity_risk: SupplyRisk = SupplyRisk.SAFE
    liquidity_score: float = 0.0
    overall_risk_score: float = 0.0
    overall_risk: SupplyRisk = SupplyRisk.SAFE

    patterns_detected: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


def _validate_url(url: str) -> bool:
    """Basic URL validation to prevent SSRF."""
    return bool(_URL_SAFE.match(url))


def _classify_risk(score: float) -> SupplyRisk:
    """Convert numeric score to risk level."""
    if score <= 10:
        return SupplyRisk.SAFE
    elif score <= 25:
        return SupplyRisk.LOW
    elif score <= 50:
        return SupplyRisk.MEDIUM
    elif score <= 75:
        return SupplyRisk.HIGH
    else:
        return SupplyRisk.CRITICAL


def _detect_chain(address: str) -> str:
    """Detect chain from address format."""
    addr = address.strip()
    if addr.startswith("0x"):
        return "ethereum"
    elif len(addr) >= 32 and not addr.startswith("0x"):
        return "solana"
    return "unknown"


def _normalize_address(address: str) -> str:
    """Normalize address to lowercase for comparison."""
    return address.lower().strip()


__all__ = [
    "CONCENTRATION_THRESHOLD_PCT",
    "KNOWN_BURN_ADDRESSES",
    "MAX_HOLDER_SAMPLE",
    "SUPPLY_RISK_RATIO",
    "LockStatus",
    "SupplyRisk",
    "TokenSupplyProfile",
    "_classify_risk",
    "_detect_chain",
    "_normalize_address",
    "_validate_url",
]
