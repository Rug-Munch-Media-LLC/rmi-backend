"""
Rug Imminence Predictor
=======================
Predicts imminent rug pulls by analyzing deployer behavior, liquidity
changes, holder concentration, and social sentiment signals.

Backward-compatibility shim: this module now lives in ``app.domains.detectors``.

Usage:
    from app.rug_imminence_predictor import RugImminencePredictor
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
