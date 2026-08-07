"""
Liquidation Cascade Analyzer
============================
Analyzes liquidation cascades across DeFi lending protocols, identifying
contagion risk and potential systemic failures.

Backward-compatibility shim: re-exports everything from
``app.domains.analysis.liquidation``.

Usage:
    from app.liquidation_cascade_analyzer import LiquidationCascadeAnalyzer
"""

from __future__ import annotations

from app.domains.analysis.liquidation import *

if __name__ == "__main__":
    from app.domains.analysis.liquidation_cascade_analyzer import main as _main

    _main()
