"""
Data models for rug imminence prediction.
"""

from __future__ import annotations

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
        return {"low": "\U0001f7e2", "medium": "\U0001f7e1", "high": "\U0001f7e0", "critical": "\U0001f534"}[self.value]

    @property
    def numeric(self) -> int:
        return {"low": 1, "medium": 2, "high": 3, "critical": 4}[self.value]


@dataclass
class SignalCategory:
    """A single signal category with weight, score, and evidence."""

    name: str
    weight: float
    score: float = 0.0
    confidence: float = 0.0
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
            lines.append("\n\u26a0\ufe0f **Warnings:**")
            for w in self.warnings[:5]:
                lines.append(f"  \u2022 {w}")
        if self.recommendations:
            lines.append("\n\U0001f4a1 **Recommendations:**")
            for r in self.recommendations[:5]:
                lines.append(f"  \u2022 {r}")
        if detailed and self.signals:
            lines.append("\n\U0001f4ca **Signal Breakdown:**")
            for name, sig in sorted(self.signals.items(), key=lambda x: x[1].weighted_contribution(), reverse=True):
                lines.append(
                    f"  {name}: {sig.score:.0f}/100 (weight: {sig.weight:.0%}) \u2192 contribution: {sig.weighted_contribution():.0f}"
                )
                for e in sig.evidence[:3]:
                    lines.append(f"    \u2514 {e}")
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
