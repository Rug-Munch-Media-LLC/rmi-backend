"""Token domain - public API + health check."""

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
    except (ValueError, TypeError, KeyError, OSError) as e :
        return DomainHealth(name="token", healthy=False, error=str(e))


health_mod.register_health_check("token", _health_check)


# Public API
from app.domains.token.analyzer import TokenAnalyzer  # noqa: E402
from app.domains.token.models import (  # noqa: E402
    RiskLevel,
    Token,
    TokenDetail,
    TokenHolder,
    TokenLiquidity,
    TokenRisk,
    TokenScanRequest,
    TokenScanResult,
)
from app.domains.token.repository import TokenRepository  # noqa: E402
from app.domains.token.service import TokenService  # noqa: E402

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
