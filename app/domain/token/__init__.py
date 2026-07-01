"""Token domain — public API + health check."""
from __future__ import annotations

from app.core import health as health_mod
from app.core.health import DomainHealth


async def _health_check() -> DomainHealth:
    """Token health: DataBus is importable."""
    try:
        import app.databus  # noqa: F401
        return DomainHealth(
            name="token",
            healthy=True,
            details={"databus": "importable"},
        )
    except Exception as e:
        return DomainHealth(name="token", healthy=False, error=str(e))


health_mod.register_health_check("token", _health_check)


# Public API
from app.domain.token.analyzer import TokenAnalyzer
from app.domain.token.models import (
    RiskLevel,
    Token,
    TokenDetail,
    TokenHolder,
    TokenLiquidity,
    TokenRisk,
    TokenScanRequest,
    TokenScanResult,
)
from app.domain.token.repository import TokenRepository
from app.domain.token.service import TokenService

__all__ = [
    "RiskLevel",
    "Token",
    "TokenAnalyzer",
    "TokenDetail",
    "TokenHolder",
    "TokenLiquidity",
    "TokenRepository",
    "TokenRisk",
    "TokenScanRequest",
    "TokenScanResult",
    "TokenService",
]
