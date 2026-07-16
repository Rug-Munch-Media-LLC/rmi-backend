"""Service - business logic for token info, risk, and scans."""

from __future__ import annotations

from app.core.logging import get_logger
from app.domains.token.analyzer import TokenAnalyzer
from app.domains.token.models import (
    TokenDetail,
    TokenHolder,
    TokenLiquidity,
    TokenRisk,
    TokenScanRequest,
    TokenScanResult,
)
from app.domains.token.repository import TokenRepository

log = get_logger(__name__)


class TokenService:
    """Orchestrates token data fetch and analysis."""

    def __init__(
        self,
        repo: TokenRepository | None = None,
        analyzer: TokenAnalyzer | None = None,
    ) -> None:
        self._repo = repo or TokenRepository()
        self._analyzer = analyzer or TokenAnalyzer()

    async def get_detail(self, address: str, chain: str = "solana") -> TokenDetail:
        return await self._repo.get_detail(address, chain)

    async def get_holders(
        self,
        address: str,
        chain: str = "solana",
        limit: int = 100,
    ) -> list[TokenHolder]:
        return await self._repo.get_holders(address, chain, limit=limit)

    async def get_liquidity(self, address: str, chain: str = "solana") -> TokenLiquidity:
        return await self._repo.get_liquidity(address, chain)

    async def get_risk(self, address: str, chain: str = "solana") -> TokenRisk:
        """Combined risk: holders + liquidity + heuristics."""
        log.info("token_risk_started", address=address[:12], chain=chain)
        holders = await self._repo.get_holders(address, chain, limit=20)
        liquidity = await self._repo.get_liquidity(address, chain)
        risk = self._analyzer.compute_risk(
            address=address,
            chain=chain,
            holders=holders,
            liquidity=liquidity,
        )
        log.info(
            "token_risk_complete",
            address=address[:12],
            risk_score=risk.risk_score,
            risk_level=risk.risk_level.value,
        )
        return risk

    async def scan(self, req: TokenScanRequest) -> TokenScanResult:
        """Full token scan. Multi-module. Used for free-tier scanner."""
        log.info("token_scan_started", address=req.address[:12], chain=req.chain, tier=req.tier)
        detail = await self._repo.get_detail(req.address, req.chain)
        risk = await self.get_risk(req.address, req.chain)

        # Convert risk flags to the legacy format (list of strings)
        risk_flags: list[str] = []
        warnings: list[str] = []
        for flag in risk.flags:
            code = flag.get("code", "")
            message = flag.get("message", "")
            if code:
                risk_flags.append(code)
            if message:
                warnings.append(message)

        # Heuristic safety score (inverse of risk, 0-100)
        safety_score = max(0.0, 100.0 - float(risk.risk_score))

        result = TokenScanResult(
            address=req.address,
            chain=req.chain,
            safety_score=safety_score,
            risk_score=risk.risk_score,
            risk_level=risk.risk_level,
            risk_flags=risk_flags,
            warnings=warnings,
            modules_run=["analyzer", "holders", "liquidity"],
            metadata={
                "name": detail.name,
                "symbol": detail.symbol,
                "decimals": detail.decimals,
                "total_supply": detail.total_supply,
                "verified": detail.verified,
            },
        )
        log.info(
            "token_scan_complete",
            address=req.address[:12],
            safety_score=safety_score,
            risk_level=risk.risk_level.value,
        )
        return result
