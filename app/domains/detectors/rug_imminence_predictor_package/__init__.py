"""
Rug Pull Imminence Predictor (RIP)
=====================================
AI-powered early warning system that predicts imminent rug pulls BEFORE they happen.

Usage:
    from app.domains.detectors.rug_imminence_predictor import RugImminencePredictor
    predictor = RugImminencePredictor()
    result = await predictor.predict("0x1234...", chain="base")
"""

from __future__ import annotations

from app.domains.detectors.rug_imminence_predictor_package.predictor import RugImminencePredictor
from app.domains.detectors.rug_imminence_predictor_package.types import (
    ImminenceLevel,
    RIPResult,
    SignalCategory,
)

__all__ = [
    "ImminenceLevel",
    "RIPResult",
    "RugImminencePredictor",
    "SignalCategory",
]
