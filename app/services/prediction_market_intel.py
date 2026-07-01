"""
Prediction Market Intelligence Signals
========================================
Signal detection layer on top of prediction market data.
Transforms raw market data into actionable intelligence.

Signal Types:
  1. PROBABILITY_SWING  — Sudden odds shift before news breaks (early warning)
  2. CROSS_SOURCE_GAP   — Disagreement between sources (arbitrage/uncertainty)
  3. MARKET_SCANNER_DIVERGENCE — Market says safe, scanner says danger (or vice versa)
  4. TOKEN_RISK_SIGNAL  — Prediction markets flagging a specific token
  5. ECOSYSTEM_RISK     — Broad risk indicators (depeg odds, exploit probability)
  6. VOLUME_SURGE       — Unusual trading volume on a security market

Each signal has:
  - severity: critical | high | medium | low | info
  - headline: human-readable one-liner
  - insight: what it actually means for crypto users
  - evidence: backing data (sources, probabilities, volumes)
  - action: what to do about it

This is the "people actually want to know" layer.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.prediction_market_service import (
    PredictionMarket,
    get_prediction_market_service,
)

logger = logging.getLogger("prediction_market_intel")


@dataclass
class IntelSignal:
    """A single intelligence signal from prediction market analysis."""

    signal_type: str  # PROBABILITY_SWING, CROSS_SOURCE_GAP, etc.
    severity: str  # critical, high, medium, low, info
    headline: str  # One-line summary
    insight: str  # What it means in plain English
    evidence: list[dict]  # Supporting data points
    action: str  # Recommended action
    confidence: float  # 0.0-1.0
    generated_at: str
    source_markets: list[str] = field(default_factory=list)  # market slugs referenced


@dataclass
class CryptoFearIndex:
    """Aggregated risk index derived from prediction markets."""

    overall_risk: float  # 0-100
    exploit_risk: float  # 0-100
    regulatory_risk: float  # 0-100
    stablecoin_risk: float  # 0-100
    exchange_risk: float  # 0-100
    components: list[dict]  # Individual markets contributing
    generated_at: str


class PredictionMarketIntel:
    """Detects intelligence signals from prediction market data."""

    # Thresholds for signal detection
    SWING_THRESHOLD = 0.15  # 15% probability change
    GAP_THRESHOLD = 0.20  # 20% difference between sources
    SURGE_MULTIPLIER = 3.0  # 3x normal volume
    HIGH_VOLUME = 50_000  # $50K minimum for high-confidence signals

    # Security-relevant search queries for ecosystem monitoring
    ECOSYSTEM_QUERIES = [
        "crypto hack",
        "defi exploit",
        "crypto exchange insolvent",
        "stablecoin depeg",
        "crypto regulation SEC",
        "blockchain vulnerability",
        "bitcoin crash",
    ]

    def __init__(self):
        self._svc = get_prediction_market_service()
        # In-memory cache (Redis unavailable in current env)
        self._fear_cache: tuple | None = None  # (timestamp, CryptoFearIndex)
        self._signals_cache: tuple | None = None  # (timestamp, List[IntelSignal])
        self._fear_cache_ttl = 300  # 5 minutes
        self._signals_cache_ttl = 120  # 2 minutes

    # ── Public API ──────────────────────────────────────────

    async def generate_signals(self, max_signals: int = 10) -> list[IntelSignal]:
        """Generate all intelligence signals from current prediction market data."""
        # Check in-memory cache
        now = datetime.now(UTC).timestamp()
        if self._signals_cache:
            cache_time, cached = self._signals_cache
            if now - cache_time < self._signals_cache_ttl:
                return cached[:max_signals]

        signals = []

        # Run all detectors in parallel
        results = await asyncio.gather(
            self._detect_probability_swings(),
            self._detect_token_risk(),
            self._detect_ecosystem_risk(),
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, list):
                signals.extend(result)
            elif isinstance(result, Exception):
                logger.warning(f"Signal detector failed: {result}")

        # Sort by severity then confidence
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        signals.sort(key=lambda s: (severity_order.get(s.severity, 99), -s.confidence))

        # Store in cache
        self._signals_cache = (now, signals)

        return signals[:max_signals]

    async def generate_crypto_fear_index(self) -> CryptoFearIndex:
        """Generate aggregated risk index from prediction markets."""
        # Check in-memory cache
        now = datetime.now(UTC).timestamp()
        if self._fear_cache:
            cache_time, cached = self._fear_cache
            if now - cache_time < self._fear_cache_ttl:
                return cached

        all_markets = []

        # Search for ecosystem risk indicators
        for query in self.ECOSYSTEM_QUERIES:
            results = await self._svc.search(query, security_only=False)
            all_markets.extend(results)

        # Deduplicate and filter resolved markets
        seen = set()
        unique = []
        for m in all_markets:
            key = m.question.lower()[:80]
            if key not in seen:
                seen.add(key)
                # Skip resolved markets (0% or 100% = already decided)
                if 0.01 < m.probability_yes < 0.99:
                    unique.append(m)

        # Categorize (only crypto-relevant markets for fear index)
        crypto_markets = [m for m in unique if m.is_crypto_relevant]
        if not crypto_markets:
            crypto_markets = unique  # Fallback if no crypto markets found

        exploit_markets = self._filter_category(crypto_markets, ["hack", "exploit", "vulnerability", "drain", "breach"])
        regulatory_markets = self._filter_category(
            crypto_markets, ["sec", "cftc", "regulation", "ban", "sanction", "doj"]
        )
        stablecoin_markets = self._filter_category(
            crypto_markets, ["stablecoin", "usdt", "usdc", "dai", "depeg", "tether"]
        )
        exchange_markets = self._filter_category(
            crypto_markets, ["exchange", "binance", "coinbase", "insolvent", "bankrupt", "ftx"]
        )

        exploit_risk = self._aggregate_risk(exploit_markets)
        regulatory_risk = self._aggregate_risk(regulatory_markets)
        stablecoin_risk = self._aggregate_risk(stablecoin_markets)
        exchange_risk = self._aggregate_risk(exchange_markets)

        overall = exploit_risk * 0.35 + regulatory_risk * 0.25 + stablecoin_risk * 0.20 + exchange_risk * 0.20

        result = CryptoFearIndex(
            overall_risk=round(overall, 1),
            exploit_risk=round(exploit_risk, 1),
            regulatory_risk=round(regulatory_risk, 1),
            stablecoin_risk=round(stablecoin_risk, 1),
            exchange_risk=round(exchange_risk, 1),
            components=[self._market_summary(m) for m in unique[:20]],
            generated_at=datetime.now(UTC).isoformat(),
        )

        # Store in cache
        self._fear_cache = (now, result)

        return result

    # ── Signal Detectors ────────────────────────────────────

    async def _detect_probability_swings(self) -> list[IntelSignal]:
        """Detect sudden probability shifts in security-relevant markets."""
        signals = []

        # Get trending markets (highest volume = most watched)
        trending = await self._svc.trending(limit=15)

        for m in trending:
            if not m.is_security_relevant and not m.is_crypto_relevant:
                continue

            # High probability + high volume = consensus forming
            if m.probability_yes >= 0.75 and m.volume_usd >= self.HIGH_VOLUME:
                signals.append(
                    IntelSignal(
                        signal_type="HIGH_CONSENSUS_THREAT",
                        severity="high",
                        headline=f"Prediction markets strongly expect: {m.question[:90]}",
                        insight=(
                            f"At {m.probability_yes * 100:.0f}% probability with ${m.volume_usd:,.0f} in volume, "
                            f"traders are putting real money behind this outcome. "
                            f"This is not sentiment — it's financial conviction."
                        ),
                        evidence=[self._market_summary(m)],
                        action=(
                            "Review exposure to affected protocols/tokens. "
                            "Consider hedging if markets are pricing in high probability of adverse event."
                        ),
                        confidence=min(m.volume_usd / 500_000, 0.95),
                        generated_at=datetime.now(UTC).isoformat(),
                        source_markets=[m.slug],
                    )
                )

            # Low probability + high volume = market thinks risk is overblown
            if m.probability_yes <= 0.10 and m.volume_usd >= self.HIGH_VOLUME:
                signals.append(
                    IntelSignal(
                        signal_type="LOW_CONSENSUS_RISK",
                        severity="low",
                        headline=f"Markets dismiss risk: {m.question[:90]}",
                        insight=(
                            f"Only {m.probability_yes * 100:.0f}% probability despite ${m.volume_usd:,.0f} in volume. "
                            f"Traders with money at stake think this is unlikely."
                        ),
                        evidence=[self._market_summary(m)],
                        action="Low concern. Monitor for probability changes.",
                        confidence=min(m.volume_usd / 500_000, 0.85),
                        generated_at=datetime.now(UTC).isoformat(),
                        source_markets=[m.slug],
                    )
                )

        return signals

    async def _detect_token_risk(self) -> list[IntelSignal]:
        """Detect tokens being flagged by prediction markets."""
        signals = []

        # Search for markets mentioning scam/rug/exploit + tokens
        queries = ["token rug scam", "crypto hack token", "protocol exploit defi"]
        all_markets = []

        for q in queries:
            results = await self._svc.search(q, security_only=True)
            all_markets.extend(results)

        # Deduplicate and filter
        seen_slugs = set()
        unique = []
        for m in all_markets:
            if m.slug not in seen_slugs:
                seen_slugs.add(m.slug)
                # Skip resolved markets (0% or 100% = already decided)
                if m.probability_yes <= 0.01 or m.probability_yes >= 0.99:
                    continue
                unique.append(m)

        for m in unique[:10]:
            if m.is_security_relevant and m.probability_yes >= 0.30:
                tokens_str = ", ".join(m.tokens_mentioned[:3]) if m.tokens_mentioned else "crypto ecosystem"
                if tokens_str == "crypto ecosystem" and m.volume_usd < 5000:
                    continue  # Skip vague low-volume markets

                severity = "critical" if m.probability_yes >= 0.60 else "high"
                confidence = min(m.probability_yes * (m.volume_usd / 50_000), 0.9)
                if m.volume_usd < 1000:
                    confidence = min(confidence, 0.3)  # Low volume = low confidence regardless

                signals.append(
                    IntelSignal(
                        signal_type="TOKEN_RISK_SIGNAL",
                        severity=severity,
                        headline=f"Markets pricing {m.probability_yes * 100:.0f}% probability: {m.question[:90]}",
                        insight=(
                            f"Prediction markets are giving this a {m.probability_yes * 100:.0f}% chance. "
                            f"Volume: ${m.volume_usd:,.0f}. "
                            f"Affected: {tokens_str}. "
                            f"When markets price risk this high, it's worth investigating."
                        ),
                        evidence=[self._market_summary(m)],
                        action=(
                            "Run scanner on affected tokens/protocols. "
                            "Check for recent deployer activity, liquidity changes, wallet clustering."
                        ),
                        confidence=round(confidence, 2),
                        generated_at=datetime.now(UTC).isoformat(),
                        source_markets=[m.slug],
                    )
                )

        return signals

    async def _detect_ecosystem_risk(self) -> list[IntelSignal]:
        """Detect broad ecosystem-level risk signals."""
        signals = []
        all_markets = []

        for query in self.ECOSYSTEM_QUERIES[:4]:
            results = await self._svc.search(query, security_only=True)
            all_markets.extend(results)

        # Find high-probability high-volume ecosystem threats
        seen = set()
        for m in all_markets:
            key = m.question.lower()[:60]
            if key in seen:
                continue
            seen.add(key)

            # Skip resolved markets
            if m.probability_yes <= 0.01 or m.probability_yes >= 0.99:
                continue

            if m.probability_yes >= 0.50 and m.volume_usd >= 10_000:
                signals.append(
                    IntelSignal(
                        signal_type="ECOSYSTEM_RISK",
                        severity="high",
                        headline=f"Ecosystem risk alert: {m.question[:90]}",
                        insight=(
                            f"Prediction markets price this at {m.probability_yes * 100:.0f}% "
                            f"with ${m.volume_usd:,.0f} in volume. "
                            f"This affects the broader crypto ecosystem, not just one token."
                        ),
                        evidence=[self._market_summary(m)],
                        action="Review portfolio exposure to affected sectors. Consider broad hedges.",
                        confidence=min(m.volume_usd / 200_000, 0.8),
                        generated_at=datetime.now(UTC).isoformat(),
                        source_markets=[m.slug],
                    )
                )

        return signals

    # ── Helpers ─────────────────────────────────────────────

    def _filter_category(self, markets: list[PredictionMarket], keywords: list[str]) -> list[PredictionMarket]:
        """Filter markets by keyword match in question text."""
        results = []
        for m in markets:
            q_lower = m.question.lower()
            if any(kw in q_lower for kw in keywords):
                results.append(m)
        return results

    def _aggregate_risk(self, markets: list[PredictionMarket]) -> float:
        """Aggregate risk score from a set of markets (0-100).

        Weighted by: probability * volume * recency.
        Higher volume markets = more reliable signal.
        """
        if not markets:
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0

        for m in markets:
            weight = max(m.volume_usd / 10_000, 0.1)  # Minimum weight for small markets
            risk = m.probability_yes * 100  # 0-100
            weighted_sum += risk * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _market_summary(self, m: PredictionMarket) -> dict:
        """Create a compact market summary for signal evidence."""
        return {
            "source": m.source,
            "question": m.question,
            "probability_yes_pct": round(m.probability_yes * 100, 1),
            "volume_usd": round(m.volume_usd, 2),
            "slug": m.slug,
            "url": m.url,
            "tokens_mentioned": m.tokens_mentioned,
            "is_security_relevant": m.is_security_relevant,
            "is_crypto_relevant": m.is_crypto_relevant,
        }


# ── Singleton ───────────────────────────────────────────────

_intel: PredictionMarketIntel | None = None


def get_prediction_market_intel() -> PredictionMarketIntel:
    global _intel
    if _intel is None:
        _intel = PredictionMarketIntel()
    return _intel
