"""
Prediction Market Service
=========================
Integrates with prediction market platforms to analyze market sentiment,
track positions, and detect manipulation patterns.

Backward-compatibility shim: this module now lives in ``app.domains.prediction``.

Usage:
    from app.prediction_market_service import PredictionMarketService
"""

from __future__ import annotations

from app.domains.prediction.prediction_market_service import *
