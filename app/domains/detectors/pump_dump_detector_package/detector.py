"""
Pump & Dump — Main Detector
=============================
PumpDumpDetector class and convenience functions.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.chain_client import ChainClient
from app.core.logging import get_logger
from app.domains.detectors.pump_dump_detector_package.signals_accum import (
    calculate_risk_score,
    check_lifecycle_patterns,
    detect_pre_pump_accumulation,
    estimate_traders,
    score_to_level,
)
from app.domains.detectors.pump_dump_detector_package.signals_coord import (
    detect_coordinated_buys,
    detect_wash_trading,
)
from app.domains.detectors.pump_dump_detector_package.signals_volume import (
    analyze_price_pump,
    detect_volume_anomalies,
)
from app.domains.detectors.pump_dump_detector_package.sources import fetch_pairs
from app.domains.detectors.pump_dump_detector_package.types import (
    ManipulationFinding,
    PumpDumpAnalysisResult,
)

logger = get_logger("pump_dump_detector")

class PumpDumpDetector:
    """
    Comprehensive pump-and-dump / coordinated market manipulation detector.
    Analyzes on-chain data to identify manipulation patterns across all 13 chains.
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)
        self._chain_client = ChainClient()

    async def __aenter__(self) -> PumpDumpDetector:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def analyze_token(
        self,
        token_address: str,
        chain: str = "ethereum",
    ) -> PumpDumpAnalysisResult:
        """
        Main entry point: analyze a token for pump-and-dump / manipulation patterns.
        """
        timestamp = datetime.now(UTC).isoformat()
        result = PumpDumpAnalysisResult(
            token_address=token_address, chain=chain,
            token_symbol="", token_name="",
            risk_score=0.0, risk_level="low",
            analysis_timestamp=timestamp,
        )

        try:
            # Step 1: Basic token info from DexScreener
            pairs_data = await fetch_pairs(self._client, token_address, chain)
            if not pairs_data or "pairs" not in pairs_data or not pairs_data["pairs"]:
                result.risk_level = "unknown"
                result.error = "No trading pairs found on DexScreener"
                return result

            result.token_symbol = pairs_data["pairs"][0].get("baseToken", {}).get("symbol", "?")
            result.token_name = pairs_data["pairs"][0].get("baseToken", {}).get("name", "?")

            # Step 2: Volume anomaly detection
            vol_result = await detect_volume_anomalies(pairs_data)
            result.volume_anomalies = vol_result["anomalies"]
            for finding in vol_result["findings"]:
                result.findings.append(ManipulationFinding(**finding))

            # Step 3: Coordinated buy detection
            coord_result = await detect_coordinated_buys(self._client, token_address, chain)
            result.coordinated_groups = coord_result["groups"]
            for finding in coord_result["findings"]:
                result.findings.append(ManipulationFinding(**finding))

            # Step 4: Wash trading detection
            wash_result = await detect_wash_trading(self._client, token_address, chain)
            result.wash_trade_clusters = wash_result["clusters"]
            for finding in wash_result["findings"]:
                result.findings.append(ManipulationFinding(**finding))

            # Step 5: Price pump analysis
            pump_result = await analyze_price_pump(pairs_data)
            result.price_pump = pump_result["signal"]
            for finding in pump_result["findings"]:
                result.findings.append(ManipulationFinding(**finding))

            # Step 6: Pre-pump accumulation
            accum_result = await detect_pre_pump_accumulation(self._client, token_address, chain)
            result.pre_pump_accumulations = accum_result["accumulations"]
            for finding in accum_result["findings"]:
                result.findings.append(ManipulationFinding(**finding))

            # Step 7: Lifecycle pattern matching
            lifecycle_findings = check_lifecycle_patterns(pairs_data)
            for finding in lifecycle_findings:
                result.findings.append(ManipulationFinding(**finding))

            # Calculate risk score
            result.risk_score = calculate_risk_score(result.findings)
            result.risk_level = score_to_level(result.risk_score)
            result.unique_traders_analyzed = estimate_traders(pairs_data)

            if pairs_data.get("pairs"):
                vol_24h = sum(
                    float(p.get("volume", {}).get("h24", 0) or 0) for p in pairs_data["pairs"]
                )
                result.total_volume_analyzed_usd = vol_24h

        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e :
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Error analyzing {token_address} on {chain}: {e}")
            result.error = str(e)[:200]
            result.risk_level = "error"

        return result

    async def close(self) -> None:
        await self._client.aclose()

# ── Convenience Functions ────────────────────────────────────────────

async def analyze_token(
    token_address: str,
    chain: str = "ethereum",
) -> PumpDumpAnalysisResult:
    """Convenience function for single-token analysis."""
    detector = PumpDumpDetector()
    try:
        return await detector.analyze_token(token_address, chain)
    finally:
        await detector.close()

def analyze_token_sync(
    token_address: str,
    chain: str = "ethereum",
) -> dict:
    """Synchronous wrapper for environments without async support."""
    return asyncio.run(analyze_token(token_address, chain)).to_dict()

# ── CLI Entry Point ──────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pump & Dump / Coordinated Manipulation Detector")
    parser.add_argument("token", help="Token contract address")
    parser.add_argument("--chain", default="ethereum", help="Blockchain name (default: ethereum)")
    parser.add_argument("--alert", action="store_true", help="Only output if risk > medium")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()
    result = asyncio.run(analyze_token(args.token, args.chain))

    if args.alert and result.risk_score < 40:
        print("[SILENT]")
    elif args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.to_markdown())
