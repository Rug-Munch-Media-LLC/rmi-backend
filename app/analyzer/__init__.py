"""
Analyzer
========
Portfolio analysis, PnL calculation, and multi-chain analytics tools.

Submodules:
    multi_chain     — Cross-chain portfolio aggregation
    pnl_calculator  — Profit and loss computation
    portfolio       — Portfolio composition analysis
"""

from __future__ import annotations

from app.analyzer.multi_chain import scan_all_chains
from app.analyzer.pnl_calculator import calculate_token_pnl
from app.analyzer.portfolio import build_portfolio

__all__ = ["build_portfolio", "calculate_token_pnl", "scan_all_chains"]
