"""
Pump & Dump / Coordinated Market Manipulation Detector — modular package.
Re-exports all public symbols for backward compatibility.
"""

from app.domains.detectors.pump_dump_detector_package.detector import (
    PumpDumpDetector,
    analyze_token,
    analyze_token_sync,
)
from app.domains.detectors.pump_dump_detector_package.signals_accum import (
    calculate_risk_score,
    estimate_traders,
    score_to_level,
)
from app.domains.detectors.pump_dump_detector_package.types import (
    PUMP_DUMP_THRESHOLDS,
    CoordinatedBuyGroup,
    FindingSeverity,
    ManipulationFinding,
    ManipulationType,
    PrePumpAccumulation,
    PricePumpSignal,
    PumpDumpAnalysisResult,
    VolumeAnomaly,
    WashTradeCluster,
)

__all__ = [
    "PUMP_DUMP_THRESHOLDS",
    "CoordinatedBuyGroup",
    "FindingSeverity",
    "ManipulationFinding",
    "ManipulationType",
    "PrePumpAccumulation",
    "PricePumpSignal",
    "PumpDumpAnalysisResult",
    "PumpDumpDetector",
    "VolumeAnomaly",
    "WashTradeCluster",
    "analyze_token",
    "analyze_token_sync",
    "calculate_risk_score",
    "estimate_traders",
    "score_to_level",
]
