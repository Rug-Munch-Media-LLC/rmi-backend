"""
Token Supply & Liquidity Analyzer
==================================
Comprehensive analysis of a token's supply mechanics, liquidity locks,
holder concentration, mint/burn capabilities, and hidden tax mechanisms.

Split from monolithic token_supply_analyzer.py into:
  - models.py     Enums, dataclasses, constants
  - analyzers.py  Supply/concentration/liquidity/risk analysis functions
  - fetchers.py   API fetch helpers + data parsing
  - main.py       Main analysis entry point + batch + CLI
"""

from app.token_supply_analyzer.models import (
    SupplyRisk,
    LockStatus,
    TokenSupplyProfile,
    KNOWN_BURN_ADDRESSES,
    _validate_url,
    _classify_risk,
    _detect_chain,
    _normalize_address,
)
from app.token_supply_analyzer.analyzers import (
    _analyze_supply_mechanics,
    _analyze_concentration,
    _analyze_liquidity,
    _compute_overall_risk,
)
from app.token_supply_analyzer.fetchers import (
    _fetch_dexscreener,
    _fetch_birdeye,
    _fetch_json,
    _compute_pair_age_days,
    _rate_limit_host,
)
from app.token_supply_analyzer.main import analyze_token_supply, analyze_multiple_tokens, main

__all__ = [
    "SupplyRisk",
    "LockStatus",
    "TokenSupplyProfile",
    "KNOWN_BURN_ADDRESSES",
    "_validate_url",
    "_classify_risk",
    "_detect_chain",
    "_normalize_address",
    "_analyze_supply_mechanics",
    "_analyze_concentration",
    "_analyze_liquidity",
    "_compute_overall_risk",
    "_fetch_dexscreener",
    "_fetch_birdeye",
    "_fetch_json",
    "_compute_pair_age_days",
    "_rate_limit_host",
    "analyze_token_supply",
    "analyze_multiple_tokens",
    "main",
]
