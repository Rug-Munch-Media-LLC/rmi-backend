"""
Portfolio Risk Aggregator
=========================
Aggregates risk scores across a wallet's entire portfolio, combining token
risk, protocol risk, and market exposure into unified metrics.

Backward-compatibility shim: this module now lives in ``app.domains.portfolio``.

Usage:
    from app.portfolio_risk_aggregator import PortfolioRiskAggregator
"""

from __future__ import annotations

from app.domains.portfolio.portfolio_risk_aggregator import *

if __name__ == "__main__":
    from app.domains.portfolio.portfolio_risk_aggregator import main as _main
    _main()
