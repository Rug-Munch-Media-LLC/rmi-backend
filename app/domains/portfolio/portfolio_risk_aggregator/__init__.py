"""
Cross-Chain Portfolio Risk Aggregator Package.
"""

from app.domains.portfolio.portfolio_risk_aggregator.aggregator import (  # type: ignore[attr-defined]  # PTB None-able fields
    PortfolioRiskAggregator,
    validate_wallet_address,
)
from app.domains.portfolio.portfolio_risk_aggregator.config import (
    CHAIN_CONFIGS,
    COMMON_TOKENS,
    ERC20_ABI,
)
from app.domains.portfolio.portfolio_risk_aggregator.connectors import (
    ChainConnector,
    EVMConnector,
    SolanaConnector,
)
from app.domains.portfolio.portfolio_risk_aggregator.models import (
    ChainPortfolio,
    PortfolioRiskProfile,
    RiskLevel,
    TokenHolding,
)
from app.domains.portfolio.portfolio_risk_aggregator.scorers import (
    PriceOracle,
    TokenRiskScorer,
)

__all__ = [
    "CHAIN_CONFIGS",
    "COMMON_TOKENS",
    "ERC20_ABI",
    "ChainConnector",
    "ChainPortfolio",
    "EVMConnector",
    "PortfolioRiskAggregator",
    "PortfolioRiskProfile",
    "PriceOracle",
    "RiskLevel",
    "SolanaConnector",
    "TokenHolding",
    "TokenRiskScorer",
    "validate_wallet_address",
]
