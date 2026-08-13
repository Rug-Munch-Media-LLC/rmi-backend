"""
Degen Security Scanner — Main Scanner Engine
==============================================
DegenSecurityScanner class orchestrating all detection phases.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.free_solscan_client import FreeSolscanClient

from .config import BIRDEYE_API_KEY, HELIUS_API_KEY
from .models import SecurityReport
from .phases import (
    phase1_metadata,
    phase2_authorities,
    phase3_holders,
    phase4_funding,
    phase5_bundlers_snipers,
    phase6_liquidity,
    phase7_advanced,
)

logging.basicConfig(level=logging.INFO)
logger = get_logger(__name__)


class DegenSecurityScanner:
    """
    Ultimate degen security scanner.
    Analyzes any Solana token for 15+ scam indicators.
    """

    def __init__(self) -> None:
        self.solscan = FreeSolscanClient()
        self.helius_key = HELIUS_API_KEY
        self.birdeye_key = BIRDEYE_API_KEY
        self.moralis_key = None
        self.gmgn_key = None
        self.critical_warnings: list[str] = []
        self.recommendations: list[str] = []

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════════════

    async def full_scan(self, token_address: str, quick: bool = False) -> SecurityReport:
        """
        Run comprehensive security scan on a token.

        Args:
            token_address: The token mint address
            quick: If True, skip slow operations (dev history, wash trade deep scan)

        Returns:
            SecurityReport with all analysis results
        """
        start_time = time.time()
        logger.info(f"\U0001f50d Starting DEGEN SCAN for {token_address}")

        report = SecurityReport(
            token_address=token_address,
            scan_timestamp=datetime.now(UTC).isoformat(),
        )

        # ── Phase 1: Token Metadata ──────────────────────────────────────────
        logger.info("[1/7] Gathering token metadata...")
        await phase1_metadata(self, report)
        await asyncio.sleep(0.3)

        # ── Phase 2: Authority Analysis ──────────────────────────────────────
        logger.info("[2/7] Checking authorities...")
        await phase2_authorities(self, report)
        await asyncio.sleep(0.2)

        # ── Phase 3: Holder Analysis ─────────────────────────────────────────
        logger.info("[3/7] Analyzing holders...")
        await phase3_holders(self, report)
        await asyncio.sleep(0.3)

        # ── Phase 4: Funding Trace ───────────────────────────────────────────
        logger.info("[4/7] Tracing funding sources...")
        await phase4_funding(self, report)
        await asyncio.sleep(0.3)

        # ── Phase 5: Bundler & Sniper Detection ─────────────────────────────
        logger.info("[5/7] Detecting bundlers & snipers...")
        await phase5_bundlers_snipers(self, report)
        await asyncio.sleep(0.3)

        # ── Phase 6: LP & Market Analysis ────────────────────────────────────
        logger.info("[6/7] Analyzing liquidity pools...")
        await phase6_liquidity(self, report)
        await asyncio.sleep(0.3)

        # ── Phase 7: Advanced Detection ──────────────────────────────────────
        logger.info("[7/7] Advanced scam detection...")
        await phase7_advanced(self, report, quick=quick)

        # ── Final: Calculate Scores ──────────────────────────────────────────
        logger.info("\u26a1 Calculating final risk scores...")
        self._calculate_final_scores(report)

        report.scan_duration_ms = int((time.time() - start_time) * 1000)
        report.critical_warnings = self.critical_warnings
        report.recommendations = self.recommendations

        logger.info(f"\u2705 DEGEN SCAN complete in {report.scan_duration_ms}ms")
        logger.info(f"\U0001f3af RUG PULL PROBABILITY: {report.rug_pull_probability}%")
        logger.info(f"\U0001f6a8 RISK LEVEL: {report.overall_risk}")

        return report

    # ═══════════════════════════════════════════════════════════════════════
    # FINAL SCORING
    # ═══════════════════════════════════════════════════════════════════════

    def _calculate_final_scores(self, report: SecurityReport) -> None:
        """Calculate the ultimate RUG PULL PROBABILITY score."""
        score: float = 0.0

        # Authority risk (20 pts)
        auth_map = {"LOW": 0, "MEDIUM": 10, "HIGH": 15, "CRITICAL": 20}
        score += auth_map.get(report.authorities_risk, 0)

        # Holder concentration (15 pts)
        score += min(report.top_10_concentration * 0.15, 15)

        # Funding risk (10 pts)
        fund_map = {"LOW": 0, "MEDIUM": 5, "HIGH": 8, "CRITICAL": 10}
        score += fund_map.get(report.funding_risk, 0)

        # Bundler risk (15 pts)
        if report.bundler_detected:
            score += min(len(report.bundler_wallets) * 1.5, 15)

        # Sniper risk (5 pts)
        sniper_map = {"LOW": 0, "MEDIUM": 3, "HIGH": 5}
        score += sniper_map.get(report.sniper_risk, 0)

        # Liquidity risk (15 pts)
        lp_map = {"LOW": 0, "MEDIUM": 7, "HIGH": 12, "CRITICAL": 15}
        score += lp_map.get(report.lp_risk, 0)

        # Insider risk (5 pts)
        insider_map = {"LOW": 0, "MEDIUM": 3, "HIGH": 5}
        score += insider_map.get(report.insider_risk, 0)

        # Wash trading (5 pts)
        wash_map = {"LOW": 0, "MEDIUM": 3, "HIGH": 4, "CRITICAL": 5}
        score += wash_map.get(report.wash_trading_risk, 0)

        # Tax/honeypot (5 pts)
        if report.honeypot_detected:
            score += 5
        else:
            tax_map = {"LOW": 0, "MEDIUM": 2, "HIGH": 4, "CRITICAL": 5}
            score += tax_map.get(report.tax_risk, 0)

        # Dev risk (5 pts)
        dev_map = {"LOW": 0, "MEDIUM": 3, "HIGH": 5}
        score += dev_map.get(report.dev_risk, 0)

        # Cap and finalize
        report.rug_pull_probability = min(int(score), 100)

        if report.rug_pull_probability >= 70:
            report.overall_risk = "CRITICAL"
            self.recommendations.insert(0, "\U0001f6d1 DO NOT INVEST - EXTREME RISK")
        elif report.rug_pull_probability >= 50:
            report.overall_risk = "HIGH"
            self.recommendations.insert(0, "\u26a0\ufe0f HIGH RISK - Only gamble what you can lose")
        elif report.rug_pull_probability >= 30:
            report.overall_risk = "MEDIUM"
        else:
            report.overall_risk = "LOW"

        # Confidence level
        data_sources = sum(
            [
                report.total_holders > 0,
                report.total_liquidity_usd > 0,
                len(report.top_holders) > 0,
                report.mint_authority is not None,
            ]
        )
        if data_sources >= 4:
            report.confidence_level = "HIGH"
        elif data_sources >= 2:
            report.confidence_level = "MEDIUM"
        else:
            report.confidence_level = "LOW"

        # Final recommendations
        if not report.mint_authority_renounced:
            self.recommendations.append("Verify mint authority renouncement on-chain")
        if report.total_liquidity_usd < 50000:
            self.recommendations.append("Low liquidity - large sells will cause massive slippage")
        if report.top_10_concentration > 40:
            self.recommendations.append("Top-heavy distribution - watch for whale dumps")
