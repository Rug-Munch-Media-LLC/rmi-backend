"""
Oracle Manipulation Detector
=============================
Detection and analysis of price oracle manipulation attacks across DeFi
protocols, including flash loan-assisted attacks and TWAP manipulation.

Split from oracle_manipulation_detector.py into: constants, models, utils, detector, cli.
"""

from __future__ import annotations
from .cli import main
from .constants import (
    CEX_DEX_DIVERGENCE_THRESHOLD,
    CHAINLINK_DEVIATION_THRESHOLD,
    CHAINLINK_FEED_UPDATE_THRESHOLD_SECONDS,
    CHAINLINK_FEEDS,
    CHAINLINK_HEARTBEAT_SECONDS,
    CROSS_POOL_DIVERGENCE_THRESHOLD,
    FLASHLOAN_PRICE_IMPACT_THRESHOLD,
    KNOWN_LP_POOLS,
    LIQUIDITY_IMPACT_THRESHOLD,
    MIN_FLASHLOAN_VALUE_USD,
    MIN_TWAP_SAMPLES,
    SUPPORTED_CHAINS,
    TWAP_MANIPULATION_THRESHOLD_PCT,
    TWAP_POISON_WINDOW_BLOCKS,
)
from .detector import OracleManipulationDetector
from .models import (
    ManipulationReport,
    ManipulationType,
    OracleRead,
    OracleType,
    PriceManipulation,
    PriceSnapshot,
    Severity,
    TWAPWindow,
)
from .utils import (
    calculate_twap_from_samples,
    classify_severity,
    compute_deviation_pct,
    detect_twap_manipulation,
    is_sandwich_pattern,
)

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
    "ManipulationReport",
    "ManipulationType",
    "OracleManipulationDetector",
    "OracleRead",
    "OracleType",
    "PriceManipulation",
    "PriceSnapshot",
    "Severity",
    "TWAPWindow",
    "calculate_twap_from_samples",
    "classify_severity",
    "compute_deviation_pct",
    "detect_twap_manipulation",
    "is_sandwich_pattern",
    "main",
]
