"""
RMI Prediction Market Intelligence Service
===========================================
Multi-source prediction market data aggregation for crypto security intelligence.

Usage:
    from app.domains.prediction.prediction_market_service import PredictionMarketService
    service = PredictionMarketService()
    results = await service.search("bitcoin hack")
"""
from app.domains.prediction.prediction_market_package.service import (
    PredictionMarketService,
    get_prediction_market_service,
)
from app.domains.prediction.prediction_market_package.models import (
    PredictionDigest,
    PredictionMarket,
)

__all__ = [
    "PredictionMarketService",
    "get_prediction_market_service",
    "PredictionDigest",
    "PredictionMarket",
]
