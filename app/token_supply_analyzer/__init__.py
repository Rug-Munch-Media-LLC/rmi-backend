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

from app.token_supply_analyzer.analyzers import (
    _analyze_concentration,
    _analyze_liquidity,
    _analyze_supply_mechanics,
    _compute_overall_risk,
)
from app.token_supply_analyzer.fetchers import (
    _compute_pair_age_days,
    _fetch_birdeye,
    _fetch_dexscreener,
    _fetch_json,
    _rate_limit_host,
)
from app.token_supply_analyzer.main import analyze_multiple_tokens, analyze_token_supply, main
from app.token_supply_analyzer.models import (
    KNOWN_BURN_ADDRESSES,
    LockStatus,
    SupplyRisk,
    TokenSupplyProfile,
    _classify_risk,
    _detect_chain,
    _normalize_address,
    _validate_url,
)

__all__ = [
    "KNOWN_BURN_ADDRESSES",
    "LockStatus",
    "SupplyRisk",
    "TokenSupplyProfile",
    "_analyze_concentration",
    "_analyze_liquidity",
    "_analyze_supply_mechanics",
    "_classify_risk",
    "_compute_overall_risk",
    "_compute_pair_age_days",
    "_detect_chain",
    "_fetch_birdeye",
    "_fetch_dexscreener",
    "_fetch_json",
    "_normalize_address",
    "_rate_limit_host",
    "_validate_url",
    "analyze_multiple_tokens",
    "analyze_token_supply",
    "main",
]
