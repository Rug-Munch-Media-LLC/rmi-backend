"""
Wallet Risk Scoring - Composite risk across all intelligence sources.
=====================================================================
THIN ADAPTER. Canonical source: `rugmunch_scanner.wallet_memory.risk_scoring`.
The implementation was superseded by the private rugmunch-scanner repo.
"""

from rugmunch_scanner.wallet_memory.risk_scoring import RiskScorer

__all__ = [
    "RiskScorer",
]
