"""
Oracle Manipulation Detector
=============================
Detection and analysis of price oracle manipulation attacks across DeFi
protocols, including flash loan-assisted attacks and TWAP manipulation.

Split from oracle_manipulation_detector.py into: constants, models, utils, detector, cli.
"""

from .constants import (
    CHAINLINK_FEED_UPDATE_THRESHOLD_SECONDS,
    CHAINLINK_DEVIATION_THRESHOLD,
    CHAINLINK_HEARTBEAT_SECONDS,
    TWAP_MANIPULATION_THRESHOLD_PCT,
    TWAP_POISON_WINDOW_BLOCKS,
    MIN_TWAP_SAMPLES,
    CROSS_POOL_DIVERGENCE_THRESHOLD,
    CEX_DEX_DIVERGENCE_THRESHOLD,
    LIQUIDITY_IMPACT_THRESHOLD,
    MIN_FLASHLOAN_VALUE_USD,
    FLASHLOAN_PRICE_IMPACT_THRESHOLD,
    SUPPORTED_CHAINS,
    CHAINLINK_FEEDS,
    KNOWN_LP_POOLS,
)
from .models import (
    OracleType,
    ManipulationType,
    Severity,
    PriceSnapshot,
    TWAPWindow,
    OracleRead,
    PriceManipulation,
    ManipulationReport,
)
from .utils import (
    detect_twap_manipulation,
    compute_deviation_pct,
    classify_severity,
    is_sandwich_pattern,
    calculate_twap_from_samples,
)
from .detector import OracleManipulationDetector
from .cli import main

__all__ = [
    "CHAINLINK_FEED_UPDATE_THRESHOLD_SECONDS",
    "CHAINLINK_DEVIATION_THRESHOLD",
    "CHAINLINK_HEARTBEAT_SECONDS",
    "TWAP_MANIPULATION_THRESHOLD_PCT",
    "TWAP_POISON_WINDOW_BLOCKS",
    "MIN_TWAP_SAMPLES",
    "CROSS_POOL_DIVERGENCE_THRESHOLD",
    "CEX_DEX_DIVERGENCE_THRESHOLD",
    "LIQUIDITY_IMPACT_THRESHOLD",
    "MIN_FLASHLOAN_VALUE_USD",
    "FLASHLOAN_PRICE_IMPACT_THRESHOLD",
    "SUPPORTED_CHAINS",
    "CHAINLINK_FEEDS",
    "KNOWN_LP_POOLS",
    "OracleType",
    "ManipulationType",
    "Severity",
    "PriceSnapshot",
    "TWAPWindow",
    "OracleRead",
    "PriceManipulation",
    "ManipulationReport",
    "OracleManipulationDetector",
    "detect_twap_manipulation",
    "compute_deviation_pct",
    "classify_severity",
    "is_sandwich_pattern",
    "calculate_twap_from_samples",
    "main",
]
