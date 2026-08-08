"""
Pump & Dump — Enums, Dataclasses & Constants
==============================================
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# ── Constants ────────────────────────────────────────────────────────

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
BIRDEYE_API = "https://public-api.birdeye.so/public"
HELIUS_API = "https://mainnet.helius-rpc.com/v0"

PUMP_DUMP_THRESHOLDS = {
    "volume_spike_min": 3.0,
    "volume_spike_high": 10.0,
    "coordinated_buy_window_s": 60,
    "coordinated_min_wallets": 3,
    "fresh_wallet_max_age_days": 7,
    "wash_trade_min_volume_pct": 5,
    "price_pump_threshold_pct": 50,
    "liquidity_min_usd": 100,
    "max_holders_for_pump": 500,
}

SOCIAL_SPIKE_WINDOW_MINUTES = 30

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VolumeAnomaly:
    """Unusual volume pattern indicating artificial activity."""

    current_volume_usd: float
    avg_24h_volume_usd: float
    spike_ratio: float
    time_window: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WashTradeCluster:
    """A group of wallets engaging in circular wash trading."""

    wallets: list[str]
    volume_created_usd: float
    trade_count: int
    circular_trades: int
    volume_pct_of_total: float

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PrePumpAccumulation:
    """Wallets accumulating before a coordinated pump."""

    wallets: list[str]
    total_accumulated_usd: float
    accumulation_period_hours: int
    avg_entry_price: float
    timing_gap_minutes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ManipulationFinding:
    """A single manipulation finding with context."""

    finding_type: ManipulationType
    severity: FindingSeverity
    description: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
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
    risk_score: float
    risk_level: str
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

    def to_dict(self) -> dict[str, Any]:
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
                lines.append(f"{emoji.get(f.severity.value, '⚪')} **[{f.severity.upper()}]** {f.description}")
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
            lines.append(f"- Current: ${p.current_price:.6f} (dump: {p.dump_pct_from_peak:.0f}% from peak)")
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
