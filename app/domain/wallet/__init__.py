"""Wallet domain — public API + health check."""
from __future__ import annotations

from app.core import health as health_mod
from app.core.health import DomainHealth


async def _health_check() -> DomainHealth:
    """Wallet health: DataBus is importable (we don't call it — that would be slow)."""
    try:
        # DataBus is the gateway for all chain data. Verify the module loads.
        import app.databus  # noqa: F401
        return DomainHealth(
            name="wallet",
            healthy=True,
            details={"databus": "importable"},
        )
    except Exception as e:
        return DomainHealth(name="wallet", healthy=False, error=str(e))


health_mod.register_health_check("wallet", _health_check)


# Public API
from app.domain.wallet.analyzer import WalletAnalyzer
from app.domain.wallet.models import (
    Balance,
    RiskLevel,
    ScanFlag,
    ScanRequest,
    ScanResult,
    TokenHolding,
    Transaction,
    Wallet,
    WalletAnalysis,
)
from app.domain.wallet.repository import WalletRepository
from app.domain.wallet.service import WalletService

__all__ = [
    "Balance",
    "RiskLevel",
    "ScanFlag",
    "ScanRequest",
    "ScanResult",
    "TokenHolding",
    "Transaction",
    "Wallet",
    "WalletAnalysis",
    "WalletAnalyzer",
    "WalletRepository",
    "WalletService",
]
