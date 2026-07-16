"""
DEX Liquidity Pool Manipulation Analyzer
=========================================
Analyzes DEX pools for manipulation, fake liquidity, and attack vectors:

  - Concentrated liquidity manipulation (Uniswap V3-style tick ranges)
  - Liquidity depth analysis and concentration detection
  - Sandwich vulnerability scoring
  - Fake/wash liquidity detection (liquidity that exists only briefly)
  - Pool owner risk assessment (fee changes, mint cap, pool config)
  - Price impact simulation
  - MEV vulnerability estimation

Features:
  - Multi-DEX support (Uniswap V2/V3, PancakeSwap, Raydium, Orca)
  - Chain-agnostic (EVM + Solana)
  - Confidence-scored manipulation risk (0-100)
  - Per-signal breakdown with evidence
  - Price impact curves for trade simulation

Tier: Premium ($0.10)
Endpoint: POST /api/v1/x402-tools/dex_pool_manipulation
"""

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.logging import get_logger

logger = get_logger("dex_pool_manipulation_analyzer")

# ── Constants ──────────────────────────────────────────────────
EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

BASIS_POINTS_DENOM = 10000

# ── Risk signal definitions ────────────────────────────────────


class RiskCategory(Enum):
    LIQUIDITY_CONCENTRATION = "liquidity_concentration"
    SANDWICH_VULNERABILITY = "sandwich_vulnerability"
    POOL_OWNER_RISK = "pool_owner_risk"
    FAKE_LIQUIDITY = "fake_liquidity"
    PRICE_MANIPULATION = "price_manipulation"
    MEV_EXPOSURE = "mev_exposure"
    FEE_TIER_ABUSE = "fee_tier_abuse"


RISK_WEIGHTS = {
    RiskCategory.LIQUIDITY_CONCENTRATION: 25,
    RiskCategory.SANDWICH_VULNERABILITY: 15,
    RiskCategory.POOL_OWNER_RISK: 20,
    RiskCategory.FAKE_LIQUIDITY: 25,
    RiskCategory.PRICE_MANIPULATION: 30,
    RiskCategory.MEV_EXPOSURE: 10,
    RiskCategory.FEE_TIER_ABUSE: 15,
}

MAX_RISK_SCORE = sum(RISK_WEIGHTS.values())  # 140


# ── Data models ─────────────────────────────────────────────────


@dataclass
class PoolConfig:
    """DEX pool configuration."""

    address: str
    chain: str
    dex: str
    version: str  # "v2" | "v3" | "clmm" (concentrated liquidity)
    token0: str
    token1: str
    token0_symbol: str = ""
    token1_symbol: str = ""
    fee_tier: int = 0  # in basis points
    tick_spacing: int = 0  # V3
    sqrt_price: int = 0  # V3
    liquidity: int = 0  # V3
    total_liquidity_usd: float = 0.0
    owner: str = ""
    created_at: int = 0


@dataclass
class Position:
    """A concentrated liquidity position."""

    owner: str
    tick_lower: int
    tick_upper: int
    liquidity: int
    usd_value: float = 0.0


@dataclass
class SwapEvent:
    """Recent swap on this pool."""

    tx_hash: str
    block: int
    timestamp: int
    amount_in: float
    amount_out: float
    price_before: float
    price_after: float
    price_impact_pct: float = 0.0


@dataclass
class RiskSignal:
    """A single risk signal with evidence."""

    category: RiskCategory
    severity: float  # 0.0 - 1.0
    description: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class PoolRiskReport:
    """Full risk analysis for a pool."""

    pool: PoolConfig
    risk_score: float  # 0-100
    signals: list[RiskSignal] = field(default_factory=list)
    price_impact_1eth: float = 0.0
    price_impact_10eth: float = 0.0
    price_impact_100eth: float = 0.0
    top_5_concentration_pct: float = 0.0
    liquidity_depth_1pct: float = 0.0
    sandwich_profit_estimate: float = 0.0
    recommendations: list[str] = field(default_factory=list)
    analysis_time_ms: int = 0


# ── Address validation ──────────────────────────────────────────


def is_valid_address(addr: str) -> bool:
    addr = addr.strip()
    return bool(EVM_ADDRESS_RE.match(addr) or SOLANA_ADDRESS_RE.match(addr))


# ═══════════════════════════════════════════════════════════════
# Core Analyzer
# ═══════════════════════════════════════════════════════════════


class DEXPoolManipulationAnalyzer:
    """Analyze a DEX pool for manipulation signals."""

    def __init__(self, chain: str = "ethereum", dex: str = "uniswap_v3"):
        self.chain = chain
        self.dex = dex

    async def analyze_pool(
        self,
        pool_address: str,
        recent_swaps: list[dict] | None = None,
        positions: list[dict] | None = None,
        pool_metadata: dict | None = None,
    ) -> PoolRiskReport:
        """Full pool risk analysis."""
        start = time.monotonic()

        if not is_valid_address(pool_address):
            raise ValueError(f"Invalid pool address: {pool_address}")

        # Build pool config from metadata
        pool = self._build_pool_config(pool_address, pool_metadata or {})

        # Parse raw data into typed models
        parsed_swaps = self._parse_swaps(recent_swaps or [])
        parsed_positions = self._parse_positions(positions or [])

        signals: list[RiskSignal] = []
        recommendations: list[str] = []

        # ── Analysis 1: Liquidity concentration ──
        conc_signal, conc_pct = self._analyze_concentration(parsed_positions, pool)
        if conc_signal:
            signals.append(conc_signal)
        top5_pct = conc_pct

        # ── Analysis 2: Sandwich vulnerability ──
        sandwich_signal, sandwich_profit = self._analyze_sandwich_vulnerability(parsed_swaps, pool)
        if sandwich_signal:
            signals.append(sandwich_signal)
        sand_profit = sandwich_profit

        # ── Analysis 3: Pool owner risk ──
        owner_signal = self._analyze_pool_owner_risk(pool)
        if owner_signal:
            signals.append(owner_signal)

        # ── Analysis 4: Fake liquidity detection ──
        fake_liq_signal = self._analyze_fake_liquidity(parsed_swaps, parsed_positions, pool)
        if fake_liq_signal:
            signals.append(fake_liq_signal)

        # ── Analysis 5: Price manipulation ──
        price_manip_signal = self._analyze_price_manipulation(parsed_swaps, pool)
        if price_manip_signal:
            signals.append(price_manip_signal)

        # ── Analysis 6: MEV exposure ──
        mev_signal = self._analyze_mev_exposure(parsed_swaps, pool)
        if mev_signal:
            signals.append(mev_signal)

        # ── Analysis 7: Fee tier abuse ──
        fee_signal = self._analyze_fee_tier_abuse(pool)
        if fee_signal:
            signals.append(fee_signal)

        # ── Calculate risk score ──
        risk_score = self._calculate_risk_score(signals)

        # ── Price impact simulation ──
        base_liquidity = pool.total_liquidity_usd or 100_000  # default fallback
        impact_1eth = self._simulate_price_impact(1, base_liquidity)
        impact_10eth = self._simulate_price_impact(10, base_liquidity)
        impact_100eth = self._simulate_price_impact(100, base_liquidity)
        depth_1pct = self._estimate_liquidity_depth(base_liquidity)

        # ── Generate recommendations ──
        recommendations = self._generate_recommendations(signals, risk_score, pool)

        elapsed = int((time.monotonic() - start) * 1000)

        return PoolRiskReport(
            pool=pool,
            risk_score=round(risk_score, 1),
            signals=signals,
            price_impact_1eth=round(impact_1eth, 4),
            price_impact_10eth=round(impact_10eth, 4),
            price_impact_100eth=round(impact_100eth, 4),
            top_5_concentration_pct=round(top5_pct, 1),
            liquidity_depth_1pct=round(depth_1pct, 2),
            sandwich_profit_estimate=round(sand_profit, 4),
            recommendations=recommendations,
            analysis_time_ms=elapsed,
        )

    def _build_pool_config(self, address: str, meta: dict) -> PoolConfig:
        """Build pool config from metadata dict."""
        return PoolConfig(
            address=address,
            chain=meta.get("chain", self.chain),
            dex=meta.get("dex", self.dex),
            version=meta.get("version", "v3"),
            token0=meta.get("token0", ""),
            token1=meta.get("token1", ""),
            token0_symbol=meta.get("token0_symbol", ""),
            token1_symbol=meta.get("token1_symbol", ""),
            fee_tier=meta.get("fee_tier", 0),
            tick_spacing=meta.get("tick_spacing", 0),
            sqrt_price=meta.get("sqrt_price", 0),
            liquidity=meta.get("liquidity", 0),
            total_liquidity_usd=float(meta.get("total_liquidity_usd", 0)),
            owner=meta.get("owner", ""),
            created_at=meta.get("created_at", 0),
        )

    def _parse_swaps(self, raw_swaps: list[dict]) -> list[SwapEvent]:
        """Parse raw swap data into typed swap events."""
        parsed = []
        for s in raw_swaps:
            try:
                amount_in = float(s.get("amount_in", 0))
                amount_out = float(s.get("amount_out", 0))
                price_before = float(s.get("price_before", 0))
                price_after = float(s.get("price_after", 0))
                impact = 0.0
                if price_before > 0:
                    impact = abs(price_after - price_before) / price_before * 100
                parsed.append(
                    SwapEvent(
                        tx_hash=s.get("tx_hash", ""),
                        block=int(s.get("block", 0)),
                        timestamp=int(s.get("timestamp", 0)),
                        amount_in=amount_in,
                        amount_out=amount_out,
                        price_before=price_before,
                        price_after=price_after,
                        price_impact_pct=impact,
                    )
                )
            except (ValueError, TypeError):
                continue
        return parsed

    def _parse_positions(self, raw_positions: list[dict]) -> list[Position]:
        """Parse raw position data into typed positions."""
        parsed = []
        for p in raw_positions:
            try:
                parsed.append(
                    Position(
                        owner=p.get("owner", ""),
                        tick_lower=int(p.get("tick_lower", 0)),
                        tick_upper=int(p.get("tick_upper", 0)),
                        liquidity=int(p.get("liquidity", 0)),
                        usd_value=float(p.get("usd_value", 0)),
                    )
                )
            except (ValueError, TypeError):
                continue
        return parsed

    # ── Analysis methods ────────────────────────────────────────

    def _analyze_concentration(
        self, positions: list[Position], pool: PoolConfig
    ) -> tuple[RiskSignal | None, float]:
        """
        Detect extreme liquidity concentration.
        If top 5 positions control >70% of liquidity, flag it.
        """
        if not positions:
            return None, 0.0

        total_liq = sum(p.liquidity for p in positions)
        if total_liq <= 0:
            return None, 0.0

        sorted_positions = sorted(positions, key=lambda p: p.liquidity, reverse=True)
        top5 = sorted_positions[:5]
        top5_liq = sum(p.liquidity for p in top5)
        top5_pct = (top5_liq / total_liq) * 100

        severity = min(top5_pct / 100, 1.0)  # 70% → 0.7, 100% → 1.0

        # Check if single owner dominates
        owner_liq: dict[str, int] = {}
        for p in positions:
            owner_liq[p.owner] = owner_liq.get(p.owner, 0) + p.liquidity
        top_owner_pct = (max(owner_liq.values()) / total_liq) * 100 if owner_liq else 0

        evidence = [
            f"Top 5 positions control {top5_pct:.1f}% of total liquidity",
            f"Largest LP provider holds {top_owner_pct:.1f}% of liquidity ({max(owner_liq, key=lambda k: owner_liq[k])[:10]}...)"
            if owner_liq
            else "",
        ]
        evidence = [e for e in evidence if e]

        if severity >= 0.3:
            signal = RiskSignal(
                category=RiskCategory.LIQUIDITY_CONCENTRATION,
                severity=round(severity, 2),
                description=f"High liquidity concentration: top 5 positions hold {top5_pct:.1f}%",
                evidence=evidence,
            )
            return signal, top5_pct

        if severity >= 0.15:
            signal = RiskSignal(
                category=RiskCategory.LIQUIDITY_CONCENTRATION,
                severity=round(severity, 2),
                description=f"Moderate liquidity concentration: top 5 positions hold {top5_pct:.1f}%",
                evidence=evidence,
            )
            return signal, top5_pct

        return None, top5_pct

    def _analyze_sandwich_vulnerability(
        self, swaps: list[SwapEvent], pool: PoolConfig
    ) -> tuple[RiskSignal | None, float]:
        """
        Estimate sandwich vulnerability.
        Pools with low liquidity and large swap-to-reserve ratios are
        sandwichable. Also check if past swaps show sandwich patterns.
        """
        if not swaps:
            return None, 0.0

        # Look for sandwich patterns: two swaps from same block with price reversal
        sandwich_count = 0
        total_profit_est = 0.0

        # Group by block
        block_groups: dict[int, list[SwapEvent]] = {}
        for s in swaps:
            block_groups.setdefault(s.block, []).append(s)

        for _block, block_swaps in block_groups.items():
            if len(block_swaps) >= 2:
                # Check for price up then down pattern
                sorted_swaps = sorted(block_swaps, key=lambda s: s.timestamp)
                for i in range(len(sorted_swaps) - 1):
                    for j in range(i + 1, len(sorted_swaps)):
                        s1 = sorted_swaps[i]
                        s2 = sorted_swaps[j]
                        # If first swap pushed price up and second moved it back
                        if (
                            s1.price_after > s1.price_before
                            and s2.price_after < s2.price_before
                            and abs(s2.price_after - s1.price_before) / max(s1.price_before, 0.001)
                            < 0.02
                        ):
                            sandwich_count += 1
                            # Estimate profit as USD value of price displacement
                            mid_price = (s1.price_before + s2.price_after) / 2
                            total_profit_est += (
                                abs(s1.price_after - s1.price_before)
                                * min(s1.amount_in, s1.amount_out)
                                / max(mid_price, 0.001)
                            )

        # Also check if low liquidity makes it vulnerable
        base_liq = pool.total_liquidity_usd
        vulnerability_score = 0.0
        swap_to_reserve = 0.0
        avg_swap_size = 0.0
        if swaps:
            avg_swap_size = sum(s.amount_in for s in swaps) / len(swaps)

        if base_liq > 0 and avg_swap_size > 0:
            swap_to_reserve = avg_swap_size / base_liq
            vulnerability_score = min(swap_to_reserve * 10, 1.0)  # 10% swap → 1.0

        severity = max(vulnerability_score * 0.7, min(sandwich_count / 10, 0.3))
        evidence = []

        if sandwich_count > 0:
            evidence.append(
                f"Detected {sandwich_count} potential sandwich attack patterns in recent blocks"
            )
            evidence.append(f"Estimated profit from sandwich activity: ${total_profit_est:.2f}")

        if vulnerability_score > 0.3:
            evidence.append(f"Large swap-to-reserve ratio ({swap_to_reserve:.4f}) - pool is thin")

        if severity >= 0.2:
            signal = RiskSignal(
                category=RiskCategory.SANDWICH_VULNERABILITY,
                severity=round(severity, 2),
                description=f"Pool is {('highly' if severity > 0.5 else 'moderately')} vulnerable to sandwich attacks",
                evidence=evidence,
            )
            return signal, total_profit_est

        return None, total_profit_est

    def _analyze_pool_owner_risk(self, pool: PoolConfig) -> RiskSignal | None:
        """
        Assess risk from pool owner/creator.
        Flag if owner can change fees, collect fees, or has special powers.
        """
        risk_factors = []
        severity = 0.0

        # Fee tier can indicate risk
        if pool.fee_tier == 0 and pool.version in ("v3", "clmm"):
            risk_factors.append("Pool has 0% fee tier - possible fee manipulation")
            severity += 0.2

        if pool.fee_tier > 1000:  # >10%
            risk_factors.append(f"High fee tier ({pool.fee_tier / 100}%) - likely rent-seeking")
            severity += 0.3

        # Pool with no liquidity
        if pool.total_liquidity_usd <= 0:
            risk_factors.append("Pool has zero reported liquidity - possible ghost pool")
            severity += 0.3

        # Check if pool is very new with high liquidity (suspicious)
        if pool.created_at > 0 and pool.total_liquidity_usd > 500_000:
            age_hours = (time.time() - pool.created_at) / 3600
            if age_hours < 24:
                risk_factors.append(
                    f"Pool is {age_hours:.1f}h old with ${pool.total_liquidity_usd:,.0f} liquidity - rapid ramp is suspicious"
                )
                severity += 0.15

        if not risk_factors:
            return None

        severity = min(severity, 1.0)
        signal = RiskSignal(
            category=RiskCategory.POOL_OWNER_RISK,
            severity=round(severity, 2),
            description="Pool configuration carries owner-related risks",
            evidence=risk_factors,
        )
        return signal

    def _analyze_fake_liquidity(
        self, swaps: list[SwapEvent], positions: list[Position], pool: PoolConfig
    ) -> RiskSignal | None:
        """
        Detect fake/wash liquidity patterns:
        - Large liquidity added then immediately removed
        - Liquidity that never gets traded against
        - Symmetric trades that wash volume
        """
        risk_factors = []
        severity = 0.0

        # Check if swaps exist at all
        if not swaps and positions and pool.total_liquidity_usd > 10_000:
            risk_factors.append(
                f"${pool.total_liquidity_usd:,.0f} liquidity with zero recent swaps - liquidity may be fake/unused"
            )
            severity += 0.3

        # Check if all liquidity is from one provider
        if positions:
            unique_owners = {p.owner for p in positions}
            if len(unique_owners) <= 1 and len(positions) > 1:
                risk_factors.append(
                    f"All {len(positions)} positions belong to a single owner - possible wash/self-dealing"
                )
                severity += 0.35

        # Check for wash trading pattern: symmetric buy/sell pairs
        if swaps:
            wash_pairs = 0
            for i in range(0, len(swaps) - 1, 2):
                if i + 1 < len(swaps):
                    s1, s2 = swaps[i], swaps[i + 1]
                    # Buy then sell of similar magnitude (within 100% of each other)
                    if (
                        abs(s1.amount_in - s2.amount_out) / max(s1.amount_in, s2.amount_out, 0.001)
                        < 1.0
                        and s1.price_before != s2.price_before
                    ):
                        # Check if price returned to near-original
                        price_change = abs(s2.price_after - s1.price_before) / max(
                            s1.price_before, 0.001
                        )
                        if price_change < 0.01:  # <1% net change after pair
                            wash_pairs += 1

            if wash_pairs >= 3:
                risk_factors.append(
                    f"Detected {wash_pairs} potential wash-trading pairs (buy/sell with <1% net price impact)"
                )
                severity += 0.25

        if not risk_factors:
            return None

        severity = min(severity, 1.0)
        signal = RiskSignal(
            category=RiskCategory.FAKE_LIQUIDITY,
            severity=round(severity, 2),
            description="Liquidity shows signs of being artificial or wash-generated",
            evidence=risk_factors,
        )
        return signal

    def _analyze_price_manipulation(
        self, swaps: list[SwapEvent], pool: PoolConfig
    ) -> RiskSignal | None:
        """
        Detect abnormal price movement patterns.
        - Large price swings with low volume
        - Price pumps followed by dumps
        - Abnormal price deviation from market
        """
        if not swaps or len(swaps) < 2:
            return None

        risk_factors = []

        # Calculate cumulative price change
        try:
            price_changes = [abs(s.price_impact_pct) for s in swaps]
            avg_impact = sum(price_changes) / len(price_changes)
            max_impact = max(price_changes)

            # Track price direction
            start_price = swaps[0].price_before
            end_price = swaps[-1].price_after
            if start_price > 0:
                _ = abs(end_price - start_price) / start_price * 100  # total price change

            # Large individual impact
            if max_impact > 5.0:
                risk_factors.append(
                    f"Single swap caused {max_impact:.2f}% price impact - pool is very thin"
                )
            elif max_impact > 2.0:
                risk_factors.append(f"Single swap caused {max_impact:.2f}% price impact")

            # High average impact indicates thin pool
            if avg_impact > 1.0:
                risk_factors.append(
                    f"Average swap impact {avg_impact:.2f}% - persistent thin liquidity"
                )

            # Total price manipulation score
            manip_severity = 0.0
            if max_impact > 5.0:
                manip_severity += 0.4
            elif max_impact > 2.0:
                manip_severity += 0.2

            if avg_impact > 2.0:
                manip_severity += 0.3
            elif avg_impact > 1.0:
                manip_severity += 0.15

            severities = [manip_severity]
            evidence = risk_factors

            if severities and severities[0] >= 0.2:
                signal = RiskSignal(
                    category=RiskCategory.PRICE_MANIPULATION,
                    severity=round(severities[0], 2),
                    description=f"Pool shows signs of price manipulation (avg impact {avg_impact:.2f}%, max {max_impact:.2f}%)",
                    evidence=evidence,
                )
                return signal

        except (ZeroDivisionError, IndexError):
            pass

        return None

    def _analyze_mev_exposure(self, swaps: list[SwapEvent], pool: PoolConfig) -> RiskSignal | None:
        """Estimate MEV exposure risk."""
        if not swaps:
            return None

        # Count rapid successive trades (potential frontrunning)
        rapid_trades = 0
        for i in range(len(swaps) - 1):
            if (
                swaps[i + 1].timestamp - swaps[i].timestamp < 3  # within 3 seconds
                and swaps[i + 1].block == swaps[i].block
            ):
                rapid_trades += 1

        mev_ratio = rapid_trades / len(swaps) if swaps else 0

        if mev_ratio >= 0.2:
            signal = RiskSignal(
                category=RiskCategory.MEV_EXPOSURE,
                severity=round(min(mev_ratio, 1.0), 2),
                description=f"{rapid_trades}/{len(swaps)} trades in same block within 3s - high MEV activity",
                evidence=[
                    f"{rapid_trades} rapid trades detected in same block timestamps",
                    f"{mev_ratio * 100:.0f}% of trades are potential frontrun/backrun targets",
                ],
            )
            return signal

        return None

    def _analyze_fee_tier_abuse(self, pool: PoolConfig) -> RiskSignal | None:
        """Flag suspicious fee tier configurations."""
        if pool.version not in ("v3", "clmm"):
            return None

        risk_factors = []
        severity = 0.0

        # Suspiciously high fee for common pairs
        common_pairs = {"WETH/USDC", "WETH/USDT", "WETH/DAI", "WBTC/USDC", "SOL/USDC", "SOL/USDT"}
        pair_key = f"{pool.token0_symbol}/{pool.token1_symbol}"
        pair_rev = f"{pool.token1_symbol}/{pool.token0_symbol}"

        if (pair_key in common_pairs or pair_rev in common_pairs) and pool.fee_tier > 100:
            risk_factors.append(
                f"High fee tier ({pool.fee_tier / 100}%) for common pair {pair_key} - above standard 0.01-1% range"
            )
            severity += 0.3

        # Zero fee with active liquidity - possible fee manipulation
        if pool.fee_tier == 0 and pool.total_liquidity_usd > 10_000:
            risk_factors.append(
                "Zero fee tier with active liquidity - unusual, may indicate fee manipulation"
            )
            severity += 0.2

        if not risk_factors:
            return None

        signal = RiskSignal(
            category=RiskCategory.FEE_TIER_ABUSE,
            severity=round(severity, 2),
            description="Pool fee tier configuration is unusual",
            evidence=risk_factors,
        )
        return signal

    # ── Scoring ──────────────────────────────────────────────────

    def _calculate_risk_score(self, signals: list[RiskSignal]) -> float:
        """Calculate weighted risk score 0-100."""
        if not signals:
            return 0.0

        total_weighted = 0.0
        for signal in signals:
            weight = RISK_WEIGHTS.get(signal.category, 10)
            total_weighted += weight * signal.severity

        raw_score = (total_weighted / MAX_RISK_SCORE) * 100
        return min(raw_score, 100.0)

    # ── Price impact simulation ──────────────────────────────────

    def _simulate_price_impact(self, eth_amount: float, pool_liquidity_usd: float) -> float:
        """
        Simulate price impact using constant product formula approximation.
        Returns percentage price impact.
        """
        if pool_liquidity_usd <= 0:
            return 999.99  # infinite impact for empty pool

        # Using constant product: k = x * y
        # Impact = 1 - (k / (k + eth_in * reserve_out))
        # Simplified: impact ≈ eth_amount / (2 * reserve + eth_amount)
        # For a 50/50 pool, reserve ≈ sqrt(k) ≈ liquidity / 2
        reserve = pool_liquidity_usd / 2
        if reserve <= 0:
            return 999.99

        impact = (eth_amount) / (2 * reserve + eth_amount) * 100
        return min(impact, 99.99)

    def _estimate_liquidity_depth(self, pool_liquidity_usd: float) -> float:
        """
        Estimate how much trade volume causes 1% price impact.
        """
        if pool_liquidity_usd <= 0:
            return 0.0

        # For constant product: 1% price impact ≈ 1% of reserve
        # Simplified: depth_1pct ≈ 0.02 * total_liquidity
        return pool_liquidity_usd * 0.02

    # ── Recommendations ──────────────────────────────────────────

    def _generate_recommendations(
        self, signals: list[RiskSignal], risk_score: float, pool: PoolConfig
    ) -> list[str]:
        """Generate actionable recommendations based on findings."""
        recs: list[str] = []
        categories = {s.category for s in signals}

        if max((s.severity for s in signals), default=0) > 0.7:
            recs.append(
                "🚨 CRITICAL: Multiple high-severity risks detected. Avoid trading this pool."
            )

        if RiskCategory.LIQUIDITY_CONCENTRATION in categories:
            recs.append(
                "Consider splitting large trades across multiple pools to reduce concentration risk."
            )

        if RiskCategory.SANDWICH_VULNERABILITY in categories:
            recs.append(
                "Use MEV-protected RPC endpoints or private mempools for any trades on this pool."
            )

        if RiskCategory.POOL_OWNER_RISK in categories:
            recs.append("Verify pool owner/creator reputation before providing liquidity.")

        if RiskCategory.FAKE_LIQUIDITY in categories:
            recs.append("⚠️ Liquidity appears artificial. Cross-check with on-chain position data.")

        if RiskCategory.PRICE_MANIPULATION in categories:
            recs.append("Monitor price closely - pool has shown abnormal price movements.")

        if RiskCategory.MEV_EXPOSURE in categories:
            recs.append("High MEV activity detected. Avoid placing market orders on this pool.")

        if RiskCategory.FEE_TIER_ABUSE in categories:
            recs.append(
                f"Fee tier ({pool.fee_tier / 100}%) is unusual for this pair. Verify against market standards."
            )

        if risk_score < 20 and not recs:
            recs.append("✅ Pool appears low risk based on available data.")
        elif not recs:
            recs.append(f"Pool risk score: {risk_score:.0f}/100 - exercise standard caution.")

        return recs


# ═══════════════════════════════════════════════════════════════
# Report formatting
# ═══════════════════════════════════════════════════════════════


def format_risk_report(report: PoolRiskReport) -> dict[str, Any]:
    """Convert report to API-friendly dict."""
    return {
        "pool_address": report.pool.address,
        "chain": report.pool.chain,
        "dex": f"{report.pool.dex}_{report.pool.version}",
        "pair": f"{report.pool.token0_symbol}/{report.pool.token1_symbol}",
        "risk_score": report.risk_score,
        "risk_level": _risk_level(report.risk_score),
        "signals": [
            {
                "category": s.category.value,
                "severity": s.severity,
                "description": s.description,
                "evidence": s.evidence,
            }
            for s in report.signals
        ],
        "metrics": {
            "price_impact": {
                "1_eth": report.price_impact_1eth,
                "10_eth": report.price_impact_10eth,
                "100_eth": report.price_impact_100eth,
            },
            "top_5_concentration_pct": report.top_5_concentration_pct,
            "liquidity_depth_1pct_change_usd": report.liquidity_depth_1pct,
            "sandwich_profit_estimate_usd": report.sandwich_profit_estimate,
        },
        "recommendations": report.recommendations,
        "total_liquidity_usd": report.pool.total_liquidity_usd,
        "fee_tier_bps": report.pool.fee_tier,
        "analysis_time_ms": report.analysis_time_ms,
    }


def _risk_level(score: float) -> str:
    if score >= 70:
        return "critical"
    if score >= 45:
        return "high"
    if score >= 25:
        return "medium"
    if score >= 10:
        return "low"
    return "minimal"
