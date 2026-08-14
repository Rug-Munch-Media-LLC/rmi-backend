"""Data models for the honeypot detector."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.pyrevm_engine import SellSimulationResult


class FindingSeverity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class HoneypotType(Enum):
    SELL_RESTRICTION = "sell_restriction"
    HIGH_SELL_TAX = "high_sell_tax"
    BUY_TAX = "buy_tax"
    BLACKLIST = "blacklist"
    MAX_WALLET = "max_wallet"
    MAX_TX = "max_tx_amount"
    COOLDOWN = "cooldown"
    PROXY_UPGRADE = "proxy_upgrade"
    OWNER_MINT = "owner_mint"
    OWNER_BURN = "owner_burn"
    LIQUIDITY_DRAIN = "liquidity_drain"
    HIDDEN_MINT = "hidden_mint"
    SELFDESTRUCT = "selfdestruct"
    TRADING_PAUSE = "trading_pause"
    FAKE_BURN = "fake_burn"
    RUG_PULL = "rug_pull"
    UNKNOWN = "unknown"


@dataclass
class SecurityFinding:
    finding_type: HoneypotType
    severity: FindingSeverity
    description: str
    detail: str = ""
    code_reference: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.finding_type.value,
            "severity": self.severity.value,
            "description": self.description,
            "detail": self.detail,
            "code_reference": self.code_reference,
        }


@dataclass
class TokenInfo:
    address: str
    chain: str
    name: str = ""
    symbol: str = ""
    decimals: int = 18
    total_supply: int = 0
    holder_count: int = 0
    creator_address: str = ""
    creation_tx: str = ""
    creation_timestamp: str = ""
    is_verified: bool = False
    source_code_url: str = ""
    price_usd: float = 0.0
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    market_cap_usd: float = 0.0
    owner_address: str = ""
    ownership_renounced: bool = False
    is_proxy: bool = False
    implementation_address: str = ""
    chain_data_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "chain": self.chain,
            "name": self.name,
            "symbol": self.symbol,
            "decimals": self.decimals,
            "total_supply": self.total_supply,
            "holder_count": self.holder_count,
            "creator": self.creator_address,
            "age": self.creation_timestamp,
            "verified": self.is_verified,
            "price_usd": self.price_usd,
            "liquidity_usd": self.liquidity_usd,
            "volume_24h": self.volume_24h_usd,
            "market_cap": self.market_cap_usd,
            "owner": self.owner_address,
            "ownership_renounced": self.ownership_renounced,
            "is_proxy": self.is_proxy,
            "implementation": self.implementation_address,
            "chain_data_sources": self.chain_data_sources,
        }


@dataclass
class HoneypotAnalysisResult:
    token: TokenInfo
    findings: list[SecurityFinding] = field(default_factory=list)
    risk_score: int = 0
    risk_label: str = "safe"
    buy_tax_pct: float = 0.0
    sell_tax_pct: float = 0.0
    max_tx_pct: float = 0.0
    max_wallet_pct: float = 0.0
    liquidity_locked_pct: float = 0.0
    liquidity_lock_duration_days: int = 0
    top_holders: list[dict[str, Any]] = field(default_factory=list)
    suspicious_patterns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    chain_data_sources: list[str] = field(default_factory=list)
    scan_timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    scan_duration_ms: int = 0
    # Optional pyrevm fork-state sell-simulation output (None when skipped or
    # when the pyrevm dependency / RPC is unavailable — static scan still runs).
    sell_simulation: SellSimulationResult | None = None

    def has_critical_findings(self) -> bool:
        return any(f.severity == FindingSeverity.CRITICAL for f in self.findings)

    def has_high_findings(self) -> bool:
        return any(f.severity == FindingSeverity.HIGH for f in self.findings)

    def findings_by_severity(self, severity: FindingSeverity) -> list[SecurityFinding]:
        return [f for f in self.findings if f.severity == severity]

    def summary(self) -> str:
        critical = len(self.findings_by_severity(FindingSeverity.CRITICAL))
        high = len(self.findings_by_severity(FindingSeverity.HIGH))
        medium = len(self.findings_by_severity(FindingSeverity.MEDIUM))
        return f"[{self.risk_label.upper()}] {self.token.symbol or '?'} ({self.token.address[:10]}...) | Risk: {self.risk_score}/100 | Found: {critical}C/{high}H/{medium}M | Buy tax: {self.buy_tax_pct:.1f}% | Sell tax: {self.sell_tax_pct:.1f}% | LP locked: {self.liquidity_locked_pct:.0f}%"

    def report(self, fmt: str = "text") -> str:
        if fmt == "json":
            return json.dumps(self.to_dict(), indent=2, default=str)
        lines = []
        lines.append("=" * 60)
        lines.append(f"HONEYPOT ANALYSIS REPORT - {self.token.symbol or 'Unknown Token'}")
        lines.append("=" * 60)
        lines.append(f"Token:     {self.token.address}")
        lines.append(f"Chain:     {self.token.chain}")
        lines.append(f"Risk:      {self.risk_score}/100 ({self.risk_label})")
        lines.append(f"Price:     ${self.token.price_usd:,.8f}")
        lines.append(f"Liquidity: ${self.token.liquidity_usd:,.2f}")
        lines.append(f"MCap:      ${self.token.market_cap_usd:,.2f}")
        lines.append("")
        if self.buy_tax_pct > 0 or self.sell_tax_pct > 0:
            lines.append("-- Fee Structure --")
            lines.append(f"  Buy tax:  {self.buy_tax_pct:.1f}%")
            lines.append(f"  Sell tax: {self.sell_tax_pct:.1f}%")
            lines.append("")
        lines.append("-- Findings --")
        if not self.findings:
            lines.append("  No suspicious findings detected.")
        else:
            sev_emoji = {
                FindingSeverity.CRITICAL: "CRIT",
                FindingSeverity.HIGH: "HIGH",
                FindingSeverity.MEDIUM: "MED",
                FindingSeverity.LOW: "LOW",
                FindingSeverity.INFO: "INFO",
            }
            for finding in self.findings:
                lines.append(f"  [{sev_emoji.get(finding.severity, '?')}] {finding.description}")
                if finding.detail:
                    lines.append(f"     {finding.detail}")
            lines.append("")
        if self.top_holders:
            lines.append("-- Top Holders --")
            for i, h in enumerate(self.top_holders[:5], 1):
                lines.append(f"  {i}. {h.get('address', '?')[:12]}... - {h.get('percentage', 0):.1f}%")
            lines.append("")
        if self.warnings:
            lines.append("-- Warnings --")
            for w in self.warnings:
                lines.append(f"  WARN {w}")
            lines.append("")
        lines.append(f"Scan completed: {self.scan_timestamp}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "token": self.token.to_dict(),
            "risk_score": self.risk_score,
            "risk_label": self.risk_label,
            "findings": [f.to_dict() for f in self.findings],
            "buy_tax_pct": self.buy_tax_pct,
            "sell_tax_pct": self.sell_tax_pct,
            "liquidity_locked_pct": self.liquidity_locked_pct,
            "liquidity_lock_duration_days": self.liquidity_lock_duration_days,
            "top_holders": self.top_holders,
            "warnings": self.warnings,
            "chain_data_sources": self.chain_data_sources,
            "scan_timestamp": self.scan_timestamp,
        }
        if self.sell_simulation is not None:
            payload["sell_simulation"] = asdict(self.sell_simulation)
        return payload
