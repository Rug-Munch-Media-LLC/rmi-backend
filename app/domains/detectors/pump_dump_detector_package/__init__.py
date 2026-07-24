"""
Pump & Dump / Coordinated Market Manipulation Detector — modular package.
Re-exports all public symbols for backward compatibility.
"""
from app.domains.detectors.pump_dump_detector_package.detector import (
    PumpDumpDetector,
    analyze_token,
    analyze_token_sync,
)
from app.domains.detectors.pump_dump_detector_package.types import (
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
]
