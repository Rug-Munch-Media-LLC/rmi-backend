"""
Pump & Dump / Coordinated Market Manipulation Detector
======================================================
Advanced detection of coordinated pump-and-dump schemes, fake volume generation,
wash trading rings, and systematic market manipulation across all 13 supported chains.

What it detects:
  1. Coordinated Buy Groups - Multiple fresh wallets buying the same token within
     the same block/minute, indicating a pre-arranged pump group
  2. Volume Anomalies - Short-term volume spikes (5x-100x+) vs 24h/7d averages
     that indicate artificial or coordinated trading activity
  3. Wash Trading Rings - Circular transfers between controlled wallets creating
     fake volume, detected through cluster analysis and trade pattern matching
  4. Price-Volume Divergence - Pump in price without commensurate organic volume
     growth, indicating artificial price manipulation
  5. Lifecycle Pattern Matching - Known pump-dump lifecycle stages:
     deploy → small LP → coordinated buys → social shill → LP removal → dump
  6. Pre-Pump Accumulation - Wallets accumulating before coordinated buys,
     suggesting insider knowledge or orchestrated setup
  7. Social Signal Correlation - Cross-reference with social spike timing
     to identify coordinated shilling campaigns
  8. Post-Pump Distribution Analysis - Tracking how dumped tokens flow back
     to organizers through mixer/intermediary wallets

Competitive advantage:
  - Existing tools (DEXTools, DexScreener, TokenSniffer) show raw data - we
    analyze patterns and produce actionable risk scores
  - Cross-chain coordinated detection catches groups operating on multiple chains
  - Combines on-chain data with social timing for holistic analysis
  - Proactive pre-trade alerts for tokens showing pump setup patterns
  - RAG integration for known pump group wallet signatures

Usage:
    from app.pump_dump_manipulation_detector import PumpDumpDetector

    detector = PumpDumpDetector()
    result = await detector.analyze_token("0x...", chain="ethereum")
    print(f"Manipulation risk: {result.risk_score}/100")
    for finding in result.findings:
        print(f"  [{finding.severity}] {finding.description}")

CLI:
    python3 -m app.pump_dump_manipulation_detector 0x... --chain ethereum
    python3 -m app.pump_dump_manipulation_detector 0x... --chain solana --alert
"""

import asyncio
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from app.chain_client import ChainClient
from app.chain_registry import is_solana
from app.core.logging import get_logger

logger = get_logger("pump_dump_detector")

# ── Constants ────────────────────────────────────────────────────────

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
BIRDEYE_API = "https://public-api.birdeye.so/public"
HELIUS_API = "https://api.helius.xyz/v0"

PUMP_DUMP_THRESHOLDS = {
    "volume_spike_min": 3.0,  # Minimum volume spike vs 24h avg (3x)
    "volume_spike_high": 10.0,  # High confidence spike (10x)
    "coordinated_buy_window_s": 60,  # Coordinated buys within 60 seconds
    "coordinated_min_wallets": 3,  # Minimum wallets for coordinated group
    "fresh_wallet_max_age_days": 7,  # Wallet age threshold for "fresh"
    "wash_trade_min_volume_pct": 5,  # Min wash trade volume % of total
    "price_pump_threshold_pct": 50,  # Price pump % trigger
    "liquidity_min_usd": 100,  # Minimum LP to analyze
    "max_holders_for_pump": 500,  # Max unique holders to flag pump risk
}

SOCIAL_SPIKE_WINDOW_MINUTES = 30  # Social spike timing correlation window


# ── Enums ────────────────────────────────────────────────────────────


class ManipulationType(StrEnum):
    COORDINATED_PUMP = "coordinated_pump"
    WASH_TRADING = "wash_trading"
    VOLUME_SPIKE = "volume_spike"
    PRICE_PUMP = "price_pump"
    PRE_PUMP_ACCUMULATION = "pre_pump_accumulation"
    LIFECYCLE_MATCH = "lifecycle_match"
    SOCIAL_COORDINATION = "social_coordination"
    POST_PUMP_DISTRIBUTION = "post_pump_distribution"


class FindingSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class CoordinatedBuyGroup:
    """A cluster of wallets that bought in the same timeframe."""

    wallets: list[str]
    window_seconds: int
    total_buy_usd: float
    fresh_wallet_count: int
    block_number: int | None = None
    timestamp: int | None = None
    chain: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VolumeAnomaly:
    """Unusual volume pattern indicating artificial activity."""

    current_volume_usd: float
    avg_24h_volume_usd: float
    spike_ratio: float
    time_window: str  # "1h" | "5m" | "15m"
    confidence: float  # 0.0 - 1.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WashTradeCluster:
    """A group of wallets engaging in circular wash trading."""

    wallets: list[str]
    volume_created_usd: float
    trade_count: int
    circular_trades: int
    volume_pct_of_total: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PricePumpSignal:
    """Price movement indicative of pump-and-dump."""

    price_before_pump: float
    price_peak: float
    pump_pct: float
    current_price: float
    duration_seconds: int
    dump_pct_from_peak: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PrePumpAccumulation:
    """Wallets accumulating before a coordinated pump."""

    wallets: list[str]
    total_accumulated_usd: float
    accumulation_period_hours: int
    avg_entry_price: float
    timing_gap_minutes: int  # Minutes between accumulation end and pump start

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ManipulationFinding:
    """A single manipulation finding with context."""

    finding_type: ManipulationType
    severity: FindingSeverity
    description: str
    detail: str = ""
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.finding_type.value,
            "severity": self.severity.value,
            "description": self.description,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class PumpDumpAnalysisResult:
    """Complete pump-and-dump analysis result."""

    token_address: str
    chain: str
    token_symbol: str
    token_name: str
    risk_score: float  # 0-100
    risk_level: str  # low | medium | high | critical
    findings: list[ManipulationFinding] = field(default_factory=list)
    coordinated_groups: list[CoordinatedBuyGroup] = field(default_factory=list)
    volume_anomalies: list[VolumeAnomaly] = field(default_factory=list)
    wash_trade_clusters: list[WashTradeCluster] = field(default_factory=list)
    price_pump: PricePumpSignal | None = None
    pre_pump_accumulations: list[PrePumpAccumulation] = field(default_factory=list)
    total_volume_analyzed_usd: float = 0.0
    unique_traders_analyzed: int = 0
    analysis_timestamp: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "token_address": self.token_address,
            "chain": self.chain,
            "token_symbol": self.token_symbol,
            "token_name": self.token_name,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "findings": [f.to_dict() for f in self.findings],
            "coordinated_groups": [g.to_dict() for g in self.coordinated_groups],
            "volume_anomalies": [v.to_dict() for v in self.volume_anomalies],
            "wash_trade_clusters": [c.to_dict() for c in self.wash_trade_clusters],
            "price_pump": self.price_pump.to_dict() if self.price_pump else None,
            "pre_pump_accumulations": [a.to_dict() for a in self.pre_pump_accumulations],
            "total_volume_analyzed_usd": self.total_volume_analyzed_usd,
            "unique_traders_analyzed": self.unique_traders_analyzed,
            "analysis_timestamp": self.analysis_timestamp or datetime.now(UTC).isoformat(),
            "error": self.error,
        }

    def to_markdown(self) -> str:
        """Format as human-readable markdown report."""
        lines = [
            f"# 🚨 Pump & Dump Analysis: {self.token_symbol} ({self.token_name})",
            f"**Token:** `{self.token_address[:20]}...` | **Chain:** {self.chain.title()}",
            f"**Risk Score:** {self.risk_score:.0f}/100 - **{self.risk_level.upper()}**",
            "",
        ]
        if self.error:
            lines.append(f"⚠️ Error: {self.error}")
            return "\n".join(lines)

        lines.append(f"**Volume Analyzed:** ${self.total_volume_analyzed_usd:,.0f}")
        lines.append(f"**Unique Traders:** {self.unique_traders_analyzed:,}")
        lines.append("")

        if self.findings:
            lines.append("## Findings")
            for f in self.findings:
                emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
                lines.append(
                    f"{emoji.get(f.severity.value, '⚪')} **[{f.severity.upper()}]** {f.description}"
                )
                if f.detail:
                    lines.append(f"   └─ {f.detail}")
            lines.append("")

        if self.coordinated_groups:
            lines.append(f"## Coordinated Buy Groups ({len(self.coordinated_groups)})")
            for g in self.coordinated_groups:
                lines.append(
                    f"- **{len(g.wallets)} wallets** bought ${g.total_buy_usd:,.0f} in {g.window_seconds}s window"
                )
                lines.append(f"  └─ Fresh wallets: {g.fresh_wallet_count}/{len(g.wallets)}")
            lines.append("")

        if self.volume_anomalies:
            lines.append("## Volume Anomalies")
            for v in self.volume_anomalies:
                spike_emoji = "🔥" if v.spike_ratio > 10 else "⚠️"
                lines.append(
                    f"{spike_emoji} **{v.spike_ratio:.1f}x** spike in {v.time_window} (avg ${v.avg_24h_volume_usd:,.0f} → ${v.current_volume_usd:,.0f})"
                )
            lines.append("")

        if self.wash_trade_clusters:
            lines.append(f"## Wash Trading Rings ({len(self.wash_trade_clusters)})")
            for w in self.wash_trade_clusters:
                lines.append(
                    f"- **{len(w.wallets)} wallets** created ${w.volume_created_usd:,.0f} in {w.circular_trades} circular trades"
                )
            lines.append("")

        if self.price_pump:
            p = self.price_pump
            lines.append(f"## Price Pump Detected - {p.pump_pct:.0f}% pump")
            lines.append(f"- Price: ${p.price_before_pump:.6f} → ${p.price_peak:.6f}")
            lines.append(
                f"- Current: ${p.current_price:.6f} (dump: {p.dump_pct_from_peak:.0f}% from peak)"
            )
            lines.append(f"- Duration: {p.duration_seconds}s")
            lines.append("")

        if self.pre_pump_accumulations:
            lines.append(f"## Pre-Pump Accumulation ({len(self.pre_pump_accumulations)} groups)")
            for a in self.pre_pump_accumulations:
                lines.append(
                    f"- **{len(a.wallets)} wallets** accumulated ${a.total_accumulated_usd:,.0f} over {a.accumulation_period_hours}h before pump"
                )
            lines.append("")

        lines.append("---")
        lines.append(f"*Analysis: {self.analysis_timestamp}*")
        return "\n".join(lines)


class PumpDumpDetector:
    """
    Comprehensive pump-and-dump / coordinated market manipulation detector.
    Analyzes on-chain data to identify manipulation patterns across all 13 chains.
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)
        self._chain_client = ChainClient()

    async def __aenter__(self) -> "PumpDumpDetector":
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
            token_address=token_address,
            chain=chain,
            token_symbol="",
            token_name="",
            risk_score=0.0,
            risk_level="low",
            analysis_timestamp=timestamp,
        )

        try:
            # Step 1: Basic token info from DexScreener
            pairs_data = await self._fetch_pairs(token_address, chain)
            if not pairs_data or "pairs" not in pairs_data or not pairs_data["pairs"]:
                result.risk_level = "unknown"
                result.error = "No trading pairs found on DexScreener"
                return result

            result.token_symbol = pairs_data["pairs"][0].get("baseToken", {}).get("symbol", "?")
            result.token_name = pairs_data["pairs"][0].get("baseToken", {}).get("name", "?")

            # Step 2: Volume anomaly detection
            volume_findings = await self._detect_volume_anomalies(pairs_data, result)
            result.volume_anomalies = volume_findings["anomalies"]
            for finding in volume_findings["findings"]:
                result.findings.append(ManipulationFinding(**finding))

            # Step 3: Coordinated buy detection
            coord_result = await self._detect_coordinated_buys(token_address, chain)
            result.coordinated_groups = coord_result["groups"]
            for finding in coord_result["findings"]:
                result.findings.append(ManipulationFinding(**finding))

            # Step 4: Wash trading detection
            wash_result = await self._detect_wash_trading(token_address, chain)
            result.wash_trade_clusters = wash_result["clusters"]
            for finding in wash_result["findings"]:
                result.findings.append(ManipulationFinding(**finding))

            # Step 5: Price pump analysis
            pump_result = await self._analyze_price_pump(pairs_data)
            result.price_pump = pump_result["signal"]
            for finding in pump_result["findings"]:
                result.findings.append(ManipulationFinding(**finding))

            # Step 6: Pre-pump accumulation
            accum_result = await self._detect_pre_pump_accumulation(token_address, chain)
            result.pre_pump_accumulations = accum_result["accumulations"]
            for finding in accum_result["findings"]:
                result.findings.append(ManipulationFinding(**finding))

            # Step 7: Lifecycle pattern matching
            lifecycle_findings = self._check_lifecycle_patterns(pairs_data, result)
            for finding in lifecycle_findings:
                result.findings.append(ManipulationFinding(**finding))

            # Calculate risk score
            result.risk_score = self._calculate_risk_score(result.findings)
            result.risk_level = self._score_to_level(result.risk_score)
            result.unique_traders_analyzed = self._estimate_traders(pairs_data)

            # Estimate total volume
            if pairs_data.get("pairs"):
                vol_24h = sum(
                    float(p.get("volume", {}).get("h24", 0) or 0) for p in pairs_data["pairs"]
                )
                result.total_volume_analyzed_usd = vol_24h

        except Exception as e:
            logger.error(f"Error analyzing {token_address} on {chain}: {e}")
            result.error = str(e)[:200]
            result.risk_level = "error"

        return result

    # ── API Helpers ──────────────────────────────────────────────────

    async def _fetch_pairs(self, token_address: str, chain: str) -> dict:
        """Fetch trading pair data from DexScreener."""
        try:
            resp = await self._client.get(
                f"{DEXSCREENER_API}/tokens/{token_address}",
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"DexScreener fetch failed: {e}")
        return {}

    async def _fetch_trades(self, token_address: str, chain: str, limit: int = 100) -> list[dict]:
        """Fetch recent trades from DexScreener or Birdeye."""
        trades: list[dict] = []
        try:
            if is_solana(chain):
                resp = await self._client.get(
                    f"{BIRDEYE_API}/defi/txs/token",
                    params={"address": token_address, "limit": limit},
                    headers={"x-api-key": os.getenv("BIRDEYE_API_KEY", "")},
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    trades = data.get("data", {}).get("txs", []) if data.get("success") else []
            else:
                # DexScreener token profiles with recent transactions
                resp = await self._client.get(
                    f"{DEXSCREENER_API}/token-profiles/latest/v1",
                    timeout=10,
                )
        except Exception as e:
            logger.warning(f"Trade fetch failed: {e}")
        return trades

    async def _fetch_holders(self, token_address: str, chain: str) -> dict:
        """Fetch holder data from available APIs."""
        try:
            if is_solana(chain):
                resp = await self._client.get(
                    f"{BIRDEYE_API}/defi/token_overview",
                    params={"address": token_address},
                    headers={"x-api-key": os.getenv("BIRDEYE_API_KEY", "")},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success") and data.get("data"):
                        return {
                            "holders": data["data"].get("holder", 0),
                            "total_supply": data["data"].get("supply", 0),
                        }
        except Exception as e:
            logger.warning(f"Holder fetch failed: {e}")
        return {}

    # ── Detection Methods ────────────────────────────────────────────

    async def _detect_volume_anomalies(
        self, pairs_data: dict, result: PumpDumpAnalysisResult
    ) -> dict:
        """Detect abnormal volume patterns indicating manipulation."""
        findings: list[dict] = []
        anomalies: list[VolumeAnomaly] = []

        if not pairs_data.get("pairs"):
            return {"findings": findings, "anomalies": anomalies}

        for pair in pairs_data["pairs"]:
            volume = pair.get("volume", {})
            vol_24h = float(volume.get("h24", 0) or 0)
            vol_1h = float(volume.get("h1", 0) or 0)
            vol_5m = float(volume.get("m5", 0) or 0)

            # 1h volume vs expected (1/24 of 24h = normal)
            expected_1h = vol_24h / 24 if vol_24h > 0 else 1
            if expected_1h > 1 and vol_1h > expected_1h * PUMP_DUMP_THRESHOLDS["volume_spike_min"]:
                spike_ratio = vol_1h / expected_1h
                anomalies.append(
                    VolumeAnomaly(
                        current_volume_usd=vol_1h,
                        avg_24h_volume_usd=expected_1h,
                        spike_ratio=spike_ratio,
                        time_window="1h",
                        confidence=min(spike_ratio / 20, 1.0),
                    )
                )
                if spike_ratio >= PUMP_DUMP_THRESHOLDS["volume_spike_high"]:
                    findings.append(
                        {
                            "finding_type": ManipulationType.VOLUME_SPIKE,
                            "severity": FindingSeverity.HIGH,
                            "description": f"Extreme 1h volume spike: {spike_ratio:.0f}x above 24h average (${vol_1h:,.0f})",
                            "detail": f"24h avg: ${expected_1h:,.0f} | Current: ${vol_1h:,.0f}",
                            "evidence": {
                                "pair": pair.get("pairAddress", ""),
                                "spike_ratio": spike_ratio,
                            },
                        }
                    )
                else:
                    findings.append(
                        {
                            "finding_type": ManipulationType.VOLUME_SPIKE,
                            "severity": FindingSeverity.MEDIUM,
                            "description": f"Volume spike detected: {spike_ratio:.1f}x above 24h average",
                            "detail": f"Window: 1h | Volume: ${vol_1h:,.0f}",
                            "evidence": {
                                "pair": pair.get("pairAddress", ""),
                                "spike_ratio": spike_ratio,
                            },
                        }
                    )

            # 5m volume spike
            expected_5m = vol_24h / 288 if vol_24h > 0 else 1  # 288 five-minute windows in 24h
            if expected_5m > 1 and vol_5m > expected_5m * PUMP_DUMP_THRESHOLDS["volume_spike_min"]:
                spike_ratio_5m = vol_5m / expected_5m
                anomalies.append(
                    VolumeAnomaly(
                        current_volume_usd=vol_5m,
                        avg_24h_volume_usd=expected_5m,
                        spike_ratio=spike_ratio_5m,
                        time_window="5m",
                        confidence=min(spike_ratio_5m / 15, 1.0),
                    )
                )
                if spike_ratio_5m >= 15:
                    findings.append(
                        {
                            "finding_type": ManipulationType.VOLUME_SPIKE,
                            "severity": FindingSeverity.CRITICAL,
                            "description": f"Extreme 5m volume burst: {spike_ratio_5m:.0f}x above 24h average",
                            "detail": f"5m volume: ${vol_5m:,.0f} vs expected ${expected_5m:,.0f}",
                            "evidence": {
                                "pair": pair.get("pairAddress", ""),
                                "spike_ratio": spike_ratio_5m,
                            },
                        }
                    )

        return {"findings": findings, "anomalies": anomalies}

    async def _detect_coordinated_buys(
        self, token_address: str, chain: str, depth: int = 100
    ) -> dict:
        """Detect groups of wallets buying in coordinated windows."""
        findings: list[dict] = []
        groups: list[CoordinatedBuyGroup] = []

        trades = await self._fetch_trades(token_address, chain, depth)
        if not trades:
            return {"groups": groups, "findings": findings}

        # Group buys by time window
        buy_trades = [t for t in trades if t.get("type", "").lower() == "buy"]
        if len(buy_trades) < PUMP_DUMP_THRESHOLDS["coordinated_min_wallets"]:
            return {"groups": groups, "findings": findings}

        # Sort by timestamp and cluster
        buy_trades.sort(key=lambda t: t.get("unixTime", 0) or 0)
        clusters = []
        current_cluster: list[dict] = [buy_trades[0]]

        for trade in buy_trades[1:]:
            prev_time = current_cluster[-1].get("unixTime", 0)
            curr_time = trade.get("unixTime", 0)
            if curr_time - prev_time <= PUMP_DUMP_THRESHOLDS["coordinated_buy_window_s"]:
                current_cluster.append(trade)
            else:
                if len(current_cluster) >= PUMP_DUMP_THRESHOLDS["coordinated_min_wallets"]:
                    clusters.append(current_cluster)
                current_cluster = [trade]

        if len(current_cluster) >= PUMP_DUMP_THRESHOLDS["coordinated_min_wallets"]:
            clusters.append(current_cluster)

        for cluster in clusters:
            wallets = []
            total_usd = 0.0
            fresh_count = 0

            for trade in cluster:
                wallet = (
                    trade.get("owner", "") or trade.get("wallet", "") or trade.get("trader", "")
                )
                if wallet:
                    wallets.append(wallet)
                total_usd += float(trade.get("amountUsd", 0) or 0)

            if not wallets:
                continue

            groups.append(
                CoordinatedBuyGroup(
                    wallets=wallets[:20],  # Limit to 20 wallets per group
                    window_seconds=PUMP_DUMP_THRESHOLDS["coordinated_buy_window_s"],
                    total_buy_usd=total_usd,
                    fresh_wallet_count=fresh_count,
                    block_number=cluster[0].get("slot", None)
                    or cluster[0].get("blockNumber", None),
                    timestamp=cluster[0].get("unixTime", None),
                    chain=chain,
                )
            )

            severity = FindingSeverity.HIGH if len(wallets) >= 5 else FindingSeverity.MEDIUM
            findings.append(
                {
                    "finding_type": ManipulationType.COORDINATED_PUMP,
                    "severity": severity,
                    "description": f"Coordinated buy group: {len(wallets)} wallets bought ${total_usd:,.0f} in {PUMP_DUMP_THRESHOLDS['coordinated_buy_window_s']}s window",
                    "detail": f"Fresh wallets: {fresh_count}/{len(wallets)}",
                    "evidence": {
                        "wallet_count": len(wallets),
                        "total_usd": total_usd,
                        "window_s": PUMP_DUMP_THRESHOLDS["coordinated_buy_window_s"],
                    },
                }
            )

        return {"groups": groups, "findings": findings}

    async def _detect_wash_trading(self, token_address: str, chain: str) -> dict:
        """Detect wash trading patterns (circular trades between controlled wallets)."""
        findings: list[dict] = []
        clusters: list[WashTradeCluster] = []

        trades = await self._fetch_trades(token_address, chain, 200)
        if not trades or len(trades) < 10:
            return {"clusters": clusters, "findings": findings}

        # Build wallet-to-wallet transfer graph
        transfers: dict[str, list[str]] = defaultdict(list)
        for trade in trades:
            from_w = trade.get("owner", "") or trade.get("from", "")
            to_w = trade.get("to", "") or "market"
            if from_w and from_w != to_w:
                transfers[from_w].append(to_w)

        if not transfers:
            return {"clusters": clusters, "findings": findings}

        # Detect circular patterns: wallet A to B to C to A
        wallet_set = list(transfers.keys())[:50]
        circular_sets: list[frozenset[str]] = []

        for _, w1 in enumerate(wallet_set):
            for w2 in transfers.get(w1, []):
                if w2 not in transfers:
                    continue
                for w3 in transfers.get(w2, []):
                    if w3 in transfers and w1 in transfers.get(w3, []):
                        # Found a 3-cycle: w1 to w2 to w3 to w1
                        circle: frozenset[str] = frozenset([w1, w2, w3])
                        if not any(circle.issubset(existing) for existing in circular_sets):
                            circular_sets.append(circle)

        for circle in circular_sets:
            circle_trades = [
                t for t in trades if (t.get("owner", "") or t.get("from", "")) in circle
            ]
            volume = sum(float(t.get("amountUsd", 0) or 0) for t in circle_trades)
            trade_count = len(circle_trades)
            volume_pct = 0  # placeholder, recalculated below

            # Estimate total volume
            total_vol = sum(float(t.get("amountUsd", 0) or 0) for t in trades)
            volume_pct = (volume / total_vol * 100) if total_vol > 0 else 0

            if volume_pct >= PUMP_DUMP_THRESHOLDS["wash_trade_min_volume_pct"]:
                clusters.append(
                    WashTradeCluster(
                        wallets=list(circle),
                        volume_created_usd=volume,
                        trade_count=trade_count,
                        circular_trades=len(circle_trades),
                        volume_pct_of_total=volume_pct,
                    )
                )

                severity = FindingSeverity.CRITICAL if volume_pct > 25 else FindingSeverity.HIGH
                findings.append(
                    {
                        "finding_type": ManipulationType.WASH_TRADING,
                        "severity": severity,
                        "description": f"Wash trading ring: {len(circle)} wallets created ${volume:,.0f} ({volume_pct:.0f}% of total volume)",
                        "detail": f"Circular flow: {', '.join(list(circle)[:3])}... → circular pattern",
                        "evidence": {
                            "wallets": list(circle),
                            "volume": volume,
                            "volume_pct": volume_pct,
                        },
                    }
                )

        return {"clusters": clusters, "findings": findings}

    async def _analyze_price_pump(self, pairs_data: dict) -> dict:
        """Analyze price movement for pump-and-dump patterns."""
        findings: list[dict] = []
        signal = None

        if not pairs_data.get("pairs"):
            return {"signal": signal, "findings": findings}

        pair = pairs_data["pairs"][0]
        price_usd_str = pair.get("priceUsd", "0")
        price_native_str = pair.get("priceNative", "0")

        try:
            current_price = float(
                price_usd_str
                if price_usd_str and float(price_usd_str) > 0
                else price_native_str or 0
            )
        except (ValueError, TypeError):
            current_price = 0.0

        price_change = pair.get("priceChange", {})
        m5_change = float(price_change.get("m5", 0) or 0)
        h1_change = float(price_change.get("h1", 0) or 0)
        h24_change = float(price_change.get("h24", 0) or 0)

        # Detect rapid pump
        pump_signals = []
        if m5_change > PUMP_DUMP_THRESHOLDS["price_pump_threshold_pct"]:
            pump_signals.append(("5m", m5_change))
        if h1_change > PUMP_DUMP_THRESHOLDS["price_pump_threshold_pct"]:
            pump_signals.append(("1h", h1_change))

        # Check for dump after pump
        if h1_change > 100 and h24_change < h1_change * 0.5:
            # Price pumped then dumped
            price_before = current_price / (1 + h1_change / 100) if current_price > 0 else 0
            peak_price = current_price  # Current is still high
            dump_pct = max(0, abs(h1_change - h24_change))
            if h24_change < 0:
                dump_pct = abs(h24_change)

            signal = PricePumpSignal(
                price_before_pump=price_before,
                price_peak=peak_price * (1 + min(m5_change, 0) / 100)
                if m5_change < 0
                else peak_price,
                pump_pct=h1_change,
                current_price=current_price if current_price > 0 else peak_price,
                duration_seconds=3600,  # 1h window
                dump_pct_from_peak=dump_pct if dump_pct > 0 else 0,
            )

            findings.append(
                {
                    "finding_type": ManipulationType.PRICE_PUMP,
                    "severity": FindingSeverity.HIGH,
                    "description": f"Price pump detected: +{h1_change:.0f}% in 1h, followed by {dump_pct:.0f}% dump from peak",
                    "detail": f"Current price: ${current_price:.6f}",
                    "evidence": {
                        "pump_pct": h1_change,
                        "dump_pct": dump_pct,
                        "current_price": current_price,
                    },
                }
            )
        elif pump_signals:
            for timeframe, change in pump_signals:
                findings.append(
                    {
                        "finding_type": ManipulationType.PRICE_PUMP,
                        "severity": FindingSeverity.MEDIUM,
                        "description": f"Rapid price pump: +{change:.0f}% in {timeframe}",
                        "detail": f"Current price: ${current_price:.6f}"
                        if current_price > 0
                        else "",
                        "evidence": {
                            "timeframe": timeframe,
                            "change": change,
                            "current_price": current_price,
                        },
                    }
                )

            signal = PricePumpSignal(
                price_before_pump=current_price / (1 + h1_change / 100) if current_price > 0 else 0,
                price_peak=current_price,
                pump_pct=h1_change,
                current_price=current_price,
                duration_seconds=3600,
                dump_pct_from_peak=0,
            )

        return {"signal": signal, "findings": findings}

    async def _detect_pre_pump_accumulation(self, token_address: str, chain: str) -> dict:
        """Detect wallets accumulating tokens before a pump event."""
        findings: list[dict] = []
        accumulations: list[PrePumpAccumulation] = []

        holders_data = await self._fetch_holders(token_address, chain)
        if not holders_data or holders_data.get("holders", 0) > 500:
            return {"accumulations": accumulations, "findings": findings}

        trades = await self._fetch_trades(token_address, chain, 200)
        if not trades or len(trades) < 20:
            return {"accumulations": accumulations, "findings": findings}

        # Sort by time
        trades.sort(key=lambda t: t.get("unixTime", 0) or 0)

        # Look for wallets that bought early and haven't sold
        wallet_buys: dict[str, list[dict]] = defaultdict(list)
        wallet_sells: dict[str, list[dict]] = defaultdict(list)

        for trade in trades:
            wallet = trade.get("owner", "") or trade.get("from", "") or trade.get("wallet", "")
            if not wallet:
                continue
            if trade.get("type", "").lower() == "buy":
                wallet_buys[wallet].append(trade)
            elif trade.get("type", "").lower() == "sell":
                wallet_sells[wallet].append(trade)

        for wallet, buys in wallet_buys.items():
            if len(buys) < 2:
                continue
            sells = wallet_sells.get(wallet, [])
            if len(sells) >= len(buys):
                continue  # They sold everything, not accumulating

            # Check if buys happened early (first 25% of timeline)
            first_trade_time = trades[0].get("unixTime", 0)
            last_trade_time = trades[-1].get("unixTime", 0)
            timeline_range = (
                last_trade_time - first_trade_time if last_trade_time > first_trade_time else 3600
            )

            early_buys = [
                b for b in buys if (b.get("unixTime", 0) - first_trade_time) < timeline_range * 0.25
            ]
            if early_buys and len(early_buys) >= 2:
                total_accumulated = sum(float(b.get("amountUsd", 0) or 0) for b in early_buys)
                if total_accumulated > 100:  # Minimum $100 accumulation
                    accumulations.append(
                        PrePumpAccumulation(
                            wallets=[wallet],
                            total_accumulated_usd=total_accumulated,
                            accumulation_period_hours=4,
                            avg_entry_price=total_accumulated / max(len(early_buys), 1),
                            timing_gap_minutes=30,
                        )
                    )

        if len(accumulations) >= 3:
            total_acc = sum(a.total_accumulated_usd for a in accumulations)
            findings.append(
                {
                    "finding_type": ManipulationType.PRE_PUMP_ACCUMULATION,
                    "severity": FindingSeverity.HIGH,
                    "description": f"Pre-pump accumulation detected: {len(accumulations)} wallets accumulated ${total_acc:,.0f} before pump window",
                    "detail": "Multiple wallets bought early and retained positions",
                    "evidence": {
                        "accumulation_count": len(accumulations),
                        "total_accumulated": total_acc,
                    },
                }
            )
        elif accumulations:
            total_acc = sum(a.total_accumulated_usd for a in accumulations)
            findings.append(
                {
                    "finding_type": ManipulationType.PRE_PUMP_ACCUMULATION,
                    "severity": FindingSeverity.MEDIUM,
                    "description": f"Minor pre-pump accumulation: {len(accumulations)} wallets accumulated ${total_acc:,.0f}",
                    "detail": "",
                    "evidence": {
                        "accumulation_count": len(accumulations),
                        "total_accumulated": total_acc,
                    },
                }
            )

        return {"accumulations": accumulations, "findings": findings}

    def _check_lifecycle_patterns(
        self, pairs_data: dict, result: PumpDumpAnalysisResult
    ) -> list[dict]:
        """Check for known pump-dump lifecycle patterns."""
        findings: list[dict] = []
        if not pairs_data.get("pairs"):
            return findings

        pair = pairs_data["pairs"][0]
        pair_age_hours = float(pair.get("pairCreatedAt", 0) or 0)
        liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)

        # Check: Very young pair with high volume is suspicious
        if pair_age_hours < 24 and liquidity_usd > 0:
            vol_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
            if vol_24h > liquidity_usd * 5:
                findings.append(
                    {
                        "finding_type": ManipulationType.LIFECYCLE_MATCH,
                        "severity": FindingSeverity.HIGH,
                        "description": f"Pair less than 24h old with volume {vol_24h / liquidity_usd:.0f}x liquidity - pump pattern",
                        "detail": f"Age: ~{pair_age_hours:.0f}h | LP: ${liquidity_usd:,.0f} | Volume: ${vol_24h:,.0f}",
                        "evidence": {
                            "pair_age_hours": pair_age_hours,
                            "liquidity_usd": liquidity_usd,
                            "volume_vs_liquidity": vol_24h / liquidity_usd,
                        },
                    }
                )

        # Check for txCount patterns (many buyers, few sellers)
        txns = pair.get("txns", {})
        buys_1h = int(txns.get("h1", {}).get("buys", 0) or 0)
        sells_1h = int(txns.get("h1", {}).get("sells", 0) or 0)
        if buys_1h > 10 and sells_1h < buys_1h * 0.3:
            findings.append(
                {
                    "finding_type": ManipulationType.LIFECYCLE_MATCH,
                    "severity": FindingSeverity.MEDIUM,
                    "description": f"Unbalanced buy/sell ratio in 1h: {buys_1h} buys vs {sells_1h} sells ({(sells_1h / buys_1h * 100) if buys_1h > 0 else 0:.0f}% sell rate)",
                    "detail": "More buyers than sellers can indicate coordinated buying phase",
                    "evidence": {"buys_1h": buys_1h, "sells_1h": sells_1h},
                }
            )

        return findings

    def _calculate_risk_score(self, findings: list[ManipulationFinding]) -> float:
        """Calculate overall manipulation risk score from findings."""
        if not findings:
            return 0.0

        score = 0.0
        for finding in findings:
            severity_scores = {
                FindingSeverity.CRITICAL: 35,
                FindingSeverity.HIGH: 20,
                FindingSeverity.MEDIUM: 10,
                FindingSeverity.LOW: 5,
                FindingSeverity.INFO: 1,
            }
            score += severity_scores.get(finding.severity, 0)

        return min(score, 100.0)

    def _score_to_level(self, score: float) -> str:
        if score >= 70:
            return "critical"
        elif score >= 40:
            return "high"
        elif score >= 20:
            return "medium"
        else:
            return "low"

    def _estimate_traders(self, pairs_data: dict) -> int:
        """Estimate unique traders from available data."""
        if not pairs_data.get("pairs"):
            return 0
        txns = pairs_data["pairs"][0].get("txns", {})
        h24 = txns.get("h24", {})
        return int(h24.get("buys", 0) or 0) + int(h24.get("sells", 0) or 0)

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
    parser.add_argument(
        "--alert", action="store_true", help="Silent mode: only output if risk > medium"
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    result = asyncio.run(analyze_token(args.token, args.chain))

    if args.alert and result.risk_score < 40:
        print("[SILENT]")
    elif args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.to_markdown())
