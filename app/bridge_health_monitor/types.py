"""
Enums & data classes for bridge health monitoring.
"""

from __future__ import annotations
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

class RiskTier(Enum):
    """Bridge risk classification."""

    SAFE = "SAFE"
    WATCH = "WATCH"
    DANGER = "DANGER"
    CRITICAL = "CRITICAL"

class TrustModel(Enum):
    """Bridge security model classification."""

    EXTERNAL_VALIDATORS = "external_validators"
    OPTIMISTIC = "optimistic"
    LIQUIDITY_NETWORK = "liquidity_network"
    HYBRID = "hybrid"
    INTENT_BASED = "intent_based"

@dataclass
class BridgeTVLSnapshot:
    """TVL snapshot for a single bridge."""

    bridge_key: str
    bridge_name: str
    tvl_usd: float
    tvl_change_24h_pct: float
    tvl_change_7d_pct: float
    tvl_change_30d_pct: float
    chain_breakdown: dict[str, float] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

@dataclass
class BridgeSecurityScore:
    """Security rating for a bridge."""

    bridge_key: str
    bridge_name: str
    overall_score: float
    tvl_depth_score: float
    decentralization_score: float
    audit_score: float
    exploit_history_score: float
    upgrade_risk_score: float
    risk_tier: RiskTier
    vulnerabilities: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)

@dataclass
class ExploitSignal:
    """Detection signal that may indicate an exploit."""

    bridge_key: str
    bridge_name: str
    signal_type: str
    severity: str
    description: str
    detected_value: Any = None
    threshold_value: Any = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

@dataclass
class BridgeHealthReport:
    """Complete bridge health report."""

    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_bridges: int = 0
    bridges_healthy: int = 0
    bridges_watch: int = 0
    bridges_danger: int = 0
    bridges_critical: int = 0
    total_tvl_usd: float = 0.0
    tvl_change_24h_pct: float = 0.0
    bridge_snapshots: dict[str, BridgeTVLSnapshot] = field(default_factory=dict)
    security_scores: dict[str, BridgeSecurityScore] = field(default_factory=dict)
    exploit_signals: list[ExploitSignal] = field(default_factory=list)
    contagion_risk: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "🌉 CROSS-CHAIN BRIDGE HEALTH REPORT",
            f"   Generated: {self.timestamp}",
            "",
            "   📊 Overview",
            f"   ├─ Bridges monitored: {self.total_bridges}",
            f"   ├─ Healthy: {self.bridges_healthy}  ✅",
            f"   ├─ Watch: {self.bridges_watch}  ⚠️",
            f"   ├─ Danger: {self.bridges_danger}  🔴",
            f"   ├─ Critical: {self.bridges_critical}  🚨",
            f"   ├─ Total TVL: ${self.total_tvl_usd:,.0f}",
            f"   └─ 24h TVL change: {self.tvl_change_24h_pct:+.1f}%",
            "",
        ]

        if self.exploit_signals:
            lines.append(f"   🚨 EXPLOIT SIGNALS ({len(self.exploit_signals)})")
            for sig in self.exploit_signals:
                emoji = {"low": "ℹ️", "medium": "⚠️", "high": "🔴", "critical": "🚨"}.get(
                    sig.severity, "ℹ️"
                )
                lines.append(f"   {emoji} [{sig.severity.upper()}] {sig.bridge_name}: {sig.description}")
            lines.append("")

        if self.contagion_risk:
            lines.append(f"   📡 Contagion Risk - {len(self.contagion_risk)} bridges may be affected")
            for b in self.contagion_risk:
                lines.append(f"   ├─ {b}")
            lines.append("")

        lines.append("   🔒 Bridge Security Scores")
        sorted_bridges = sorted(self.security_scores.items(), key=lambda x: x[1].overall_score)
        for _key, score in sorted_bridges:
            emoji = {
                RiskTier.SAFE: "🟢",
                RiskTier.WATCH: "🟡",
                RiskTier.DANGER: "🟠",
                RiskTier.CRITICAL: "🔴",
            }.get(score.risk_tier, "⚪")
            lines.append(
                f"   {emoji} {score.bridge_name:<20} {score.overall_score:>5.1f}/100 "
                f"[TVL={score.tvl_depth_score:.0f} Dec={score.decentralization_score:.0f} "
                f"Audit={score.audit_score:.0f} Exploit={score.exploit_history_score:.0f} "
                f"Upgrade={score.upgrade_risk_score:.0f}]"
            )
            if score.vulnerabilities:
                for v in score.vulnerabilities:
                    lines.append(f"       ⚠ {v}")

        lines.append("")
        lines.append("   💡 Recommendation:")
        if self.bridges_critical > 0:
            lines.append("   🚨 CRITICAL: Exploit likely in progress. Avoid using affected bridges.")
        elif self.bridges_danger > 0:
            lines.append("   🔴 DANGER: High-risk bridges detected. Use with extreme caution.")
        elif self.bridges_watch > 0:
            lines.append("   ⚠️ WATCH: Monitor bridges flagged below. Elevated risk.")
        else:
            lines.append("   ✅ All bridges appear healthy. Normal monitoring cadence.")

        return "\n".join(lines)

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(asdict(self), indent=2, default=str)

__all__ = [
    "RiskTier",
    "TrustModel",
    "BridgeTVLSnapshot",
    "BridgeSecurityScore",
    "ExploitSignal",
    "BridgeHealthReport",
]
