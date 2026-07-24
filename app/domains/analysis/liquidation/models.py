"""
Liquidation Cascade — Data Models
===================================
Risk tiers, position dataclasses, analysis result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

class RiskTier(StrEnum):
    SAFE = "SAFE"
    WATCH = "WATCH"
    DANGER = "DANGER"
    CRITICAL = "CRITICAL"

    def score(self) -> int:
        return {"SAFE": 0, "WATCH": 1, "DANGER": 2, "CRITICAL": 3}[self.value]

@dataclass
class CollateralPosition:
    """A collateral asset deposited in a lending protocol."""
    asset: str
    asset_address: str
    amount_usd: float
    amount_token: float
    ltv: float
    liquidation_threshold: float
    price_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class DebtPosition:
    """A debt/borrow position against collateral."""
    asset: str
    asset_address: str
    amount_usd: float
    amount_token: float
    variable_rate: float
    stable_rate: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class ProtocolPosition:
    """A full position in one lending protocol on one chain."""
    protocol: str
    chain: str
    wallet: str
    collateral: list[CollateralPosition] = field(default_factory=list)
    debt: list[DebtPosition] = field(default_factory=list)
    total_collateral_usd: float = 0.0
    total_debt_usd: float = 0.0
    health_factor: float | None = None
    liquidation_price_usd: float | None = None
    risk_tier: RiskTier = RiskTier.SAFE

    def compute_health(self) -> None:
        """Compute health factor from total collateral and debt."""
        if self.total_debt_usd <= 0:
            self.health_factor = float("inf")
            self.risk_tier = RiskTier.SAFE
            return
        weighted_threshold = sum(
            c.liquidation_threshold * c.amount_usd for c in self.collateral
        ) / max(sum(c.amount_usd for c in self.collateral), 1)
        if weighted_threshold <= 0:
            self.health_factor = float("inf")
            self.risk_tier = RiskTier.SAFE
            return
        self.health_factor = (self.total_collateral_usd * weighted_threshold) / self.total_debt_usd
        if self.health_factor >= 2.0:
            self.risk_tier = RiskTier.SAFE
        elif self.health_factor >= 1.5:
            self.risk_tier = RiskTier.WATCH
        elif self.health_factor >= 1.1:
            self.risk_tier = RiskTier.DANGER
        else:
            self.risk_tier = RiskTier.CRITICAL
        if self.collateral:
            primary = max(self.collateral, key=lambda c: c.amount_usd)
            if primary.amount_usd > 0:
                price_ratio = self.total_debt_usd / (
                    primary.amount_usd * primary.liquidation_threshold
                )
                self.liquidation_price_usd = primary.price_usd * price_ratio

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["risk_tier"] = self.risk_tier.value
        d["health_factor"] = (
            round(self.health_factor, 4)
            if self.health_factor and self.health_factor != float("inf")
            else None
        )
        d["liquidation_price_usd"] = (
            round(self.liquidation_price_usd, 2) if self.liquidation_price_usd else None
        )
        return d

@dataclass
class CascadeScenario:
    """A simulated cascade event."""
    name: str
    description: str
    liquidated_positions: int = 0
    total_liquidated_value_usd: float = 0.0
    secondary_affected_positions: int = 0
    total_secondary_value_usd: float = 0.0
    market_impact_pct: float | None = None
    chain: str = "all"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class LiquidationCluster:
    """A cluster of wallets at similar liquidation prices."""
    chain: str
    primary_collateral: str
    price_range_low: float
    price_range_high: float
    wallet_count: int = 0
    total_debt_usd: float = 0.0
    total_collateral_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class LiquidationAnalysis:
    """Complete analysis result for a wallet or set of wallets."""
    wallet: str
    chains_analyzed: list[str]
    total_collateral_usd: float = 0.0
    total_debt_usd: float = 0.0
    positions: list[ProtocolPosition] = field(default_factory=list)
    overall_health_factor: float | None = None
    overall_risk_tier: RiskTier = RiskTier.SAFE
    cascade_scenarios: list[CascadeScenario] = field(default_factory=list)
    liquidation_clusters: list[LiquidationCluster] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def analyze(self) -> LiquidationAnalysis:
        """Run the full analysis pipeline and return self."""
        for pos in self.positions:
            pos.compute_health()
        self.total_collateral_usd = sum(p.total_collateral_usd for p in self.positions)
        self.total_debt_usd = sum(p.total_debt_usd for p in self.positions)
        if self.total_debt_usd > 0 and self.total_collateral_usd > 0:
            total_weighted_collateral = sum(
                p.total_collateral_usd
                * (
                    sum(c.liquidation_threshold * c.amount_usd for c in p.collateral)
                    / max(sum(c.amount_usd for c in p.collateral), 1)
                )
                for p in self.positions if p.collateral
            )
            self.overall_health_factor = total_weighted_collateral / self.total_debt_usd
            if self.overall_health_factor >= 2.0:
                self.overall_risk_tier = RiskTier.SAFE
            elif self.overall_health_factor >= 1.5:
                self.overall_risk_tier = RiskTier.WATCH
            elif self.overall_health_factor >= 1.1:
                self.overall_risk_tier = RiskTier.DANGER
            else:
                self.overall_risk_tier = RiskTier.CRITICAL
        self._detect_clusters()
        self._simulate_cascades()
        return self

    def _detect_clusters(self) -> None:
        """Group positions with similar liquidation prices."""
        cluster_map: dict[tuple[str, str], list[ProtocolPosition]] = {}
        for pos in self.positions:
            if pos.liquidation_price_usd is not None and pos.collateral:
                primary = max(pos.collateral, key=lambda c: c.amount_usd)
                key = (pos.chain, primary.asset)
                if key not in cluster_map:
                    cluster_map[key] = []
                cluster_map[key].append(pos)
        for (chain, asset), positions in cluster_map.items():
            if not positions:
                continue
            prices = [p.liquidation_price_usd for p in positions if p.liquidation_price_usd]
            if not prices:
                continue
            min_p, max_p = min(prices), max(prices)
            if (max_p - min_p) / max(min_p, 1) <= 0.15 or len(positions) == 1:
                self.liquidation_clusters.append(
                    LiquidationCluster(
                        chain=chain, primary_collateral=asset,
                        price_range_low=min_p, price_range_high=max_p,
                        wallet_count=len(positions),
                        total_debt_usd=sum(p.total_debt_usd for p in positions),
                        total_collateral_usd=sum(p.total_collateral_usd for p in positions),
                    )
                )

    def _simulate_cascades(self) -> None:
        """Run cascade simulation scenarios."""
        if not self.positions:
            return
        impacted = [p for p in self.positions if p.risk_tier in (RiskTier.DANGER, RiskTier.CRITICAL)]
        if impacted:
            self.cascade_scenarios.append(
                CascadeScenario(
                    name="10% Collateral Drop",
                    description="If primary collateral prices drop 10%, simulate which positions get liquidated",
                    liquidated_positions=sum(1 for p in impacted if p.risk_tier == RiskTier.CRITICAL),
                    total_liquidated_value_usd=sum(p.total_debt_usd for p in impacted if p.risk_tier == RiskTier.CRITICAL),
                    secondary_affected_positions=sum(1 for p in impacted if p.risk_tier == RiskTier.DANGER),
                    total_secondary_value_usd=sum(p.total_debt_usd for p in impacted if p.risk_tier == RiskTier.DANGER),
                )
            )
        all_at_risk = [p for p in self.positions if p.risk_tier in (RiskTier.WATCH, RiskTier.DANGER, RiskTier.CRITICAL)]
        if all_at_risk:
            self.cascade_scenarios.append(
                CascadeScenario(
                    name="25% Flash Crash",
                    description="Simulate a 25% market-wide flash crash and cascade effects",
                    liquidated_positions=sum(1 for p in all_at_risk if p.risk_tier in (RiskTier.DANGER, RiskTier.CRITICAL)),
                    total_liquidated_value_usd=sum(p.total_debt_usd for p in all_at_risk if p.risk_tier in (RiskTier.DANGER, RiskTier.CRITICAL)),
                    secondary_affected_positions=sum(1 for p in self.positions if p.risk_tier == RiskTier.WATCH),
                    total_secondary_value_usd=sum(p.total_debt_usd for p in self.positions if p.risk_tier == RiskTier.WATCH),
                )
            )
        critical = [p for p in self.positions if p.risk_tier == RiskTier.CRITICAL]
        if critical:
            total_critical_value = sum(p.total_debt_usd for p in critical)
            market_impact = min(total_critical_value / 1_000_000 * 0.01, 0.25)
            self.cascade_scenarios.append(
                CascadeScenario(
                    name="Worst-Case Cascade",
                    description="All CRITICAL positions liquidate simultaneously - worst-case scenario",
                    liquidated_positions=len(critical),
                    total_liquidated_value_usd=total_critical_value,
                    secondary_affected_positions=sum(1 for p in self.positions if p.risk_tier == RiskTier.DANGER),
                    total_secondary_value_usd=sum(p.total_debt_usd for p in self.positions if p.risk_tier == RiskTier.DANGER),
                    market_impact_pct=market_impact,
                )
            )

    def report(self, format: str = "text") -> str:
        """Generate a human-readable or JSON report."""
        from app.domains.analysis.liquidation.report import generate_report
        return generate_report(self, format)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "generated_at": self.generated_at,
            "chains_analyzed": self.chains_analyzed,
            "total_collateral_usd": round(self.total_collateral_usd, 2),
            "total_debt_usd": round(self.total_debt_usd, 2),
            "overall_health_factor": round(self.overall_health_factor, 4) if self.overall_health_factor else None,
            "overall_risk_tier": self.overall_risk_tier.value,
            "position_count": len(self.positions),
            "positions": [p.to_dict() for p in self.positions],
            "cascade_scenarios": [s.to_dict() for s in self.cascade_scenarios],
            "liquidation_clusters": [c.to_dict() for c in self.liquidation_clusters],
            "warnings": self.warnings,
            "errors": self.errors,
        }
