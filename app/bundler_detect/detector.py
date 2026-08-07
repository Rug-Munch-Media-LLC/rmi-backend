"""
Bundler Detector - Core Detector
=================================
Main detector class for bundled/supply-manipulated token launches.
Orchestrates data fetching, analysis, and scoring.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.bundler_detect.fetchers import fetch_buys, fetch_holders, fetch_metadata
from app.bundler_detect.models import (
    EVM_CHAINS,
    SUPPORTED_CHAINS,
    BundledBuy,
    BundlerReport,
    HolderCluster,
)
from app.bundler_detect.scoring import gini_coefficient, label_risk
from app.bundler_detect.scoring_methods import (
    compute_bundler_score,
    score_fund_flow,
    score_launch_timing,
    score_sniper_clusters,
    score_supply_concentration,
)
from app.core.logging import get_logger
from app.core.shared_utils import EVM_ADDRESS_RE, SOLANA_ADDRESS_RE

logger = get_logger(__name__)


class BundlerDetector:
    """Main detector for bundled/supply-manipulated token launches."""

    def __init__(self, http_timeout: float = 15.0) -> None:
        self.http = httpx.AsyncClient(timeout=http_timeout)
        self._birdeye_api_key = os.environ.get("BIRDEYE_API_KEY", "")

    async def close(self) -> None:
        await self.http.aclose()

    async def scan(self, address: str, chain: str) -> BundlerReport:
        """Full supply manipulation analysis for a token."""
        if not self._validate_address(address, chain):
            return BundlerReport(
                token_address=address,
                chain=chain,
                errors=[f"Invalid address format for chain: {chain}"],
                risk_label="error",
            )

        chain = chain.lower()
        if chain not in SUPPORTED_CHAINS:
            return BundlerReport(
                token_address=address,
                chain=chain,
                errors=[f"Unsupported chain: {chain}"],
                risk_label="error",
            )

        report = BundlerReport(token_address=address, chain=chain)

        try:
            # 1. Fetch metadata
            metadata = await fetch_metadata(self.http, address, chain)
            report.name = metadata.get("name", "Unknown")
            report.symbol = metadata.get("symbol", "UNKNOWN")
            report.raw["metadata"] = metadata

            # 2. Fetch holders
            holders = await fetch_holders(self.http, address, chain, self._birdeye_api_key)
            report.raw["holders_raw"] = holders

            if not holders:
                report.errors.append("No holder data available")
                report.risk_label = "error"
                return report

            # 3. Supply concentration
            top10_pct = self._compute_top_holder_pct(holders, 10)
            report.top_10_holder_concentration = top10_pct
            report.dev_hold_pct = self._extract_dev_hold_pct(holders, metadata)

            # 4. Fetch and analyze buys
            buys = await fetch_buys(self.http, address, chain)
            report.raw["buys_raw"] = buys

            # 5. Detect bundled buys
            bundled_buys, same_funding = self._detect_bundled_buys(buys)
            report.suspected_bundled_buys = bundled_buys
            report.buys_from_same_funding = same_funding

            # 6. Launch timing analysis
            timing = self._analyze_launch_timing(buys)
            report.unique_buyers_first_block = timing["unique_buyers_first_block"]
            report.total_buys_first_blocks = timing["total_buys_first_blocks"]

            # 7. Cluster wallets
            clusters = self._cluster_wallets(buys, holders)
            report.holder_clusters = clusters

            # 8. Estimate entities
            report.estimated_unique_entities = self._estimate_entities(holders, clusters, len(bundled_buys))

            # 9. Compute scores
            report.supply_concentration_score = score_supply_concentration(holders, top10_pct)
            report.sniper_cluster_score = score_sniper_clusters(clusters, bundled_buys)
            report.launch_timing_anomaly_score = self._score_launch_timing(timing, buys, holders)
            report.fund_flow_risk_score = score_fund_flow(bundled_buys, same_funding, clusters)

            # 10. Composite score
            report.bundler_score = compute_bundler_score(report)
            report.risk_label = label_risk(report.bundler_score)

        except Exception as e:
            logger.error(f"Bundler scan error for {address}: {e}")
            report.errors.append(str(e))
            report.risk_label = "error"

        return report

    async def quick_check(self, address: str, chain: str) -> dict[str, Any]:
        """Quick supply concentration check - holder data only."""
        if not self._validate_address(address, chain):
            return {"error": f"Invalid address for chain {chain}"}

        chain = chain.lower()
        metadata = await fetch_metadata(self.http, address, chain)
        holders = await fetch_holders(self.http, address, chain, self._birdeye_api_key)

        if not holders:
            return {
                "address": address,
                "chain": chain,
                "name": metadata.get("name", ""),
                "symbol": metadata.get("symbol", ""),
                "error": "No holder data available",
            }

        top10 = self._compute_top_holder_pct(holders, 10)
        gini = gini_coefficient([h.get("percentage", 0) for h in holders[:100]])

        score = 0.0
        if top10 > 80:
            score += 40
        elif top10 > 60:
            score += 25
        if gini > 0.8:
            score += 30
        elif gini > 0.6:
            score += 15

        return {
            "address": address,
            "chain": chain,
            "name": metadata.get("name", ""),
            "symbol": metadata.get("symbol", ""),
            "supply_concentration_score": min(score, 100),
            "risk_label": label_risk(min(score, 100)),
            "top_10_holder_pct": round(top10, 2),
            "gini_coefficient": round(gini, 3),
        }

    # ── Validation ──────────────────────────────────────────────

    def _validate_address(self, address: str, chain: str) -> bool:
        chain = chain.lower()
        if chain == "solana":
            return bool(SOLANA_ADDRESS_RE.match(address))
        if chain in EVM_CHAINS:
            return bool(EVM_ADDRESS_RE.match(address))
        return bool(EVM_ADDRESS_RE.match(address) or SOLANA_ADDRESS_RE.match(address))

    # ── Analysis ────────────────────────────────────────────────

    @staticmethod
    def _compute_top_holder_pct(holders: list[dict[str, Any]], top_n: int) -> float:
        sorted_h = sorted(holders, key=lambda h: h.get("percentage", 0), reverse=True)
        top = sorted_h[:top_n]
        return sum(h.get("percentage", 0) for h in top if h.get("percentage") is not None)

    @staticmethod
    def _extract_dev_hold_pct(holders: list[dict[str, Any]], metadata: dict[str, Any]) -> float:
        return holders[0].get("percentage", 0) if holders else 0.0

    def _detect_bundled_buys(self, buys: list[dict[str, Any]]) -> tuple[list[BundledBuy], int]:
        bundled: list[BundledBuy] = []
        same_funding_count = 0

        for buy in buys:
            m5_buys = buy.get("m5_buys", 0)
            h1_buys = buy.get("h1_buys", 0)

            if m5_buys > 0 and h1_buys > 0:
                m5_rate = m5_buys / 5
                h1_rate = h1_buys / 60
                if m5_rate > h1_rate * 3 and m5_buys >= 10:
                    bundled.append(
                        BundledBuy(
                            wallet=f"cluster:{buy.get('pair_address', '')[:12]}",
                            amount_usd=0,
                            buy_block=0,
                            buy_timestamp=time.time(),
                            tx_hash="",
                            funding_source="aggregated",
                            is_sniper=True,
                        )
                    )
                    same_funding_count += m5_buys

        return bundled, same_funding_count

    def _analyze_launch_timing(self, buys: list[dict[str, Any]]) -> dict[str, Any]:
        result = {
            "unique_buyers_first_block": 0,
            "total_buys_first_blocks": 0,
            "buy_concentration_ratio": 0.0,
        }

        for buy in buys:
            m5 = buy.get("m5_buys", 0)
            h1 = buy.get("h1_buys", 0)
            h6 = buy.get("h6_buys", 0)
            total = m5 + h1 + h6

            if total > 0:
                first_5m_pct = m5 / total if total > 0 else 0
                result["buy_concentration_ratio"] = max(result["buy_concentration_ratio"], first_5m_pct)
                result["total_buys_first_blocks"] += m5
                if h1 > 0 and m5 > 0:
                    result["unique_buyers_first_block"] = max(result["unique_buyers_first_block"], min(m5, h1))

        return result

    def _cluster_wallets(self, buys: list[dict[str, Any]], holders: list[dict[str, Any]]) -> list[HolderCluster]:
        clusters: list[HolderCluster] = []

        if not holders:
            return clusters

        sorted_h = sorted(holders, key=lambda h: h.get("percentage", 0), reverse=True)

        # Top 3 holders cluster
        top3 = sorted_h[:3]
        top3_pct = sum(h.get("percentage", 0) for h in top3 if h.get("percentage") is not None)
        if top3_pct > 60 and len(top3) >= 2:
            clusters.append(
                HolderCluster(
                    wallets=[h.get("address", "") for h in top3 if h.get("address")],
                    total_supply_pct=top3_pct,
                    funding_overlap_score=0.7 if top3_pct > 80 else 0.5,
                    buy_time_similarity=0.8 if top3_pct > 80 else 0.6,
                    common_funding_source="top_holders_cluster",
                )
            )

        # Mid-holder belt (5-15% each)
        cluster_wallets: list[dict[str, Any]] = []
        cluster_pct = 0.0
        for h in sorted_h[3:]:
            pct = h.get("percentage", 0)
            if pct and 2 <= pct <= 15:
                cluster_wallets.append(h)
                cluster_pct += pct
                if len(cluster_wallets) >= 5 and cluster_pct >= 15:
                    break

        if len(cluster_wallets) >= 5 and cluster_pct >= 15:
            clusters.append(
                HolderCluster(
                    wallets=[h.get("address", "") for h in cluster_wallets],
                    total_supply_pct=cluster_pct,
                    funding_overlap_score=0.6,
                    buy_time_similarity=0.7,
                    common_funding_source="mid_holder_belt",
                )
            )

        return clusters

    @staticmethod
    def _estimate_entities(
        holders: list[dict[str, Any]],
        clusters: list[HolderCluster],
        bundled_buys_count: int,
    ) -> int:
        total_holders = len(holders)
        cluster_wallet_count = sum(len(c.wallets) for c in clusters)
        entities = max(1, total_holders - cluster_wallet_count)
        if bundled_buys_count > 20:
            entities = max(1, entities - bundled_buys_count // 5)
        return entities
