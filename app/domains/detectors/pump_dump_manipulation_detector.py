"""
Pump & Dump / Coordinated Market Manipulation Detector
======================================================
This file has been modularized into app/domains/detectors/pump_dump_detector_package/ package.
This stub re-exports everything for backward compatibility.
"""

from __future__ import annotations

from app.domains.detectors.pump_dump_detector_package import *

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
