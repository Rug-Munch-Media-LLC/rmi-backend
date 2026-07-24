"""
Portfolio Risk — Data Models
Enums, dataclasses, and result types for cross-chain portfolio risk analysis.
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class RiskLevel(Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_score(cls, score: float) -> "RiskLevel":
        if score >= 80:
            return cls.SAFE
        elif score >= 60:
            return cls.LOW
        elif score >= 40:
            return cls.MEDIUM
        elif score >= 20:
            return cls.HIGH
        return cls.CRITICAL


@dataclass
class TokenHolding:
    chain: str
    symbol: str
    name: str
    contract_address: str
    balance_raw: int
    balance_formatted: float
    decimals: int
    estimated_usd_value: float = 0.0
    risk_score: float = 50.0
    risk_flags: list[str] = field(default_factory=list)
    is_native: bool = False
    pct_of_portfolio: float = 0.0

    def risk_level(self) -> RiskLevel:
        return RiskLevel.from_score(self.risk_score)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChainPortfolio:
    chain: str
    total_value_usd: float = 0.0
    token_count: int = 0
    holdings: list[TokenHolding] = field(default_factory=list)
    avg_risk_score: float = 50.0
    high_risk_count: int = 0
    critical_risk_count: int = 0
    scan_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "total_value_usd": round(self.total_value_usd, 2),
            "token_count": self.token_count,
            "avg_risk_score": round(self.avg_risk_score, 1),
            "high_risk_count": self.high_risk_count,
            "critical_risk_count": self.critical_risk_count,
            "holdings": [h.to_dict() for h in self.holdings],
            "scan_error": self.scan_error,
        }


@dataclass
class PortfolioRiskProfile:
    wallet_address: str
    chains_scanned: list[str]
    total_value_usd: float = 0.0
    total_tokens: int = 0
    chain_count: int = 0
    overall_health_score: float = 50.0
    concentration_risk_pct: float = 0.0
    chain_portfolios: dict[str, ChainPortfolio] = field(default_factory=dict)
    top_risk_tokens: list[TokenHolding] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    scan_errors: list[str] = field(default_factory=list)

    def overall_risk_level(self) -> RiskLevel:
        return RiskLevel.from_score(self.overall_health_score)

    def report(self, format: str = "text") -> str:
        if format == "json":
            return json.dumps(self.to_dict(), indent=2, default=str)
        lines = []
        lines.append("=" * 64)
        total_val = f"${self.total_value_usd:,.2f}" if self.total_value_usd > 0 else "Unknown"
        lines.append(f"  PORTFOLIO RISK REPORT - {self.wallet_address[:12]}...{self.wallet_address[-6:]}")
        lines.append(f"  Health: {self.overall_health_score:.0f}/100 ({self.overall_risk_level().value.upper()})")
        lines.append(f"  Value:  {total_val} | Tokens: {self.total_tokens} | Chains: {self.chain_count}")
        lines.append("=" * 64)
        if self.findings:
            lines.append("")
            lines.append("🔍 KEY FINDINGS:")
            for f in self.findings:
                lines.append(f"  • {f}")
        lines.append("")
        lines.append(f"{'CHAIN':<14} {'VALUE':<16} {'TOKENS':<8} {'RISK':<8} {'HIGH/CRIT':<10}")
        lines.append("-" * 56)
        for chain_name, cp in sorted(self.chain_portfolios.items()):
            val = f"${cp.total_value_usd:,.2f}" if cp.total_value_usd else "?"
            avg_risk = f"{cp.avg_risk_score:.0f}"
            high_crit = f"{cp.high_risk_count}/{cp.critical_risk_count}"
            error_tag = f" ⚠️ {cp.scan_error}" if cp.scan_error else ""
            lines.append(f"{chain_name:<14} {val:<16} {cp.token_count:<8} {avg_risk:<8} {high_crit:<10}{error_tag}")
        if self.top_risk_tokens:
            lines.append("")
            lines.append("⚠️  HIGHEST RISK TOKENS:")
            for t in self.top_risk_tokens[:10]:
                val = f"${t.estimated_usd_value:,.2f}" if t.estimated_usd_value > 0 else "N/A"
                flags = ", ".join(t.risk_flags[:3])
                pct = f"{t.pct_of_portfolio:.1f}%"
                lines.append(f"  • {t.symbol:>8} ({t.chain}) - Score: {t.risk_score:.0f}/100 - {val} - {pct}")
                if flags:
                    lines.append(f"            ⚑ {flags}")
        lines.append("")
        lines.append("=" * 64)
        lines.append(f"  Report generated: {self.timestamp[:19]}")
        lines.append("=" * 64)
        return "\\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_address": self.wallet_address,
            "chains_scanned": self.chains_scanned,
            "total_value_usd": round(self.total_value_usd, 2),
            "total_tokens": self.total_tokens,
            "chain_count": self.chain_count,
            "overall_health_score": round(self.overall_health_score, 1),
            "concentration_risk_pct": round(self.concentration_risk_pct, 1),
            "overall_risk_level": self.overall_risk_level().value,
            "findings": self.findings,
            "chain_portfolios": {k: v.to_dict() for k, v in self.chain_portfolios.items()},
            "top_risk_tokens": [t.to_dict() for t in self.top_risk_tokens[:10]],
            "scan_errors": self.scan_errors,
            "timestamp": self.timestamp,
        }
