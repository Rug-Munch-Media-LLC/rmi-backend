"""
Cross-Chain Portfolio Risk Aggregator
======================================
Aggregates wallet holdings across all supported chains and produces a unified risk profile.

Usage:
    from app.domains.portfolio.portfolio_risk_aggregator import PortfolioRiskAggregator
    agg = PortfolioRiskAggregator()
    profile = await agg.analyze("0x...")
"""
from __future__ import annotations
from app.domains.portfolio.portfolio_risk_package.aggregator import PortfolioRiskAggregator
from app.domains.portfolio.portfolio_risk_package.constants import validate_wallet_address
from app.domains.portfolio.portfolio_risk_package.models import (
    ChainPortfolio,
    PortfolioRiskProfile,
    RiskLevel,
    TokenHolding,
)

__all__ = [
    "ChainPortfolio",
    "PortfolioRiskAggregator",
    "PortfolioRiskProfile",
    "RiskLevel",
    "TokenHolding",
    "validate_wallet_address",
]
