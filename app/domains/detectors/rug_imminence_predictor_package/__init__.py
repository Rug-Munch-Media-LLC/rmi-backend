"""
Rug Pull Imminence Predictor (RIP)
=====================================
AI-powered early warning system that predicts imminent rug pulls BEFORE they happen.

⚠️ DEPRECATED SHIM — import directly from app.domains.detectors.rug_imminence_predictor instead
This shim exists for backward compatibility and will be removed in a future release.

Usage:
    from app.domains.detectors.rug_imminence_predictor import RugImminencePredictor
    predictor = RugImminencePredictor()
    result = await predictor.predict("0x1234...", chain="base")
"""

from __future__ import annotations

from app.domains.detectors.rug_imminence_predictor import (
    DEFAULT_WEIGHTS,
    SCORE_THRESHOLDS,
    SIGNAL_NAMES,
    ImminenceLevel,
    RIPResult,
    RugImminencePredictor,
    SignalCategory,
    _get_known_lp_lockers,
    get_predictor,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "SCORE_THRESHOLDS",
    "SIGNAL_NAMES",
    "ImminenceLevel",
    "RIPResult",
    "RugImminencePredictor",
    "SignalCategory",
    "_get_known_lp_lockers",
    "get_predictor",
]
