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

from app.domains.prediction.prediction_market_service import (
    CRYPTO_KEYWORDS,
    SECURITY_KEYWORDS,
    PredictionDigest,
    PredictionMarket,
    PredictionMarketService,
    _cache_hash,
    _dict_to_digest,
    _dict_to_market,
    _digest_to_dict,
    _extract_token_symbols,
    _market_to_dict,
    get_prediction_market_service,
)


__all__ = [
    "CRYPTO_KEYWORDS",
    "SECURITY_KEYWORDS",
    "PredictionDigest",
    "PredictionMarket",
    "PredictionMarketService",
    "_cache_hash",
    "_dict_to_digest",
    "_dict_to_market",
    "_digest_to_dict",
    "_extract_token_symbols",
    "_market_to_dict",
    "get_prediction_market_service",
]
