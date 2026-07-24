"""
RMI Prediction Market Intelligence Service
Re-export from package.
"""
from __future__ import annotations
from app.domains.prediction.prediction_market_service import (  # noqa: F401
    PredictionDigest,
    PredictionMarket,
    PredictionMarketService,
    get_prediction_market_service,
)
