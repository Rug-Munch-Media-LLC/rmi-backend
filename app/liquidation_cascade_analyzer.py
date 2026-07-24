"""
Liquidation Cascade Analyzer
============================
Analyzes liquidation cascades across DeFi lending protocols, identifying
contagion risk and potential systemic failures.

Backward-compatibility shim: this module now lives in ``app.domains.analysis``.

Usage:
    from app.liquidation_cascade_analyzer import LiquidationCascadeAnalyzer
"""

from __future__ import annotations

from app.domains.analysis.liquidation_cascade_analyzer import (  # noqa: F401
    CascadeScenario,
    CollateralPosition,
    DebtPosition,
    LiquidationAnalysis,
    LiquidationCascadeAnalyzer,
    LiquidationCluster,
    ProtocolPosition,
    RiskTier,
    _estimate_asset_ltv,
    _estimate_asset_price,
    _resolve_asset_symbol,
    _try_import_web3,
    _validate_address,
    generate_report,
    main,
)

if __name__ == "__main__":
    from app.domains.analysis.liquidation_cascade_analyzer import main as _main
    _main()
