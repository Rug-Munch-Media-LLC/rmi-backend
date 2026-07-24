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
from app.domains.detectors.rug_imminence_predictor import *  # noqa: F401, F403
