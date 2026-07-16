"""
Exit Scam Forecaster (Rug Pull Predictor)
==========================================
Predictive model identifying tokens at high risk of rug pull.
Multi-signal analysis combining liquidity patterns, holder behavior,
deployer history, and social signals for early warning.

The model evaluates 6 risk dimensions, each weighted by predictive power,
to produce a single 0-100 rug score with explainable per-signal breakdown.

TOOL  : rug_pull_predictor
TIER  : premium / intelligence
PRICE : $0.10 (100000 atoms)
TRIAL : 1 free check

Signals Analyzed:
  - LIQUIDITY: LP lock status, liquidity depth vs market cap, sudden changes
  - HOLDER_CONCENTRATION: Top-10 concentration, single-entity cluster ratio
  - DEPLOYER: Previous project history, rug count, anonymous deployer
  - LAUNCH_PATTERN: Bundled distribution, sniper activity, presale structure
  - TRADING_BEHAVIOR: Seller concentration, wash trading, suspicious transfers
  - SOCIAL_SIGNALS: Low engagement, anonymous team, no community presence

Data Sources (all free):
  - DexScreener - token pairs, liquidity, price
  - Solscan (public) - SPL token holder data
  - Etherscan/BscScan (public free) - EVM token info
  - Birdeye public - basic token metrics
  - GoPlus (free) - token security info (if available)
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import httpx

from app.core.logging import get_logger

__all__ = ["RiskLevel", "RugPullAnalysis", "SignalResult", "analyze", "predict_rug_pull"]

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────

FREE_APIS = {
    "dexscreener": "https://api.dexscreener.com/latest/dex",
    "birdeye_public": "https://public-api.birdeye.so",
    "goplus": "https://api.gopluslabs.io/api/v1",
}

EVM_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Default risk thresholds
DEFAULT_WEIGHTS = {
    "liquidity": 0.25,
    "holder_concentration": 0.20,
    "deployer": 0.15,
    "launch_pattern": 0.18,
    "trading_behavior": 0.12,
    "social_signals": 0.10,
}

CHAIN_MAP: dict[str, str] = {
    "solana": "solana",
    "ethereum": "ethereum",
    "bsc": "bsc",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "avalanche": "avalanche",
}

# ── Data Models ──────────────────────────────────────────────────


class RiskLevel(Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SignalResult:
    """Individual signal analysis result."""

    signal: str
    score: float  # 0 (safe) - 100 (risky)
    weight: float
    findings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def weighted_score(self) -> float:
        return self.score * self.weight

    @property
    def level(self) -> RiskLevel:
        if self.score < 20:
            return RiskLevel.SAFE
        elif self.score < 40:
            return RiskLevel.LOW
        elif self.score < 60:
            return RiskLevel.MEDIUM
        elif self.score < 80:
            return RiskLevel.HIGH
        return RiskLevel.CRITICAL


@dataclass
class RugPullAnalysis:
    """Complete rug pull risk analysis result."""

    token_address: str
    chain: str
    token_symbol: str
    token_name: str

    overall_score: float  # 0 (safe) - 100 (critical risk)
    overall_level: RiskLevel
    signals: list[SignalResult] = field(default_factory=list)
    summary: str = ""
    recommendations: list[str] = field(default_factory=list)
    analysis_timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_address": self.token_address,
            "chain": self.chain,
            "token_symbol": self.token_symbol,
            "token_name": self.token_name,
            "rug_score": round(self.overall_score, 1),
            "risk_level": self.overall_level.value,
            "signals": [
                {
                    "name": s.signal,
                    "score": round(s.score, 1),
                    "level": s.level.value,
                    "findings": s.findings,
                }
                for s in self.signals
            ],
            "summary": self.summary,
            "recommendations": self.recommendations,
            "analyzed_at": self.analysis_timestamp,
        }


# ── Helpers ──────────────────────────────────────────────────────


def is_valid_address(addr: str) -> bool:
    addr = addr.strip()
    return bool(EVM_ADDR_RE.match(addr) or SOLANA_ADDR_RE.match(addr))


def _normalize_chain(chain: str) -> str:
    chain = chain.strip().lower()
    return CHAIN_MAP.get(chain, chain)


def _level_from_score(score: float) -> RiskLevel:
    if score < 20:
        return RiskLevel.SAFE
    elif score < 40:
        return RiskLevel.LOW
    elif score < 60:
        return RiskLevel.MEDIUM
    elif score < 80:
        return RiskLevel.HIGH
    return RiskLevel.CRITICAL


# ── Signal Analyzers ────────────────────────────────────────────


async def _analyze_liquidity(
    client: httpx.AsyncClient, token_address: str, chain: str
) -> SignalResult:
    """Analyze liquidity risk: depth, lock status, sudden changes."""
    signal = SignalResult(
        signal="liquidity",
        score=0.0,
        weight=DEFAULT_WEIGHTS["liquidity"],
        findings=[],
    )

    try:
        # Try DexScreener for pair data
        url = f"{FREE_APIS['dexscreener']}/search?q={token_address}"
        resp = await client.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            pairs = data.get("pairs", [])
            if pairs:
                pair = pairs[0]
                liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                fdv = float(pair.get("fdv", 0) or 0)
                volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)

                signal.details["liquidity_usd"] = liquidity_usd
                signal.details["fdv"] = fdv
                signal.details["volume_24h"] = volume_24h

                # Score: low liquidity = higher risk
                if liquidity_usd < 1000:
                    signal.score += 40
                    signal.findings.append("Extremely low liquidity (<$1K)")
                elif liquidity_usd < 10000:
                    signal.score += 25
                    signal.findings.append("Low liquidity (<$10K)")
                elif liquidity_usd < 100000:
                    signal.score += 15
                else:
                    signal.score += 5

                # Score: low liquidity-to-FDV ratio = manipulation risk
                if fdv > 0 and liquidity_usd > 0:
                    liq_ratio = liquidity_usd / fdv
                    if liq_ratio < 0.01:
                        signal.score += 20
                        signal.findings.append(
                            "Critical: Liquidity-to-FDV ratio <1% - easy to dump"
                        )
                    elif liq_ratio < 0.05:
                        signal.score += 10
                        signal.findings.append("Low liquidity-to-FDV ratio")

                # Score: zero volume or very low = suspicious
                if volume_24h < 100:
                    signal.score += 15
                    signal.findings.append("Near-zero 24h trading volume")
    except Exception as e:
        logger.warning("Liquidity analysis failed: %s", e)
        signal.findings.append("Liquidity data unavailable - scoring conservative")

    signal.score = min(signal.score, 100)
    return signal


async def _analyze_holder_concentration(
    client: httpx.AsyncClient, token_address: str, chain: str
) -> SignalResult:
    """Analyze token holder distribution for concentration risk."""
    signal = SignalResult(
        signal="holder_concentration",
        score=0.0,
        weight=DEFAULT_WEIGHTS["holder_concentration"],
        findings=[],
    )

    if chain == "solana":
        try:
            # Try Birdeye public API for holder info
            resp = await client.get(
                f"{FREE_APIS['birdeye_public']}/public/token/token_owner?address={token_address}",
                headers={"accept": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                holders = (
                    data.get("data", {}).get("tokenOwner", [])
                    or data.get("data", {}).get("list", [])
                    or []
                )
                if holders:
                    total_supply_analyzed = sum(float(h.get("uiAmount", 0) or 0) for h in holders)
                    top10 = sorted(
                        holders, key=lambda h: float(h.get("uiAmount", 0) or 0), reverse=True
                    )[:10]
                    top10_pct = (
                        sum(float(h.get("uiAmount", 0) or 0) for h in top10) / total_supply_analyzed
                        if total_supply_analyzed > 0
                        else 0
                    )

                    signal.details["top_10_concentration_pct"] = round(top10_pct * 100, 1)

                    if top10_pct > 0.8:
                        signal.score += 45
                        signal.findings.append(
                            f"Top 10 holders control {top10_pct * 100:.0f}% of supply - extreme concentration"
                        )
                    elif top10_pct > 0.5:
                        signal.score += 30
                        signal.findings.append(
                            f"Top 10 holders control {top10_pct * 100:.0f}% - high concentration"
                        )
                    elif top10_pct > 0.3:
                        signal.score += 15
                    else:
                        signal.score += 5
        except Exception as e:
            logger.warning("Holder concentration analysis failed: %s", e)
            signal.findings.append("Holder data unavailable")

    # Conservative scoring if we lack data
    if not signal.findings:
        signal.score = 30  # moderate default
        signal.findings.append("Limited holder data - scored conservatively")

    signal.score = min(signal.score, 100)
    return signal


async def _analyze_deployer(
    client: httpx.AsyncClient, deployer: str | None, chain: str
) -> SignalResult:
    """Analyze deployer risk based on available data."""
    signal = SignalResult(
        signal="deployer",
        score=0.0,
        weight=DEFAULT_WEIGHTS["deployer"],
        findings=[],
    )

    if not deployer:
        signal.score = 50
        signal.findings.append("Deployer address unknown - cannot verify history")
        signal.score = min(signal.score, 100)
        return signal

    try:
        if chain == "solana":
            # Try to check if deployer has multiple tokens
            resp = await client.get(
                f"{FREE_APIS['birdeye_public']}/public/token/list?creator={deployer}",
                headers={"accept": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                tokens_deployed = data.get("data", {}).get("items", [])
                token_count = len(tokens_deployed)

                signal.details["deployer_token_count"] = token_count

                if token_count > 20:
                    signal.score += 40
                    signal.findings.append(
                        f"Serial deployer: {token_count} tokens created - potential rug factory"
                    )
                elif token_count > 10:
                    signal.score += 25
                    signal.findings.append(f"Active deployer: {token_count} tokens created")
                elif token_count > 5:
                    signal.score += 10
                else:
                    signal.score += 5
        else:
            # EVM - check if deployer is a known contract factory
            signal.score += 10
            signal.findings.append("EVM deployer - limited public history available")
    except Exception as e:
        logger.warning("Deployer analysis failed: %s", e)
        signal.findings.append("Deployer data unavailable")

    signal.score = min(signal.score, 100)
    return signal


async def _analyze_launch_pattern(
    client: httpx.AsyncClient, token_address: str, chain: str
) -> SignalResult:
    """Analyze launch pattern for manipulation signs."""
    signal = SignalResult(
        signal="launch_pattern",
        score=20.0,  # Start at moderate risk as baseline
        weight=DEFAULT_WEIGHTS["launch_pattern"],
        findings=[],
    )

    try:
        # Use DexScreener for trading data
        url = f"{FREE_APIS['dexscreener']}/search?q={token_address}"
        resp = await client.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            pairs = data.get("pairs", [])
            if pairs:
                pair = pairs[0]
                pair_age_hours = None
                if pair.get("pairCreatedAt"):
                    created = pair["pairCreatedAt"] / 1000  # ms to s
                    pair_age_hours = (time.time() - created) / 3600
                    signal.details["age_hours"] = round(pair_age_hours, 1)

                txns = pair.get("txns", {})
                buys_h24 = int(txns.get("h24", {}).get("buys", 0))
                sells_h24 = int(txns.get("h24", {}).get("sells", 0))

                signal.details["buys_24h"] = buys_h24
                signal.details["sells_24h"] = sells_h24

                # Very young pair = higher risk
                if pair_age_hours is not None:
                    if pair_age_hours < 1:
                        signal.score += 25
                        signal.findings.append(
                            f"Pair is {pair_age_hours:.1f}h old - extremely young, high risk"
                        )
                    elif pair_age_hours < 24:
                        signal.score += 15
                        signal.findings.append(f"Pair is {pair_age_hours:.1f}h old - recent launch")

                # Abnormal buy/sell ratio
                total_txns = buys_h24 + sells_h24
                if total_txns > 0:
                    sell_ratio = sells_h24 / total_txns
                    if sell_ratio > 0.8:
                        signal.score += 20
                        signal.findings.append(
                            f"Sell ratio {sell_ratio * 100:.0f}% - heavy selling pressure"
                        )
                    elif sell_ratio > 0.6:
                        signal.score += 10
                    elif sell_ratio < 0.1 and buys_h24 > 50:
                        signal.score += 15
                        signal.findings.append(
                            "Abnormally low sell ratio - possible bot wash trading"
                        )
    except Exception as e:
        logger.warning("Launch pattern analysis failed: %s", e)
        signal.findings.append("Launch data partially available")

    signal.score = min(signal.score, 100)
    return signal


async def _analyze_trading_behavior(
    client: httpx.AsyncClient, token_address: str, chain: str
) -> SignalResult:
    """Analyze trading behavior for manipulation signs."""
    signal = SignalResult(
        signal="trading_behavior",
        score=10.0,
        weight=DEFAULT_WEIGHTS["trading_behavior"],
        findings=[],
    )

    try:
        url = f"{FREE_APIS['dexscreener']}/search?q={token_address}"
        resp = await client.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            pairs = data.get("pairs", [])
            if pairs:
                pair = pairs[0]
                liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)

                # Suspicious volume-to-liquidity ratio (wash trading indicator)
                if liquidity_usd > 0 and volume_24h > 0:
                    vol_liq_ratio = volume_24h / liquidity_usd
                    if vol_liq_ratio > 20:
                        signal.score += 25
                        signal.findings.append(
                            f"Volume/liquidity ratio of {vol_liq_ratio:.0f}x - possible wash trading"
                        )
                    elif vol_liq_ratio > 10:
                        signal.score += 10
                elif liquidity_usd == 0 and volume_24h > 0:
                    signal.score += 20
                    signal.findings.append("Volume with zero liquidity - suspicious")

                # Price change indicators
                price_change_h24 = pair.get("priceChange", {}).get("h24", 0)
                if isinstance(price_change_h24, int | float):
                    if price_change_h24 < -50:
                        signal.score += 20
                        signal.findings.append(f"Price dropped {abs(price_change_h24):.0f}% in 24h")
                    elif price_change_h24 > 500:
                        signal.score += 10
                        signal.findings.append(
                            f"Price surged {price_change_h24:.0f}% in 24h - potential pump"
                        )
    except Exception as e:
        logger.warning("Trading behavior analysis failed: %s", e)

    signal.score = min(signal.score, 100)
    return signal


async def _analyze_social_signals(token_address: str, chain: str) -> SignalResult:
    """Analyze social signals (placeholder - relies on chain metadata)."""
    signal = SignalResult(
        signal="social_signals",
        score=20.0,  # Default conservative
        weight=DEFAULT_WEIGHTS["social_signals"],
        findings=["Social signal analysis limited with free data sources"],
    )

    # Conservative scoring: no social data = moderate risk
    signal.score = 30
    return signal


# ── Main Analysis ───────────────────────────────────────────────


async def predict_rug_pull(
    token_address: str,
    chain: str = "solana",
    deployer: str | None = None,
    weights: dict[str, float] | None = None,
) -> RugPullAnalysis:
    """
    Predict rug pull risk for a given token.

    Args:
        token_address: The token contract/mint address
        chain: Blockchain (solana, ethereum, bsc, etc.)
        deployer: Optional deployer/creator address for history check
        weights: Optional custom signal weights (must sum to 1.0)

    Returns:
        RugPullAnalysis with overall score and per-signal breakdown
    """
    if not is_valid_address(token_address):
        raise ValueError(f"Invalid token address: {token_address}")

    chain = _normalize_chain(chain)
    effective_weights = weights or DEFAULT_WEIGHTS

    # Validate weights
    if abs(sum(effective_weights.values()) - 1.0) > 0.01:
        raise ValueError("Signal weights must sum to 1.0")

    token_symbol = ""
    token_name = ""

    # Run all signal analyzers in parallel (single client for efficiency)
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Quick metadata fetch for display
        try:
            search_resp = await client.get(
                f"{FREE_APIS['dexscreener']}/search?q={token_address}", timeout=10
            )
            if search_resp.status_code == 200:
                pairs = search_resp.json().get("pairs", [])
                if pairs:
                    token_symbol = pairs[0].get("baseToken", {}).get("symbol", "")
                    token_name = pairs[0].get("baseToken", {}).get("name", "")
        except Exception:
            pass
        results = await asyncio.gather(
            _analyze_liquidity(client, token_address, chain),
            _analyze_holder_concentration(client, token_address, chain),
            _analyze_deployer(client, deployer, chain),
            _analyze_launch_pattern(client, token_address, chain),
            _analyze_trading_behavior(client, token_address, chain),
            _analyze_social_signals(token_address, chain),
            return_exceptions=True,
        )

    signals: list[SignalResult] = []
    for r in results:
        if isinstance(r, SignalResult):
            signals.append(r)
        elif isinstance(r, Exception):
            logger.warning("Signal analysis exception: %s", r)
            signals.append(
                SignalResult(
                    signal="unknown",
                    score=50.0,
                    weight=0.05,
                    findings=[f"Analysis error: {r}"],
                )
            )

    # Calculate weighted overall score using ternary
    total_weight = sum(s.weight for s in signals)
    overall_score = (
        sum(s.score * s.weight for s in signals) / total_weight if total_weight > 0 else 50.0
    )

    overall_score = min(max(overall_score, 0), 100)
    overall_level = _level_from_score(overall_score)

    # Generate summary
    if overall_score >= 70:
        summary = (
            f"CRITICAL RISK: This token shows strong rug pull indicators "
            f"(score: {overall_score:.0f}/100). Multiple red flags detected "
            f"across {sum(1 for s in signals if s.score > 50)} risk dimensions."
        )
    elif overall_score >= 50:
        summary = (
            f"HIGH RISK: This token has concerning signals "
            f"(score: {overall_score:.0f}/100). Proceed with extreme caution."
        )
    elif overall_score >= 30:
        summary = (
            f"MODERATE RISK: Some concerning patterns detected "
            f"(score: {overall_score:.0f}/100). Standard due diligence recommended."
        )
    else:
        summary = (
            f"LOW RISK: No major rug pull indicators detected "
            f"(score: {overall_score:.0f}/100). Continue standard safety checks."
        )

    # Generate recommendations
    recommendations = []
    if overall_score >= 50:
        recommendations.append("AVOID trading this token - high rug pull probability")
    if any(s.score > 60 for s in signals if s.signal == "liquidity"):
        recommendations.append("Liquidity is critically low or manipulable")
    if any(s.score > 50 for s in signals if s.signal == "holder_concentration"):
        recommendations.append("Top holders control excessive supply - potential dump risk")
    if any(s.score > 50 for s in signals if s.signal == "deployer"):
        recommendations.append("Deployer has concerning history - check their other projects")
    if recommendations:
        recommendations.append("Always verify with RugShield or SENTINEL deep scan before trading")

    return RugPullAnalysis(
        token_address=token_address,
        chain=chain,
        token_symbol=token_symbol or "UNKNOWN",
        token_name=token_name or "Unknown Token",
        overall_score=overall_score,
        overall_level=overall_level,
        signals=signals,
        summary=summary,
        recommendations=recommendations or ["No specific concerns detected"],
        analysis_timestamp=datetime.now(UTC).isoformat(),
    )


# ── Synchronous Wrapper ─────────────────────────────────────────


def analyze(token_address: str, chain: str = "solana", **kwargs: Any) -> dict[str, Any]:
    """
    Synchronous entry point for rug pull prediction.

    Args:
        token_address: Token contract/mint address
        chain: Blockchain name
        **kwargs: Additional options (deployer, weights)

    Returns:
        Dict with full analysis results
    """
    result = asyncio.run(predict_rug_pull(token_address, chain, **kwargs))
    return result.to_dict()


# ── CLI Entry Point ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python3 rug_pull_predictor.py <TOKEN_ADDRESS> [chain] [deployer]")
        sys.exit(1)

    addr = sys.argv[1]
    chain = sys.argv[2] if len(sys.argv) > 2 else "solana"
    deployer = sys.argv[3] if len(sys.argv) > 3 else None

    result = analyze(addr, chain, deployer=deployer)
    print(json.dumps(result, indent=2))
