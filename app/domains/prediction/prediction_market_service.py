"""
RMI Prediction Market Intelligence Service
Re-export from package.
"""
from app.domains.prediction.prediction_market_package import (  # noqa: F401
    PredictionDigest,
    PredictionMarket,
    PredictionMarketService,
    get_prediction_market_service,
)
