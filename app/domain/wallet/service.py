"""Service - business logic for wallet analysis and scans.

Composes the repository (data access) and the analyzer (pure logic).
This is the only thing the api/ layer should call.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.domain.wallet.analyzer import WalletAnalyzer
from app.domain.wallet.models import (
    Balance,
    ScanRequest,
    ScanResult,
    Transaction,
    Wallet,
    WalletAnalysis,
)
from app.domain.wallet.repository import WalletRepository

log = get_logger(__name__)


class WalletService:
    """Orchestrates wallet data fetch and analysis."""

    def __init__(
        self,
        repo: WalletRepository | None = None,
        analyzer: WalletAnalyzer | None = None,
    ) -> None:
        self._repo = repo or WalletRepository()
        self._analyzer = analyzer or WalletAnalyzer()

    async def analyze(
        self,
        address: str,
        chain: str = "solana",
    ) -> WalletAnalysis:
        """Full wallet analysis: balance + transactions + labels → risk-scored result."""
        wallet = Wallet(address=address, chain=chain)
        log.info("wallet_analysis_started", address=address[:12], chain=chain)
        balance = await self._repo.get_balance(wallet)
        txs = await self._repo.get_transactions(wallet, limit=10)
        labels = await self._repo.get_labels(wallet)
        analysis = self._analyzer.analyze(
            address=address,
            chain=chain,
            tokens=balance.tokens,
            recent_transactions=txs,
            labels=labels,
        )
        # Override total value with the balance's authoritative value
        analysis.total_value_usd = balance.total_value_usd
        log.info(
            "wallet_analysis_complete",
            address=address[:12],
            risk_score=analysis.risk_score,
            risk_level=analysis.risk_level.value,
        )
        return analysis

    async def get_balance(self, address: str, chain: str = "solana") -> Balance:
        """Just the balance."""
        return await self._repo.get_balance(Wallet(address=address, chain=chain))

    async def get_transactions(
        self,
        address: str,
        chain: str = "solana",
        limit: int = 50,
    ) -> list[Transaction]:
        """Just the transactions."""
        return await self._repo.get_transactions(
            Wallet(address=address, chain=chain),
            limit=limit,
        )

    async def scan(self, req: ScanRequest) -> ScanResult:
        """Threat scan. Multi-module. Returns a ScanResult.

        The legacy /api/v1/wallet/scan does a freemium rate-limit check
        before calling the scanner. That check lives in the api/ layer
        (HTTP concern), not here.
        """
        log.info("wallet_scan_started", address=req.address[:12], chain=req.chain, tier=req.tier)
        # For now: the scan reuses analyze() + adds module-level flags.
        analysis = await self.analyze(req.address, req.chain)
        result = ScanResult(
            address=analysis.address,
            chain=analysis.chain,
            risk_score=analysis.risk_score,
            risk_level=analysis.risk_level,
            flags=analysis.flags,
            modules_run=["analyzer"],
        )
        log.info(
            "wallet_scan_complete",
            address=req.address[:12],
            risk_score=result.risk_score,
            risk_level=result.risk_level.value,
            flag_count=len(result.flags),
        )
        return result
