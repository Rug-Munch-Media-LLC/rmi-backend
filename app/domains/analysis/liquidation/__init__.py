"""
Liquidation Cascade Risk Analysis — modular package.
Re-exports all public symbols for backward compatibility.
"""

# Add report method patch to LiquidationAnalysis
from __future__ import annotations

from app.domains.analysis.liquidation import report as _rp_mod
from app.domains.analysis.liquidation.analyzer import LiquidationCascadeAnalyzer
from app.domains.analysis.liquidation.cli import main
from app.domains.analysis.liquidation.fetcher import (
    LTV_RANGES,
    PROTOCOL_CONFIGS,
    SUPPORTED_CHAINS,
    _estimate_asset_ltv,
    _estimate_asset_price,
    _resolve_asset_symbol,
    _try_import_web3,
    _validate_address,
)
from app.domains.analysis.liquidation.models import (
    CascadeScenario,
    CollateralPosition,
    DebtPosition,
    LiquidationAnalysis,
    LiquidationCluster,
    ProtocolPosition,
    RiskTier,
)
from app.domains.analysis.liquidation.report import generate_report

LiquidationAnalysis.report = lambda self, format="text": _rp_mod.generate_report(self, format)  # type: ignore[method-assign]  # runtime monkey-patch of report method

__all__ = [
    "LTV_RANGES",
    "PROTOCOL_CONFIGS",
    "SUPPORTED_CHAINS",
    "CascadeScenario",
    "CollateralPosition",
    "DebtPosition",
    "LiquidationAnalysis",
    "LiquidationCascadeAnalyzer",
    "LiquidationCluster",
    "ProtocolPosition",
    "RiskTier",
    "_estimate_asset_ltv",
    "_estimate_asset_price",
    "_resolve_asset_symbol",
    "_try_import_web3",
    "_validate_address",
    "generate_report",
    "main",
]
