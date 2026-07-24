"""
RIP — Enums, Types & Constants
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
    score: float = 0.0
    imminence: ImminenceLevel = ImminenceLevel.LOW
    signals: dict[str, SignalCategory] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    last_updated: str = ""
    scan_duration_ms: float = 0.0
    sources_used: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        emoji = self.imminence.emoji
        return (
            f"{emoji} RIP {self.score:.0f}/100 - {self.token_symbol or self.token_address[:10]} "
            f"on {self.chain}: {self.imminence.value.upper()} imminence. "
            f"{len(self.warnings)} warnings, {len(self.recommendations)} recommendations."
        )

    def narrative(self, detailed: bool = False) -> str:
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


# Default signal weights (sum to 1.0)
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

# Known safe LP lockers
KNOWN_LP_LOCKERS_DEFAULT = [
    "0x407993575c91ce7643a4d4cCACc9A98c36eE1BBE",
    "0x663a5c229c09b049e36dCc11a9B0d4a8Eb9db214",
    "0x74de5d12FC0fC274C1b0C6E9F58E60e0b4B7eCb1",
    "0xE2fE530C047f2d85298b07D9333C05737f1435FB",
    "0x6b8DA0E0E1d7b8B0E0E1d7b8B0E0E1d7b8B0E0E1d",
    "0x4e59b44847b379578588920cA78FbF26c0B4956C",
    "0xD152f549545093347A162Dce210e7293f1452150",
]

SCORE_THRESHOLDS = {
    ImminenceLevel.LOW: (0, 30),
    ImminenceLevel.MEDIUM: (30, 55),
    ImminenceLevel.HIGH: (55, 75),
    ImminenceLevel.CRITICAL: (75, 101),
}

VALID_CHAINS = {
    "base", "solana", "ethereum", "bsc", "polygon",
    "arbitrum", "optimism", "avalanche", "fantom", "gnosis",
}
