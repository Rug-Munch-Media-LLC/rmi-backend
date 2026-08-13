"""
Pump & Dump Manipulation Detector
==================================
Detects pump-and-dump schemes by analyzing trading patterns, social signals,
and wallet coordination across all supported chains.

Backward-compatibility shim: this module now lives in ``app.domains.detectors``.

Usage:
    from app.pump_dump_manipulation_detector import PumpDumpDetector

CLI:
    python3 -m app.pump_dump_manipulation_detector 0x... --chain ethereum
    python3 -m app.pump_dump_manipulation_detector 0x... --chain solana --alert
"""

from __future__ import annotations

from app.domains.detectors.pump_dump_manipulation_detector import (
    PUMP_DUMP_THRESHOLDS,
    CoordinatedBuyGroup,
    FindingSeverity,
    ManipulationFinding,
    ManipulationType,
    PrePumpAccumulation,
    PricePumpSignal,
    PumpDumpAnalysisResult,
    PumpDumpDetector,
    VolumeAnomaly,
    WashTradeCluster,
    analyze_token,
    analyze_token_sync,
    calculate_risk_score,
    estimate_traders,
    score_to_level,
)

if __name__ == "__main__":
    # The domain package exposes no importable `main`; executing this re-export
    # shim directly is a no-op (previously raised ImportError).
    pass


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
