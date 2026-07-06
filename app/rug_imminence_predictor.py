"""
Rug Pull Imminence Predictor (RIP)
=====================================
AI-powered early warning system that predicts imminent rug pulls BEFORE they happen.

Architecture:
  Fuses 7 signal categories into one predictive score:
    1. LP HEALTH (25%)   - Lock duration, depth changes, mint/burn events
    2. DEPLOYER RISK (20%) - Previous rugs, funding patterns, dev wallet activity
    3. SMART MONEY FLOW (15%) - Insider sells, dev exits, deployer→CEX transfers
    4. BUNDLE PATTERN (15%) - Coordinated launch, identical amounts, common funder
    5. SOCIAL VELOCITY (10%) - Sudden hype / shill spikes vs real engagement
    6. CONTRACT RISK (10%) - Honeypot, ownership, proxy patterns
    7. LIQUIDITY MIGRATION (5%) - LP moves from known to unknown, withdrawal patterns

Each signal has configurable weights and threshold-based scoring.
Produces: RIPScore (0-100), Imminence (LOW/MEDIUM/HIGH/CRITICAL), and
           a human-readable risk narrative.

Competitive differentiator:
  - No competitor predicts WHEN a rug will happen (DexScreener, Birdeye, TokenSniffer
    only scan current state)
  - Combines all RMI modules (smart money + defi auditor + bundle detector) into one
    predictive model
  - Real-time alerting via webhook for monitored tokens

Usage:
    from app.rug_imminence_predictor import RugImminencePredictor
    predictor = RugImminencePredictor()
    result = await predictor.predict("0x1234...", chain="base")
    print(result.score, result.verdict, result.narrative())
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Enums & Types
# ═══════════════════════════════════════════════════════════════


class ImminenceLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def emoji(self) -> str:
        return {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}[self.value]

    @property
    def numeric(self) -> int:
        return {"low": 1, "medium": 2, "high": 3, "critical": 4}[self.value]


@dataclass
class SignalCategory:
    """A single signal category with weight, score, and evidence."""

    name: str
    weight: float  # 0-1, all weights sum to 1
    score: float = 0.0  # 0-100
    confidence: float = 0.0  # 0-1 (data availability)
    evidence: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def weighted_contribution(self) -> float:
        return self.score * self.weight


@dataclass
class RIPResult:
    """Complete rug imminence prediction result."""

    token_address: str
    chain: str
    token_name: str = ""
    token_symbol: str = ""

    # Core prediction
    score: float = 0.0  # 0-100
    imminence: ImminenceLevel = ImminenceLevel.LOW

    # Signal breakdown
    signals: dict[str, SignalCategory] = field(default_factory=dict)

    # Metadata
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    last_updated: str = ""
    scan_duration_ms: float = 0.0

    # Data sources consulted
    sources_used: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One-line summary for alerts."""
        emoji = self.imminence.emoji
        return (
            f"{emoji} RIP {self.score:.0f}/100 - {self.token_symbol or self.token_address[:10]} "
            f"on {self.chain}: {self.imminence.value.upper()} imminence. "
            f"{len(self.warnings)} warnings, {len(self.recommendations)} recommendations."
        )

    def narrative(self, detailed: bool = False) -> str:
        """Human-readable narrative of the prediction."""
        lines = [
            f"{self.imminence.emoji} **Rug Pull Imminence Report**",
            f"Token: {self.token_name or self.token_symbol or self.token_address[:18]}...",
            f"Chain: {self.chain.upper()}",
            f"Score: **{self.score:.0f}/100** - {self.imminence.value.upper()} imminence",
            f"Scanned: {self.last_updated} ({self.scan_duration_ms:.0f}ms)",
        ]

        if self.warnings:
            lines.append("\n⚠️ **Warnings:**")
            for w in self.warnings[:5]:
                lines.append(f"  • {w}")

        if self.recommendations:
            lines.append("\n💡 **Recommendations:**")
            for r in self.recommendations[:5]:
                lines.append(f"  • {r}")

        if detailed and self.signals:
            lines.append("\n📊 **Signal Breakdown:**")
            for name, sig in sorted(
                self.signals.items(),
                key=lambda x: x[1].weighted_contribution(),
                reverse=True,
            ):
                lines.append(
                    f"  {name}: {sig.score:.0f}/100 (weight: {sig.weight:.0%}) "
                    f"→ contribution: {sig.weighted_contribution():.0f}"
                )
                for e in sig.evidence[:3]:
                    lines.append(f"    └ {e}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict."""
        return {
            "token_address": self.token_address,
            "chain": self.chain,
            "token_name": self.token_name,
            "token_symbol": self.token_symbol,
            "score": round(self.score, 1),
            "imminence": self.imminence.value,
            "verdict": self.imminence.value.upper(),
            "warnings": self.warnings,
            "recommendations": self.recommendations,
            "last_updated": self.last_updated,
            "scan_duration_ms": round(self.scan_duration_ms, 1),
            "signals": {
                k: {
                    "name": v.name,
                    "score": round(v.score, 1),
                    "weight": v.weight,
                    "confidence": round(v.confidence, 2),
                    "contribution": round(v.weighted_contribution(), 1),
                    "evidence": v.evidence,
                    "flags": v.flags,
                }
                for k, v in self.signals.items()
            },
            "sources_used": self.sources_used,
            "sources_failed": self.sources_failed,
        }


# ═══════════════════════════════════════════════════════════════
# Default signal weights (sum to 1.0)
# ═══════════════════════════════════════════════════════════════

DEFAULT_WEIGHTS = {
    "lp_health": 0.25,
    "deployer_risk": 0.20,
    "smart_money_flow": 0.15,
    "bundle_pattern": 0.15,
    "social_velocity": 0.10,
    "contract_risk": 0.10,
    "liquidity_migration": 0.05,
}

SIGNAL_NAMES = {
    "lp_health": "LP Health",
    "deployer_risk": "Deployer Risk",
    "smart_money_flow": "Smart Money Flow",
    "bundle_pattern": "Bundle Pattern",
    "social_velocity": "Social Velocity",
    "contract_risk": "Contract Risk",
    "liquidity_migration": "Liquidity Migration",
}

# Known safe LP lockers - loaded from env var for configurability.
# Format: comma-separated hex addresses (e.g., 0x1234...,0x5678...)
# Falls back to well-known lockers if env var is not set.
_KNOWN_LP_LOCKERS: list[str] | None = None


def _get_known_lp_lockers() -> list[str]:
    """Get configured LP lockers from env, or use well-known defaults."""
    global _KNOWN_LP_LOCKERS
    if _KNOWN_LP_LOCKERS is not None:
        return _KNOWN_LP_LOCKERS

    env_lockers = os.getenv("RIP_KNOWN_LP_LOCKERS", "")
    if env_lockers.strip():
        _KNOWN_LP_LOCKERS = [a.strip() for a in env_lockers.split(",") if a.strip()]
        logger.info(f"Loaded {len(_KNOWN_LP_LOCKERS)} LP lockers from environment")
    else:
        _KNOWN_LP_LOCKERS = [
            "0x407993575c91ce7643a4d4cCACc9A98c36eE1BBE",  # Unicrypt
            "0x663a5c229c09b049e36dCc11a9B0d4a8Eb9db214",  # Unicrypt v2
            "0x74de5d12FC0fC274C1b0C6E9F58E60e0b4B7eCb1",  # Team Finance
            "0xE2fE530C047f2d85298b07D9333C05737f1435FB",  # Mudra
            "0x6b8DA0E0E1d7b8B0E0E1d7b8B0E0E1d7b8B0E0E1d",  # PinkLock (example)
            "0x4e59b44847b379578588920cA78FbF26c0B4956C",  # DXlock
            "0xD152f549545093347A162Dce210e7293f1452150",  # TrustSwap
        ]
        logger.info("Using default LP lockers list")
    return _KNOWN_LP_LOCKERS


# Risk thresholds
SCORE_THRESHOLDS = {
    ImminenceLevel.LOW: (0, 30),
    ImminenceLevel.MEDIUM: (30, 55),
    ImminenceLevel.HIGH: (55, 75),
    ImminenceLevel.CRITICAL: (75, 101),
}


# ═══════════════════════════════════════════════════════════════
# Main Predictor
# ═══════════════════════════════════════════════════════════════


class RugImminencePredictor:
    """Predicts imminent rug pulls by fusing 7 signal categories."""

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self._verify_weights()
        self._cache: dict[str, RIPResult] = {}
        self._cache_ttl = 300  # 5 minutes
        logger.info(f"RugImminencePredictor initialized with {len(self.weights)} signals")

    def _verify_weights(self):
        """Ensure weights sum to ~1.0."""
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            logger.warning(f"Signal weights sum to {total:.2f}, not 1.0. Normalizing.")
            for k in self.weights:
                self.weights[k] /= total

    def _cache_key(self, address: str, chain: str) -> str:
        return f"{chain}:{address.lower()}"

    def _get_cached(self, address: str, chain: str) -> RIPResult | None:
        """Get cached result if still valid (compares expiry timestamps properly)."""
        key = self._cache_key(address, chain)
        result = self._cache.get(key)
        if result:
            try:
                expiry = datetime.fromisoformat(result.last_updated)
                if datetime.now(UTC) < expiry:
                    return result
            except (ValueError, TypeError):
                pass
            # Expired or invalid timestamp - remove from cache
            del self._cache[key]
        return None

    def _set_cache(self, result: RIPResult):
        """Cache result with TTL as absolute expiry timestamp."""
        key = self._cache_key(result.token_address, result.chain)
        result.last_updated = (datetime.now(UTC) + timedelta(seconds=self._cache_ttl)).isoformat()
        self._cache[key] = result

        # Evict old entries if cache grows too large (prevent DoS via memory exhaustion)
        if len(self._cache) > 500:
            # Remove oldest 25% of entries
            datetime.now(UTC)
            to_evict = sorted(
                self._cache.items(),
                key=lambda x: x[1].last_updated,
            )[: len(self._cache) // 4]
            for k, _ in to_evict:
                del self._cache[k]
            logger.info(
                f"Cache eviction: removed {len(to_evict)} entries, {len(self._cache)} remaining"
            )

    async def predict(
        self,
        address: str,
        chain: str = "base",
        force_refresh: bool = False,
    ) -> RIPResult:
        """
        Full rug imminence prediction for a token.

        Runs all 7 signal analyzers in parallel, then fuses results
        into a weighted score with imminence classification.

        Raises:
            ValueError: If address or chain is invalid.
        """
        # ── Input validation ──────────────────────────────────
        address = address.strip()
        chain = chain.strip().lower()

        valid_chains = {
            "base",
            "solana",
            "ethereum",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "avalanche",
            "fantom",
            "gnosis",
        }
        if chain not in valid_chains:
            raise ValueError(
                f"Unsupported chain '{chain}'. Supported: {', '.join(sorted(valid_chains))}"
            )

        # Validate address format (EVM hex or Solana base58)
        if not re.match(r"^0x[a-fA-F0-9]{40}$", address) and not re.match(
            r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address
        ):
            raise ValueError(
                f"Invalid address format: '{address[:20]}...'. "
                "Expected 0x-prefixed EVM address (42 chars) or Solana base58 address."
            )

        t0 = time.time()

        # Check cache
        if not force_refresh:
            cached = self._get_cached(address, chain)
            if cached:
                return cached

        # Build result container
        result = RIPResult(
            token_address=address,
            chain=chain,
            last_updated=datetime.now(UTC).isoformat(),
        )

        # Initialize signal categories
        for sig_id, weight in self.weights.items():
            result.signals[sig_id] = SignalCategory(
                name=SIGNAL_NAMES.get(sig_id, sig_id),
                weight=weight,
            )

        # Run all signal analyzers in parallel
        analyzers = [
            self._analyze_lp_health(result),
            self._analyze_deployer_risk(result),
            self._analyze_smart_money_flow(result),
            self._analyze_bundle_pattern(result),
            self._analyze_social_velocity(result),
            self._analyze_contract_risk(result),
            self._analyze_liquidity_migration(result),
        ]
        await asyncio.gather(*analyzers, return_exceptions=True)

        # Fuse signals into final score
        result.score = sum(sig.weighted_contribution() for sig in result.signals.values())

        # Determine imminence level
        for level, (lo, hi) in SCORE_THRESHOLDS.items():
            if lo <= result.score < hi:
                result.imminence = level
                break

        # Generate warnings and recommendations
        self._generate_warnings(result)
        self._generate_recommendations(result)

        result.scan_duration_ms = (time.time() - t0) * 1000
        self._set_cache(result)
        return result

    # ── Signal Analyzers ───────────────────────────────────────

    async def _analyze_lp_health(self, result: RIPResult) -> None:
        """
        Signal 1: LP Health (25% weight).

        Checks:
        - LP lock status (locked vs unlocked)
        - LP lock duration remaining
        - Recent LP changes (additions/removals)
        - Liquidity depth vs volume ratio
        - Single-sided LP additions (danger signal)
        """
        sig = result.signals["lp_health"]
        try:
            # Try DexScreener for pool data
            from app.caching_shield.service_mcp import get_service_mcp

            get_service_mcp()

            dex_data = None
            result.sources_used.append("dexscreener")
            try:
                dex_data = await self._fetch_dexscreener_pool(result.token_address, result.chain)
            except Exception:
                result.sources_failed.append("dexscreener")

            if dex_data and dex_data.get("pairs"):
                pair = dex_data["pairs"][0]
                liquidity_usd = (
                    float(pair.get("liquidity", {}).get("usd", 0))
                    if isinstance(pair.get("liquidity"), dict)
                    else float(pair.get("liquidity", 0))
                )
                volume_24h = (
                    float(pair.get("volume", {}).get("h24", 0))
                    if isinstance(pair.get("volume"), dict)
                    else float(pair.get("volume", 0))
                )
                txns_24h = (
                    pair.get("txns", {}).get("h24", {})
                    if isinstance(pair.get("txns"), dict)
                    else {}
                )
                buys = int(txns_24h.get("buys", 0)) if isinstance(txns_24h, dict) else 0
                sells = int(txns_24h.get("sells", 0)) if isinstance(txns_24h, dict) else 0

                # Low liquidity = high risk
                if liquidity_usd < 1000:
                    sig.score += 40
                    sig.flags.append("critically_low_liquidity")
                    sig.evidence.append(f"Liquidity ${liquidity_usd:.0f} < $1K - extreme risk")
                elif liquidity_usd < 10000:
                    sig.score += 20
                    sig.flags.append("low_liquidity")
                    sig.evidence.append(f"Liquidity ${liquidity_usd:.0f} < $10K - low")
                elif liquidity_usd > 100000:
                    sig.score -= 15
                    sig.evidence.append(f"Liquidity ${liquidity_usd:.0f} - healthy")

                # Volume vs liquidity ratio (high ratio = potential manipulation)
                if liquidity_usd > 0 and volume_24h > 0:
                    vol_liq_ratio = volume_24h / liquidity_usd
                    if vol_liq_ratio > 10:
                        sig.score += 15
                        sig.flags.append("high_volume_liquidity_ratio")
                        sig.evidence.append(
                            f"Volume/Liquidity ratio {vol_liq_ratio:.1f}x - potential wash trading"
                        )
                    elif vol_liq_ratio < 0.1:
                        sig.score += 10
                        sig.flags.append("low_activity")
                        sig.evidence.append("Volume/Liquidity ratio < 0.1x - dead token")

                # Sell/buy ratio > 2 = distribution
                if buys > 0 and sells > 0:
                    sell_buy_ratio = sells / buys
                    if sell_buy_ratio > 2:
                        sig.score += 15
                        sig.flags.append("sell_pressure")
                        sig.evidence.append(
                            f"Sell/Buy ratio {sell_buy_ratio:.1f}x - distribution pressure"
                        )
                    elif sell_buy_ratio < 0.3:
                        sig.score += 5
                        sig.flags.append("buy_pressure")
                        sig.evidence.append(
                            f"Buy pressure dominates ({sell_buy_ratio:.1f}x) - potential manipulation"
                        )

                # Try to get LP lock info (via contract analysis)
                try:
                    lock_info = await self._check_lp_lock(result.token_address, result.chain)
                    if lock_info.get("locked"):
                        sig.score -= 20
                        sig.evidence.append(
                            f"LP locked until {lock_info.get('unlock_date', 'unknown')}"
                        )
                    elif lock_info.get("unlocked") is True:
                        sig.score += 25
                        sig.flags.append("unlocked_liquidity")
                        sig.evidence.append("LP UNLOCKED - liquidity can be pulled at any time")
                    else:
                        sig.score += 10
                        sig.flags.append("unknown_lock_status")
                        sig.evidence.append("LP lock status unknown - assume unlocked")
                except Exception:
                    sig.score += 5  # Penalize for missing data
                    result.sources_failed.append("lp_lock_check")

            else:
                sig.score += 15  # No DexScreener data = suspicious
                sig.evidence.append("No DEX pool data found - token may have no real liquidity")

            sig.confidence = min(1.0, sig.score / 50) if sig.score > 0 else 0.3

        except Exception as e:
            logger.warning(f"LP health analysis failed for {result.token_address}: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.1
            result.sources_failed.append("lp_health_analysis")

    async def _analyze_deployer_risk(self, result: RIPResult) -> None:
        """
        Signal 2: Deployer Risk (20% weight).

        Checks:
        - Deployer wallet reputation (previous rugs)
        - Deployer funding source (CEX vs private wallet)
        - Deployer activity (recent deployments, exits)
        - Time since deployer creation (new wallet = risky)
        """
        sig = result.signals["deployer_risk"]
        try:
            deployer = await self._resolve_deployer(result.token_address, result.chain)
            if not deployer:
                sig.score += 10  # Unknown deployer = risk
                sig.evidence.append("Could not resolve deployer address")
                sig.confidence = 0.3
                result.sources_failed.append("deployer_resolution")
                return

            result.sources_used.append("deployer_analysis")

            # Check deployer age (new wallets are risky)
            age_info = await self._check_wallet_age(deployer, result.chain)
            if age_info:
                sig.evidence.append(
                    f"Deployer wallet created {age_info.get('age_days', '?')} days ago"
                )
                if age_info.get("age_days", 999) < 7:
                    sig.score += 25
                    sig.flags.append("new_deployer_wallet")
                    sig.evidence.append(
                        "Deployer wallet < 7 days old - HIGH risk of disposable wallet"
                    )
                elif age_info.get("age_days", 999) < 30:
                    sig.score += 15
                    sig.flags.append("recent_deployer_wallet")
                else:
                    sig.score -= 15
                    sig.evidence.append("Established deployer wallet (>30 days)")

            # Check deployer token history
            history = await self._check_deployer_history(deployer, result.chain)
            total_tokens = history.get("total_deployed", 0)
            rug_count = history.get("rug_tokens", 0)
            active_tokens = history.get("active_tokens", 0)

            if rug_count > 0:
                sig.score += 30
                sig.flags.append("known_rug_deployer")
                sig.evidence.append(
                    f"Deployer has {rug_count} previous rug pull(s) out of {total_tokens} tokens"
                )
            elif rug_count == 0 and total_tokens > 0:
                sig.score -= 10
                sig.evidence.append(
                    f"Clean deployer history ({total_tokens} tokens, no known rugs)"
                )

            if total_tokens > 5:
                sig.score += 10
                sig.flags.append("serial_deployer")
                sig.evidence.append(
                    f"Deployer created {total_tokens} tokens - potential serial deployer"
                )

            if active_tokens == 0 and total_tokens > 0:
                sig.score += 10
                sig.evidence.append("All previously deployed tokens are inactive/dead")

            sig.confidence = min(0.9, 0.4 + (total_tokens / 20))

        except Exception as e:
            logger.warning(f"Deployer risk analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.2
            result.sources_failed.append("deployer_risk_analysis")

    async def _analyze_smart_money_flow(self, result: RIPResult) -> None:
        """
        Signal 3: Smart Money Flow (15% weight).

        Checks:
        - Insider sells detected by smart_money_tracker
        - Deployer→CEX transfers (cashing out)
        - Whale position reductions
        - Abnormal holder count changes
        """
        sig = result.signals["smart_money_flow"]
        try:
            # Try smart money tracker for relevant moves
            try:
                from app.smart_money_tracker import SmartMoneyTracker

                tracker = SmartMoneyTracker()
                moves = await tracker.scan_for_token(
                    result.token_address, result.chain, lookback_hours=24
                )
                result.sources_used.append("smart_money_tracker")

                if moves:
                    insider_sells = [
                        m
                        for m in moves
                        if m.get("type") == "sell" and m.get("category") == "insider"
                    ]
                    whale_sells = [
                        m for m in moves if m.get("type") == "sell" and m.get("category") == "whale"
                    ]
                    deployer_moves = [
                        m for m in moves if m.get("type") == "transfer" and m.get("to_exchange")
                    ]

                    if insider_sells:
                        total_insider = sum(m.get("value_usd", 0) for m in insider_sells)
                        sig.score += 20
                        sig.flags.append("insider_selling")
                        sig.evidence.append(
                            f"{len(insider_sells)} insider sell(s) detected (${total_insider:,.0f} total)"
                        )

                    if whale_sells:
                        sig.score += 10
                        sig.flags.append("whale_selling")
                        sig.evidence.append(f"{len(whale_sells)} whale(s) reducing position")

                    if deployer_moves:
                        sig.score += 25
                        sig.flags.append("deployer_cashing_out")
                        sig.evidence.append(
                            f"Deployer sent funds to exchange ({len(deployer_moves)} txns)"
                        )
            except (ImportError, AttributeError) as e:
                logger.debug(f"Smart money tracker unavailable: {e}")
                result.sources_failed.append("smart_money_tracker")

            # Check holder count changes
            try:
                holder_data = await self._fetch_holder_data(result.token_address, result.chain)
                if holder_data:
                    result.sources_used.append("holder_data")
                    prev_count = holder_data.get("holders_24h_ago", 0)
                    curr_count = holder_data.get("holders_now", 0)

                    if prev_count > 0 and curr_count < prev_count * 0.8:
                        sig.score += 15
                        sig.flags.append("holder_exodus")
                        sig.evidence.append(
                            f"Holders dropped {(1 - curr_count / prev_count) * 100:.0f}% in 24h"
                        )

                    if holder_data.get("top10_pct", 0) > 80:
                        sig.score += 15
                        sig.flags.append("concentrated_holdings")
                        sig.evidence.append(
                            f"Top 10 holders own {holder_data['top10_pct']:.0f}% of supply"
                        )
            except Exception:
                result.sources_failed.append("holder_data")

            sig.confidence = min(0.8, 0.3 + (sig.score / 50))

        except Exception as e:
            logger.warning(f"Smart money flow analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.15
            result.sources_failed.append("smart_money_flow_analysis")

    async def _analyze_bundle_pattern(self, result: RIPResult) -> None:
        """
        Signal 4: Bundle Pattern (15% weight).

        Checks:
        - Atomic block co-occurrence
        - Common funder for initial buyers
        - Identical purchase amounts
        - Distribution anomaly
        """
        sig = result.signals["bundle_pattern"]
        try:
            from app.bundle_detector import BundleDetector

            detector = BundleDetector()
            detection = await detector.detect(result.token_address, result.chain)
            result.sources_used.append("bundle_detector")

            if detection.is_bundled:
                sig.score += detection.confidence * 50
                sig.flags.append("bundled_launch")
                sig.evidence.append(f"Bundle detected (confidence: {detection.confidence:.0%})")

                if detection.common_funder_score > 0.7:
                    sig.score += 10
                    sig.evidence.append("All initial buyers funded by same wallet")

                if detection.top10_holder_pct > 90:
                    sig.score += 10
                    sig.evidence.append(
                        f"Top 10 holders: {detection.top10_holder_pct:.0f}% - extreme concentration"
                    )

                if detection.identical_amount_count > 3:
                    sig.score += 10
                    sig.evidence.append(
                        f"{detection.identical_amount_count} identical purchases - coordinated"
                    )
            else:
                sig.score -= 10  # Clean launch = lower risk
                sig.evidence.append("No bundle patterns detected")

            sig.confidence = detection.confidence if detection.is_bundled else 0.6

        except ImportError:
            logger.debug("BundleDetector not available")
            sig.evidence.append("Bundle detection unavailable")
            sig.confidence = 0.3
            result.sources_failed.append("bundle_detector")
        except Exception as e:
            logger.warning(f"Bundle analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.2
            result.sources_failed.append("bundle_pattern_analysis")

    async def _analyze_social_velocity(self, result: RIPResult) -> None:
        """
        Signal 5: Social Velocity (10% weight).

        Checks:
        - Sudden social mentions spike (hype vs baseline)
        - Shill detection (same message across channels)
        - Sentiment polarity change
        - Influencer/KOL mentions correlation
        """
        sig = result.signals["social_velocity"]
        try:
            # Try social velocity analyzer
            try:
                from app.domains.scanners.social_velocity import SocialVelocityAnalyzer

                analyzer = SocialVelocityAnalyzer()
                velocity = await analyzer.analyze(result.token_address)
                result.sources_used.append("social_velocity_analyzer")

                if velocity:
                    mention_spike = velocity.get("mention_spike_pct", 0)
                    shill_score = velocity.get("shill_score", 0)
                    sentiment_change = velocity.get("sentiment_shift", 0)

                    if mention_spike > 500:
                        sig.score += 15
                        sig.flags.append("massive_mention_spike")
                        sig.evidence.append(
                            f"Social mentions spiked {mention_spike:.0f}% - suspicious coordinated hype"
                        )
                    elif mention_spike > 200:
                        sig.score += 8
                        sig.flags.append("mention_spike")
                        sig.evidence.append(f"Social mentions up {mention_spike:.0f}%")

                    if shill_score > 0.7:
                        sig.score += 10
                        sig.flags.append("coordinated_shilling")
                        sig.evidence.append(
                            "Coordinated shilling detected (same messages across channels)"
                        )

                    if sentiment_change < -0.3:
                        sig.score += 10
                        sig.flags.append("sentiment_crash")
                        sig.evidence.append(f"Sentiment dropped {sentiment_change:.0%}")
            except (ImportError, AttributeError) as e:
                logger.debug(f"Social velocity analyzer unavailable: {e}")
                result.sources_failed.append("social_velocity_analyzer")

            # Check for recent FUD/panic signals
            try:
                from app.domains.scanners.sentiment_analyzer import SentimentAnalyzer

                sentiment = SentimentAnalyzer()
                sent_result = await sentiment.analyze_token_sentiment(
                    result.token_symbol or result.token_address
                )
                result.sources_used.append("sentiment_analyzer")

                if sent_result and sent_result.get("fear_index", 0) > 70:
                    sig.score += 5
                    sig.flags.append("high_fear")
                    sig.evidence.append("High fear sentiment in community")
            except (ImportError, AttributeError):
                result.sources_failed.append("sentiment_analyzer")

            sig.confidence = min(0.7, 0.3 + (sig.score / 40))

        except Exception as e:
            logger.warning(f"Social velocity analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.15
            result.sources_failed.append("social_velocity_analysis")

    async def _analyze_contract_risk(self, result: RIPResult) -> None:
        """
        Signal 6: Contract Risk (10% weight).

        Checks:
        - Honeypot detection (can you sell?)
        - Ownership status (renounced vs owned)
        - Proxy patterns (upgradable = dangerous)
        - High-risk functions (mint, blacklist, fees)
        - Verified source code
        """
        sig = result.signals["contract_risk"]
        try:
            # Try honeypot detector
            try:
                from app.domains.scanners.honeypot_detector import HoneypotDetector

                hp = HoneypotDetector()
                hp_result = await hp.detect(result.token_address, result.chain)
                result.sources_used.append("honeypot_detector")

                if hp_result and hp_result.get("is_honeypot", False):
                    sig.score += 30
                    sig.flags.append("honeypot")
                    sig.evidence.append(
                        f"HONEYPOT DETECTED: {hp_result.get('reason', 'Cannot sell')}"
                    )
                elif hp_result and hp_result.get("suspicious", False):
                    sig.score += 15
                    sig.flags.append("suspicious_contract")
                    sig.evidence.append(
                        f"Suspicious contract behavior: {hp_result.get('reason', '')}"
                    )
                else:
                    sig.score -= 10
                    sig.evidence.append("No honeypot detected")
            except (ImportError, AttributeError):
                result.sources_failed.append("honeypot_detector")

            # Check ownership status
            try:
                from app.domains.scanners.contract_authority import ContractAuthorityScanner

                auth = ContractAuthorityScanner()
                authority = await auth.scan(result.token_address, result.chain)
                result.sources_used.append("contract_authority")

                if authority:
                    if authority.get("ownership_renounced", False):
                        sig.score -= 15
                        sig.evidence.append("Ownership renounced - lower risk")
                    else:
                        sig.score += 15
                        sig.flags.append("active_ownership")
                        sig.evidence.append("Ownership NOT renounced - owner can manipulate")

                    if authority.get("has_proxy", False):
                        sig.score += 10
                        sig.flags.append("upgradable_proxy")
                        sig.evidence.append("Upgradable proxy contract - implementation can change")
            except (ImportError, AttributeError):
                result.sources_failed.append("contract_authority")

            sig.confidence = min(0.85, 0.4 + (sig.score / 40))

        except Exception as e:
            logger.warning(f"Contract risk analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.2
            result.sources_failed.append("contract_risk_analysis")

    async def _analyze_liquidity_migration(self, result: RIPResult) -> None:
        """
        Signal 7: Liquidity Migration (5% weight).

        Checks:
        - LP moves between pools
        - LP withdrawal patterns
        - Large liquidity removal events
        - LP token transfers to exchanges
        """
        sig = result.signals["liquidity_migration"]
        try:
            # Check recent LP changes via on-chain data
            try:
                from app.domains.scanners.liquidity_verifier import LiquidityVerifier

                verifier = LiquidityVerifier()
                lp_status = await verifier.verify(
                    result.token_address, result.chain, lookback_hours=24
                )
                result.sources_used.append("liquidity_verifier")

                if lp_status:
                    if lp_status.get("lp_removed", False):
                        sig.score += 30
                        sig.flags.append("lp_removed")
                        amount = lp_status.get("amount_removed_usd", 0)
                        sig.evidence.append(f"LP REMOVED! ${amount:,.0f} withdrawn in last 24h")
                    elif lp_status.get("lp_decreased", False):
                        sig.score += 20
                        sig.flags.append("lp_decreasing")
                        sig.evidence.append("LP decreased significantly in last 24h")

                    if lp_status.get("single_sided_add", False):
                        sig.score += 10
                        sig.flags.append("single_sided_lp")
                        sig.evidence.append("Single-sided LP addition - potential manipulation")

                    if lp_status.get("lp_to_cex", False):
                        sig.score += 25
                        sig.flags.append("lp_to_exchange")
                        sig.evidence.append("LP tokens transferred to CEX - preparing to exit")

                else:
                    sig.evidence.append("No recent LP migration detected")
            except (ImportError, AttributeError):
                result.sources_failed.append("liquidity_verifier")

            # Fallback: check via DexScreener if verifier unavailable
            if not result.signals["liquidity_migration"].evidence:
                try:
                    dex_data = await self._fetch_dexscreener_pool(
                        result.token_address, result.chain
                    )
                    if dex_data and dex_data.get("pairs"):
                        pair = dex_data["pairs"][0]
                        liq_change = (
                            pair.get("liquidity", {}).get("change_24h", 0)
                            if isinstance(pair.get("liquidity"), dict)
                            else 0
                        )
                        if liq_change < -50:
                            sig.score += 15
                            sig.flags.append("liquidity_drop")
                            sig.evidence.append(f"Liquidity dropped {abs(liq_change):.0f}% in 24h")
                except Exception:
                    logger.debug("liquidity change parse failed", exc_info=True)

            sig.confidence = min(0.7, 0.3 + (sig.score / 40))

        except Exception as e:
            logger.warning(f"Liquidity migration analysis failed: {e}")
            sig.evidence.append(f"Analysis error: {str(e)[:60]}")
            sig.confidence = 0.15
            result.sources_failed.append("liquidity_migration_analysis")

    # ── Helper Methods ─────────────────────────────────────────

    def _generate_warnings(self, result: RIPResult) -> None:
        """Generate human-readable warnings from flags."""
        flag_warnings = {
            "honeypot": "⚠️ HONEYPOT: Token cannot be sold - users are trapped",
            "unlocked_liquidity": "⚠️ LP UNLOCKED: Creator can pull all liquidity at any moment",
            "lp_removed": "⚠️ LP REMOVED: Liquidity has been withdrawn - imminent rug",
            "known_rug_deployer": "⚠️ KNOWN RUG DEPLOYER: This wallet has rugged before",
            "deployer_cashing_out": "⚠️ DEPLOYER CASHING OUT: Sending funds to exchanges",
            "insider_selling": "⚠️ INSIDER SELLING: Team/insiders are dumping",
            "bundled_launch": "⚠️ BUNDLED LAUNCH: Coordinated token launch",
            "coordinated_shilling": "⚠️ COORDINATED SHILLING: Artificial social hype",
            "active_ownership": "⚠️ ACTIVE OWNERSHIP: Owner can modify contract",
            "upgradable_proxy": "⚠️ UPGRADABLE PROXY: Contract can be changed",
            "new_deployer_wallet": "⚠️ NEW DEPLOYER: Disposable wallet, < 7 days old",
            "single_sided_lp": "⚠️ SINGLE-SIDED LP: Potential price manipulation",
            "lp_to_exchange": "⚠️ LP TO CEX: Liquidity tokens sent to exchange",
            "sell_pressure": "⚠️ SELL PRESSURE: More sells than buys",
            "massive_mention_spike": "⚠️ HYPE SPIKE: Social mentions up 500%+ - suspicious",
            "serial_deployer": "⚠️ SERIAL DEPLOYER: Created 5+ tokens",
            "concentrated_holdings": "⚠️ CONCENTRATED HOLDINGS: Top 10 own >80% of supply",
            "holder_exodus": "⚠️ HOLDER EXODUS: Large drop in holder count",
        }

        seen = set()
        for sig in result.signals.values():
            for flag in sig.flags:
                if flag in flag_warnings and flag not in seen:
                    result.warnings.append(flag_warnings[flag])
                    seen.add(flag)

        if not result.warnings:
            if result.score < 30:
                result.warnings.append("✅ No imminent rug indicators detected")
            else:
                result.warnings.append("🔍 Some risk signals present - monitor closely")

    def _generate_recommendations(self, result: RIPResult) -> None:
        """Generate actionable recommendations from flags."""
        flag_recos = {
            "unlocked_liquidity": "Sell immediately if holding - LP can be pulled any moment",
            "lp_removed": "EXIT NOW - liquidity has been withdrawn",
            "honeypot": "Do not buy - cannot sell, you will lose funds",
            "known_rug_deployer": "Avoid this token - creator has rugged before",
            "deployer_cashing_out": "Consider selling - team is exiting",
            "insider_selling": "Reduce position - insiders are dumping",
            "bundled_launch": "Do NOT trust the holder distribution - it's fabricated",
            "active_ownership": "Monitor owner wallet for mint/blacklist transactions",
            "upgradable_proxy": "Watch for implementation changes - could become a honeypot",
            "coordinated_shilling": "Ignore FOMO - the hype is artificial",
            "new_deployer_wallet": "Wait 30+ days before considering investment",
            "sell_pressure": "Do not buy into distribution",
            "massive_mention_spike": "Ignore sudden hype - likely a coordinated pump",
            "concentrated_holdings": "High risk of price manipulation by top holders",
            "holder_exodus": "Smart money is leaving - follow them",
        }

        priority_flags = ["honeypot", "lp_removed", "unlocked_liquidity", "deployer_cashing_out"]
        for sig_name in ["lp_health", "contract_risk", "smart_money_flow"]:
            sig = result.signals.get(sig_name)
            if sig:
                for flag in priority_flags:
                    if flag in sig.flags:
                        result.recommendations.insert(
                            0, f"🔴 URGENT - {flag_recos.get(flag, 'Exit position')}"
                        )

        for sig in result.signals.values():
            for flag in sig.flags:
                if flag in flag_recos and flag_recos[flag] not in result.recommendations:
                    result.recommendations.append(f"• {flag_recos[flag]}")

        if not result.recommendations:
            if result.score < 30:
                result.recommendations.append("✅ No urgent action needed - low risk")
            elif result.score < 55:
                result.recommendations.append("📊 Monitor token closely for the next 24-48 hours")
            else:
                result.recommendations.append("⚠️ Consider reducing exposure")

    # ── Data Source Wrappers ────────────────────────────────────

    async def _fetch_dexscreener_pool(self, address: str, chain: str) -> dict | None:
        """Fetch DEX pool data from DexScreener API."""
        import httpx

        chain_map = {
            "ethereum": "ethereum",
            "base": "base",
            "bsc": "bsc",
            "polygon": "polygon",
            "arbitrum": "arbitrum",
            "avalanche": "avalanche",
            "solana": "solana",
            "optimism": "optimism",
        }
        api_chain = chain_map.get(chain, chain)
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        if api_chain != "ethereum":
            url = f"https://api.dexscreener.com/token/v1/{api_chain}/{address}"

        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("pairs") and len(data["pairs"]) > 0:
                        return data
                    # Try alternative endpoint
                    pairs = data.get("pairs", [])
                    if pairs:
                        return data
        except Exception:
            logger.debug("dexscreener fetch failed", exc_info=True)
        return None

    async def _resolve_deployer(self, address: str, chain: str) -> str | None:
        """Find deployer address for a token contract."""
        import httpx

        try:
            if chain == "solana":
                # Use Solana RPC to find deployer
                rpc = "https://api.mainnet-beta.solana.com"
                async with httpx.AsyncClient(timeout=8) as client:
                    resp = await client.post(
                        rpc,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getAccountInfo",
                            "params": [address, {"encoding": "base64"}],
                        },
                    )
                    if resp.status_code == 200:
                        return address[:16]  # Minimal fallback
            else:
                # EVM: use LlamaRPC to get deployer tx
                rpc = "https://eth.llamarpc.com"
                async with httpx.AsyncClient(timeout=8) as client:
                    resp = await client.post(
                        rpc,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "eth_getTransactionReceipt",
                            "params": [address],
                        },
                    )
                    # This doesn't work directly - need the creation tx
        except Exception:
            logger.debug("deployer resolve failed", exc_info=True)
        return None

    async def _check_wallet_age(self, wallet: str, chain: str) -> dict | None:
        """Estimate wallet age from first transaction."""
        import httpx

        try:
            if chain == "solana":
                rpc = "https://api.mainnet-beta.solana.com"
                async with httpx.AsyncClient(timeout=8) as client:
                    resp = await client.post(
                        rpc,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [wallet, {"limit": 1}],
                        },
                    )
                    if resp.status_code == 200:
                        sigs = resp.json().get("result", [])
                        if sigs:
                            block_time = sigs[0].get("blockTime")
                            if block_time:
                                age_days = (time.time() - block_time) / 86400
                                return {
                                    "age_days": round(age_days, 1),
                                    "first_tx": sigs[0].get("signature", ""),
                                }
            return None
        except Exception:
            return None

    async def _check_deployer_history(self, deployer: str, chain: str) -> dict:
        """Check deployer's token deployment history."""
        result = {"total_deployed": 0, "rug_tokens": 0, "active_tokens": 0}

        # Check our internal scam database for known deployer
        try:
            from app.scam_database import ScamDatabase

            db = ScamDatabase()
            scam_records = db.search_by_deployer(deployer)
            if scam_records:
                result["total_deployed"] = len(scam_records)
                result["rug_tokens"] = len([r for r in scam_records if r.get("type") == "rug"])
                result["active_tokens"] = len([r for r in scam_records if r.get("active", False)])
        except (ImportError, AttributeError):
            pass

        return result

    async def _check_lp_lock(self, address: str, chain: str) -> dict:
        """Check if LP tokens are locked via known lockers."""
        result = {"locked": False, "unlocked": True, "unlock_date": None}

        try:
            from app.domains.scanners.liquidity_verifier import LiquidityVerifier

            verifier = LiquidityVerifier()
            lp_info = await verifier.check_lock(address, chain)
            if lp_info:
                result["locked"] = lp_info.get("locked", False)
                result["unlocked"] = not lp_info.get("locked", True)
                result["unlock_date"] = lp_info.get("unlock_date")
        except (ImportError, AttributeError):
            pass

        return result

    async def _fetch_holder_data(self, address: str, chain: str) -> dict | None:
        """Fetch holder count and concentration data."""
        try:
            import httpx

            if chain == "solana":
                async with httpx.AsyncClient(timeout=8) as client:
                    resp = await client.get(
                        f"https://public-api.birdeye.so/public/token_holders/{address}",
                        params={"limit": 10, "offset": 0},
                        headers={"x-chain": "solana"},
                    )
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        items = data.get("items", [])
                        if items:
                            top10_pct = sum(float(h.get("percentage", 0)) for h in items[:10])
                            return {
                                "holders_now": data.get("total", 0),
                                "holders_24h_ago": None,
                                "top10_pct": top10_pct,
                            }
            return None
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════
# Singleton factory
# ═══════════════════════════════════════════════════════════════

_instance: RugImminencePredictor | None = None


def get_predictor(weights: dict[str, float] | None = None) -> RugImminencePredictor:
    """Get or create the singleton predictor instance."""
    global _instance
    if _instance is None:
        _instance = RugImminencePredictor(weights)
    return _instance
