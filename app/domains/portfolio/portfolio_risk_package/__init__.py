"""
Cross-Chain Portfolio Risk Aggregator
======================================
Aggregates wallet holdings across all supported chains and produces a unified risk profile.

Usage:
    from app.domains.portfolio.portfolio_risk_aggregator import PortfolioRiskAggregator
    agg = PortfolioRiskAggregator()
    profile = await agg.analyze("0x...")
"""
from app.domains.portfolio.portfolio_risk_package.aggregator import PortfolioRiskAggregator
from app.domains.portfolio.portfolio_risk_package.models import (
    ChainPortfolio,
    PortfolioRiskProfile,
    RiskLevel,
    TokenHolding,
)
from app.domains.portfolio.portfolio_risk_package.constants import validate_wallet_address

__all__ = [
    "PortfolioRiskAggregator",
    "ChainPortfolio",
    "PortfolioRiskProfile",
    "RiskLevel",
    "TokenHolding",
    "validate_wallet_address",
]
