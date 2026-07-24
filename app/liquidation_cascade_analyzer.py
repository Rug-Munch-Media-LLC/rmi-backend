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
import sys as _sys
from app.domains.analysis.liquidation_cascade_analyzer import *  # noqa: F401, F403

if __name__ == "__main__":
    from app.domains.analysis.liquidation_cascade_analyzer import main as _main
    _main()
