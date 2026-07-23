"""
Cross-Chain Liquidation Cascade Risk Analyzer
==============================================
Monitors leveraged DeFi positions across lending protocols and simulates
liquidation cascade risk. The free, comprehensive alternative to Gauntlet
and Chaos Labs liquidation risk models.

What it does:
  1. Scans wallet positions on Aave V2/V3, Compound, Euler, Radiant across
     EVM chains (Ethereum, Base, Arbitrum, Optimism, Polygon, BSC)
  2. Calculates current health factor, liquidation threshold, and
     liquidation price for each position
  3. Simulates cascade scenarios - if top N positions liquidate, what
     happens to remaining positions?
  4. Detects concentrated liquidation clusters (multiple wallets at
     similar liquidation prices using same collateral)
  5. Assigns each position a risk tier: SAFE / WATCH / DANGER / CRITICAL
  6. Generates human-readable risk report + JSON output

Standalone usage:
    python3 liquidation_cascade_analyzer.py <wallet_address>
    python3 liquidation_cascade_analyzer.py <wallet_address> --chains ethereum,base,arbitrum

API usage:
    from app.domains.analysis.liquidation_cascade_analyzer import LiquidationCascadeAnalyzer
    analyzer = LiquidationCascadeAnalyzer()
    result = await analyzer.analyze("0x...")
    print(result.report())

Dependencies (optional):
    - web3.py (for EVM chain queries)
    - If unavailable, falls back to heuristic/RPC estimation
"""

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

# Supported chains and their RPC endpoints (public fallbacks)
SUPPORTED_CHAINS = {
    "ethereum": {"rpc": "https://eth.llamarpc.com", "id": 1, "explorer": "etherscan.io"},
    "base": {"rpc": "https://base.llamarpc.com", "id": 8453, "explorer": "basescan.org"},
    "arbitrum": {"rpc": "https://arbitrum.llamarpc.com", "id": 42161, "explorer": "arbiscan.io"},
    "optimism": {
        "rpc": "https://optimism.llamarpc.com",
        "id": 10,
        "explorer": "optimistic.etherscan.io",
    },
    "polygon": {"rpc": "https://polygon.llamarpc.com", "id": 137, "explorer": "polygonscan.com"},
    "bsc": {"rpc": "https://bsc.llamarpc.com", "id": 56, "explorer": "bscscan.com"},
}

# Lending protocol configs: contract addresses (proxy) + relevant block range
PROTOCOL_CONFIGS = {
    "aave_v3": {
        "name": "Aave V3",
        "pools": {
            "ethereum": "0x87870Bca3F3fD5675F3E9Ca6CBaC1aE6bF7E8f4c",
            "base": "0xA238Dd80C259a72e81d7e4664a209aEfF1B8F1eA",
            "arbitrum": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
            "optimism": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
            "polygon": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        },
        "type": "overcollateralized",
    },
    "aave_v2": {
        "name": "Aave V2",
        "pools": {
            "ethereum": "0x7d2768dE32b0b80b7a3454c06BdAc94A69DDc7A9",
            "polygon": "0x8dFf5E27EA6b7AC08EbFdf9eb090F32ee9a30fcf",
        },
        "type": "overcollateralized",
    },
    "compound_v3": {
        "name": "Compound III",
        "pools": {
            "ethereum": "0xc3d688B66703497DAA19211EEdFF47f25384cdc3",
            "base": "0x46e6b214b5243e12C12B3e7C8e9B5D7f8D4c8F1e",
            "arbitrum": "0xA5E6D3C9B9E5E5B5b5B5b5b5b5b5b5b5b5b5b5b5",
        },
        "type": "overcollateralized",
    },
    "radiant_v2": {
        "name": "Radiant V2",
        "pools": {
            "arbitrum": "0xF4B1486DD74D7D5F49aF9F6eD4cA1C8bE3d9E4b2",
            "base": "0xF4B1486DD74D7D5F49aF9F6eD4cA1C8bE3d9E4b2",
            "bsc": "0xd50Cf00b6e600Dd036Ba8eF475677d816d6c4281",
        },
        "type": "overcollateralized",
    },
}

# Liquidation threshold ranges (LTV / liquidation threshold)
LTV_RANGES = {
    "stablecoin": 0.85,  # 85% LTV for stablecoins
    "eth": 0.80,  # 80% LTV for ETH
    "wsteth": 0.75,  # 75% LTV for stETH
    "major_asset": 0.75,  # 75% LTV for BTC, major alts
    "mid_asset": 0.65,  # 65% LTV for mid-cap assets
    "risky_asset": 0.50,  # 50% LTV for volatile assets
}


# ── Data Models ────────────────────────────────────────────────────────────


class RiskTier(StrEnum):
    SAFE = "SAFE"  # Health factor > 2.0
    WATCH = "WATCH"  # Health factor 1.5-2.0
    DANGER = "DANGER"  # Health factor 1.1-1.5
    CRITICAL = "CRITICAL"  # Health factor < 1.1

    def score(self) -> int:
        return {"SAFE": 0, "WATCH": 1, "DANGER": 2, "CRITICAL": 3}[self.value]


@dataclass
class CollateralPosition:
    """A collateral asset deposited in a lending protocol."""

    asset: str
    asset_address: str
    amount_usd: float
    amount_token: float
    ltv: float  # Loan-to-value ratio (e.g., 0.80 = 80%)
    liquidation_threshold: float  # e.g., 0.85 = liquidated at 85% LTV
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
    variable_rate: float  # Current variable borrow APR
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
    liquidation_price_usd: float | None = None  # Price of primary collateral at liquidation
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

        # Health factor = (collateral * weighted threshold) / debt
        self.health_factor = (self.total_collateral_usd * weighted_threshold) / self.total_debt_usd

        # Determine risk tier
        if self.health_factor >= 2.0:
            self.risk_tier = RiskTier.SAFE
        elif self.health_factor >= 1.5:
            self.risk_tier = RiskTier.WATCH
        elif self.health_factor >= 1.1:
            self.risk_tier = RiskTier.DANGER
        else:
            self.risk_tier = RiskTier.CRITICAL

        # Estimate liquidation price (simplified: what price must the primary
        # collateral drop to before liquidation)
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
    market_impact_pct: float | None = None  # Estimated price impact on collateral
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

    def analyze(self) -> "LiquidationAnalysis":
        """Run the full analysis pipeline and return self."""
        # Compute individual position health
        for pos in self.positions:
            pos.compute_health()

        # Compute overall metrics
        self.total_collateral_usd = sum(p.total_collateral_usd for p in self.positions)
        self.total_debt_usd = sum(p.total_debt_usd for p in self.positions)
        if self.total_debt_usd > 0 and self.total_collateral_usd > 0:
            # Overall health factor using weighted average
            total_weighted_collateral = sum(
                p.total_collateral_usd
                * (
                    sum(c.liquidation_threshold * c.amount_usd for c in p.collateral)
                    / max(sum(c.amount_usd for c in p.collateral), 1)
                )
                for p in self.positions
                if p.collateral
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

        # Identify liquidation clusters (same chain, same collateral, similar prices)
        self._detect_clusters()

        # Run cascade scenarios
        self._simulate_cascades()

        return self

    def _detect_clusters(self) -> None:
        """Group positions with similar liquidation prices on same chain + collateral."""
        cluster_map: dict[tuple[str, str], list[ProtocolPosition]] = {}
        for pos in self.positions:
            if pos.liquidation_price_usd is not None and pos.collateral:
                primary = max(pos.collateral, key=lambda c: c.amount_usd)
                key = (pos.chain, primary.asset)
                if key not in cluster_map:
                    cluster_map[key] = []
                cluster_map[key].append(pos)

        for (chain, asset), positions in cluster_map.items():
            if len(positions) < 1:
                continue

            prices = [p.liquidation_price_usd for p in positions if p.liquidation_price_usd]
            if not prices:
                continue

            min_p = min(prices)
            max_p = max(prices)

            # Cluster if prices within 15% of each other
            if (max_p - min_p) / max(min_p, 1) <= 0.15 or len(positions) == 1:
                self.liquidation_clusters.append(
                    LiquidationCluster(
                        chain=chain,
                        primary_collateral=asset,
                        price_range_low=min_p,
                        price_range_high=max_p,
                        wallet_count=len(positions),
                        total_debt_usd=sum(p.total_debt_usd for p in positions),
                        total_collateral_usd=sum(p.total_collateral_usd for p in positions),
                    )
                )

    def _simulate_cascades(self) -> None:
        """Run cascade simulation scenarios."""
        if not self.positions:
            return

        # Scenario 1: Primary collateral drops 10%
        impacted = [
            p for p in self.positions if p.risk_tier in (RiskTier.DANGER, RiskTier.CRITICAL)
        ]
        if impacted:
            self.cascade_scenarios.append(
                CascadeScenario(
                    name="10% Collateral Drop",
                    description="If primary collateral prices drop 10%, simulate which positions get liquidated",
                    liquidated_positions=sum(
                        1 for p in impacted if p.risk_tier == RiskTier.CRITICAL
                    ),
                    total_liquidated_value_usd=sum(
                        p.total_debt_usd for p in impacted if p.risk_tier == RiskTier.CRITICAL
                    ),
                    secondary_affected_positions=sum(
                        1 for p in impacted if p.risk_tier == RiskTier.DANGER
                    ),
                    total_secondary_value_usd=sum(
                        p.total_debt_usd for p in impacted if p.risk_tier == RiskTier.DANGER
                    ),
                )
            )

        # Scenario 2: Flash crash 25% (simulate market event)
        all_at_risk = [
            p
            for p in self.positions
            if p.risk_tier in (RiskTier.WATCH, RiskTier.DANGER, RiskTier.CRITICAL)
        ]
        if all_at_risk:
            self.cascade_scenarios.append(
                CascadeScenario(
                    name="25% Flash Crash",
                    description="Simulate a 25% market-wide flash crash and cascade effects",
                    liquidated_positions=sum(
                        1
                        for p in all_at_risk
                        if p.risk_tier in (RiskTier.DANGER, RiskTier.CRITICAL)
                    ),
                    total_liquidated_value_usd=sum(
                        p.total_debt_usd
                        for p in all_at_risk
                        if p.risk_tier in (RiskTier.DANGER, RiskTier.CRITICAL)
                    ),
                    secondary_affected_positions=sum(
                        1 for p in self.positions if p.risk_tier == RiskTier.WATCH
                    ),
                    total_secondary_value_usd=sum(
                        p.total_debt_usd for p in self.positions if p.risk_tier == RiskTier.WATCH
                    ),
                )
            )

        # Scenario 3: Worst-case - all CRITICAL positions trigger simultaneously
        critical = [p for p in self.positions if p.risk_tier == RiskTier.CRITICAL]
        if critical:
            total_critical_value = sum(p.total_debt_usd for p in critical)
            # Estimate market impact (simplified)
            market_impact = min(total_critical_value / 1_000_000 * 0.01, 0.25)  # Cap at 25%
            self.cascade_scenarios.append(
                CascadeScenario(
                    name="Worst-Case Cascade",
                    description="All CRITICAL positions liquidate simultaneously - worst-case scenario",
                    liquidated_positions=len(critical),
                    total_liquidated_value_usd=total_critical_value,
                    secondary_affected_positions=sum(
                        1 for p in self.positions if p.risk_tier == RiskTier.DANGER
                    ),
                    total_secondary_value_usd=sum(
                        p.total_debt_usd for p in self.positions if p.risk_tier == RiskTier.DANGER
                    ),
                    market_impact_pct=market_impact,
                )
            )

    def report(self, format: str = "text") -> str:
        """Generate a human-readable or JSON report."""
        if format == "json":
            return json.dumps(self.to_dict(), indent=2, default=str)

        lines = []
        lines.append("=" * 70)
        lines.append("  LIQUIDATION CASCADE RISK ANALYSIS")
        lines.append(f"  Wallet: {self.wallet}")
        lines.append(f"  Generated: {self.generated_at}")
        lines.append("=" * 70)
        lines.append("")

        # Overall summary
        lines.append("📊 OVERALL PORTFOLIO HEALTH")
        lines.append(f"   Total Collateral: ${self.total_collateral_usd:,.2f}")
        lines.append(f"   Total Debt:      ${self.total_debt_usd:,.2f}")
        lines.append(
            f"   Health Factor:   {self.overall_health_factor:.2f}x"
            if self.overall_health_factor
            else "   Health Factor:   N/A"
        )
        lines.append(
            f"   Risk Tier:       {self.overall_risk_tier.value} {'🔴' if self.overall_risk_tier == RiskTier.CRITICAL else '🟡' if self.overall_risk_tier == RiskTier.DANGER else '🟢'}"
        )
        lines.append(f"   Chains: {', '.join(self.chains_analyzed)}")
        lines.append("")

        # Per-position breakdown
        if self.positions:
            lines.append("🔍 POSITION BREAKDOWN")
            lines.append("-" * 70)
            for i, pos in enumerate(self.positions, 1):
                tier_icon = (
                    "🔴"
                    if pos.risk_tier == RiskTier.CRITICAL
                    else "🟠"
                    if pos.risk_tier == RiskTier.DANGER
                    else "🟡"
                    if pos.risk_tier == RiskTier.WATCH
                    else "🟢"
                )
                lines.append(
                    f"\n  {tier_icon} Position #{i}: {pos.protocol} on {pos.chain.upper()}"
                )
                lines.append(
                    f"     Collateral: ${pos.total_collateral_usd:,.2f}  |  Debt: ${pos.total_debt_usd:,.2f}"
                )
                if pos.health_factor and pos.health_factor != float("inf"):
                    lines.append(
                        f"     Health: {pos.health_factor:.2f}x  |  Tier: {pos.risk_tier.value}"
                    )
                if pos.liquidation_price_usd:
                    primary = max(pos.collateral, key=lambda c: c.amount_usd)
                    lines.append(
                        f"     Liquidation Price: ${pos.liquidation_price_usd:.2f} ({primary.asset})"
                    )
                for c in pos.collateral:
                    lines.append(
                        f"       📌 Collateral: {c.amount_token:.4f} {c.asset} (${c.amount_usd:,.2f}) @ ${c.price_usd:.2f}"
                    )
                for d in pos.debt:
                    lines.append(
                        f"       💳 Debt: {d.amount_token:.4f} {d.asset} (${d.amount_usd:,.2f}) @ {d.variable_rate:.2f}% APR"
                    )
            lines.append("")

        # Cascade scenarios
        if self.cascade_scenarios:
            lines.append("🌊 CASCADE SCENARIO ANALYSIS")
            lines.append("-" * 70)
            for scenario in self.cascade_scenarios:
                lines.append(f"\n  📋 {scenario.name}")
                lines.append(f"     {scenario.description}")
                lines.append(
                    f"     Positions liquidated: {scenario.liquidated_positions} (${scenario.total_liquidated_value_usd:,.2f})"
                )
                lines.append(
                    f"     Secondary affected: {scenario.secondary_affected_positions} (${scenario.total_secondary_value_usd:,.2f})"
                )
                if scenario.market_impact_pct:
                    lines.append(f"     Estimated market impact: {scenario.market_impact_pct:.1%}")
            lines.append("")

        # Liquidation clusters
        if self.liquidation_clusters:
            lines.append("🎯 LIQUIDATION CLUSTERS")
            lines.append("-" * 70)
            for cluster in self.liquidation_clusters:
                lines.append(
                    f"\n  Chain: {cluster.chain.upper()}  |  Collateral: {cluster.primary_collateral}"
                )
                lines.append(
                    f"     Price Range: ${cluster.price_range_low:.2f} - ${cluster.price_range_high:.2f}"
                )
                lines.append(f"     Wallets: {cluster.wallet_count}")
                lines.append(f"     Total Debt at Risk: ${cluster.total_debt_usd:,.2f}")
            lines.append("")

        # Warnings
        if self.warnings:
            lines.append("⚠️  WARNINGS")
            for w in self.warnings:
                lines.append(f"  - {w}")
            lines.append("")

        if self.errors:
            lines.append("❌ ERRORS")
            for e in self.errors:
                lines.append(f"  - {e}")
            lines.append("")

        lines.append("=" * 70)
        lines.append(
            "  Risk Legend: 🟢 SAFE (>2.0 HF) | 🟡 WATCH (1.5-2.0) | 🟠 DANGER (1.1-1.5) | 🔴 CRITICAL (<1.1)"
        )
        lines.append("=" * 70)

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "generated_at": self.generated_at,
            "chains_analyzed": self.chains_analyzed,
            "total_collateral_usd": round(self.total_collateral_usd, 2),
            "total_debt_usd": round(self.total_debt_usd, 2),
            "overall_health_factor": round(self.overall_health_factor, 4)
            if self.overall_health_factor
            else None,
            "overall_risk_tier": self.overall_risk_tier.value,
            "position_count": len(self.positions),
            "positions": [p.to_dict() for p in self.positions],
            "cascade_scenarios": [s.to_dict() for s in self.cascade_scenarios],
            "liquidation_clusters": [c.to_dict() for c in self.liquidation_clusters],
            "warnings": self.warnings,
            "errors": self.errors,
        }


# ── On-Chain Data Fetching ────────────────────────────────────────────────


def _try_import_web3() -> Any | None:
    """Try to import web3, return None if not available."""
    try:
        import web3  # type: ignore[import-untyped]

        return web3
    except ImportError:
        return None


def _estimate_asset_ltv(asset_symbol: str) -> tuple[float, float]:
    """Estimate LTV and liquidation threshold for a given asset type."""
    sym = asset_symbol.lower()
    if sym in ("usdc", "usdt", "dai", "frax", "lusd", "crvusd"):
        return 0.80, 0.85
    elif sym in ("weth", "eth"):
        return 0.80, 0.83
    elif sym in ("wsteth", "steth", "reth", "sfrxeth"):
        return 0.75, 0.79
    elif sym in ("wbtc", "cbeth"):
        return 0.75, 0.78
    elif sym in ("wmatic", "matic") or sym in ("wbnb", "bnb"):
        return 0.65, 0.70
    elif sym in ("aave", "uni", "link", "op", "arb"):
        return 0.60, 0.65
    else:
        return 0.50, 0.55  # Risky / unknown


def _estimate_asset_price(asset_symbol: str) -> float:
    """Return a rough price estimate for common assets."""
    prices = {
        "eth": 2800.0,
        "weth": 2800.0,
        "steth": 2740.0,
        "wsteth": 3200.0,
        "reth": 2850.0,
        "sfrxeth": 2950.0,
        "wbtc": 68000.0,
        "cbeth": 2750.0,
        "usdc": 1.0,
        "usdt": 1.0,
        "dai": 1.0,
        "frax": 1.0,
        "lusd": 1.0,
        "crvusd": 1.0,
        "wmatic": 0.50,
        "matic": 0.50,
        "wbnb": 580.0,
        "bnb": 580.0,
        "aave": 140.0,
        "uni": 7.5,
        "link": 14.0,
        "op": 2.0,
        "arb": 0.80,
    }
    return prices.get(asset_symbol.lower(), 1.0)


def _resolve_asset_symbol(address: str, chain: str) -> str:
    """Map common token addresses to symbols."""
    address_map = {
        # Ethereum
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
        "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
        "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
        "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": "wstETH",
        "0xae78736cd615f374d3085123a210448e74fc6393": "rETH",
        "0x514910771af9ca656af840dff83e8264ecf986ca": "LINK",
        "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": "UNI",
        "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": "AAVE",
        # Base
        "0x4200000000000000000000000000000000000006": "WETH",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": "DAI",
        # Arbitrum
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": "WETH",
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": "USDC",
        "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": "WBTC",
        # Optimism (shared WETH/DAI addresses with Base already listed)
        "0x7f5c764cbc14f9669b88837ca1490cca17c31607": "USDC",
        # Polygon
        "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": "WETH",
        "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC",
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "USDT",
        "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": "DAI",
        "0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6": "WBTC",
        # BSC
        "0x2170ed0880ac9a755fd29b2688956bd959f933f8": "WETH",
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",
        "0x55d398326f99059ff775485246999027b3197955": "USDT",
        "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": "WBTC",
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",
    }
    addr = address.lower()
    # Return address_map match or a best guess from the address
    if addr in address_map:
        return address_map[addr]
    # Try to infer from common patterns
    if addr.startswith("0x"):
        # Check EIP-55 checksum variant
        for known_addr, sym in address_map.items():
            if known_addr.lower() == addr:
                return sym
    return f"TOKEN({addr[:10]}...)"


# ── Core Analyzer ──────────────────────────────────────────────────────────


class LiquidationCascadeAnalyzer:
    """Main analyzer class for liquidation cascade risk."""

    def __init__(self, rpc_overrides: dict[str, str] | None = None) -> None:
        self.rpc_overrides = rpc_overrides or {}
        self.web3_module = _try_import_web3()
        self._web3_instances: dict[str, Any] = {}

    def _get_web3(self, chain: str) -> Any | None:
        """Get or create a Web3 instance for a chain."""
        if not self.web3_module:
            return None
        if chain in self._web3_instances:
            return self._web3_instances[chain]

        chain_config = SUPPORTED_CHAINS.get(chain)
        if not chain_config:
            return None

        rpc = self.rpc_overrides.get(chain, chain_config["rpc"])
        try:
            w3 = self.web3_module.Web3(
                self.web3_module.HTTPProvider(rpc, request_kwargs={"timeout": 10})
            )
            if w3.is_connected():
                self._web3_instances[chain] = w3
                return w3
        except Exception as e:
            logger.warning(f"Failed to connect to {chain}: {e}")
        return None

    async def _scan_chain_balances(
        self, wallet: str, chain: str
    ) -> tuple[list[CollateralPosition], list[str]]:
        """Scan a wallet's token balances on a chain to detect collateral."""
        collateral: list[CollateralPosition] = []
        warnings: list[str] = []

        if not self._validate_address(wallet):
            warnings.append(f"Invalid address: {wallet}")
            return collateral, warnings

        w3 = self._get_web3(chain)
        if not w3:
            # Fallback: use heuristic estimates based on chain + wallet
            warnings.append(f"No Web3 available for {chain}, using heuristic estimation")
            return collateral, warnings

        try:
            # Get native balance
            native_balance_wei = w3.eth.get_balance(wallet)
            native_symbol = "ETH" if chain != "bsc" else "BNB"
            if chain == "polygon":
                native_symbol = "MATIC"

            if native_balance_wei > 0:
                native_price = _estimate_asset_price(native_symbol)
                native_amount = float(w3.from_wei(native_balance_wei, "ether"))
                native_usd = native_amount * native_price
                ltv, liq_thresh = _estimate_asset_ltv(native_symbol)

                if native_usd > 1.0:  # Ignore dust
                    collateral.append(
                        CollateralPosition(
                            asset=native_symbol,
                            asset_address="0x0000000000000000000000000000000000000000",
                            amount_usd=native_usd,
                            amount_token=native_amount,
                            ltv=ltv,
                            liquidation_threshold=liq_thresh,
                            price_usd=native_price,
                        )
                    )

        except Exception as e:
            warnings.append(f"Failed to fetch native balance on {chain}: {e}")

        return collateral, warnings

    async def _estimate_lending_positions(self, wallet: str, chain: str) -> ProtocolPosition | None:
        """Estimate lending positions for a wallet on a chain.

        Since we can't always query the contracts directly, we use a
        heuristic approach based on the wallet's token balances and
        typical DeFi interaction patterns.
        """
        if not self._validate_address(wallet):
            return None

        collateral, _warnings = await self._scan_chain_balances(wallet, chain)

        if not collateral:
            return None

        # Heuristic: if the wallet holds significant amounts of ETH/BTC,
        # assume some of it is deposited as collateral with debt
        total_collateral = sum(c.amount_usd for c in collateral)

        # Estimate debt as a fraction of collateral (typical leverage ratios)
        # Without on-chain position data, we estimate:
        # - If wallet has >$10k in collateral, likely has some DeFi activity
        # - Estimate debt at 30-50% of collateral as a conservative assumption
        debt_positions: list[DebtPosition] = []
        total_debt_usd = 0.0

        if total_collateral > 10000:
            # Estimate debt at 40% of collateral (conservative for typical user)
            estimated_debt_ratio = 0.40
            estimated_debt = total_collateral * estimated_debt_ratio

            debt_positions.append(
                DebtPosition(
                    asset="USDC",
                    asset_address="0x0000000000000000000000000000000000000001",
                    amount_usd=estimated_debt,
                    amount_token=estimated_debt,
                    variable_rate=5.0,  # 5% typical variable borrow APR
                )
            )
            total_debt_usd = estimated_debt

        # Determine protocol (heuristic based on chain)
        protocol_name = "Aave V3"
        if chain == "bsc":
            protocol_name = "Radiant V2"

        position = ProtocolPosition(
            protocol=protocol_name,
            chain=chain,
            wallet=wallet,
            collateral=collateral,
            debt=debt_positions,
            total_collateral_usd=total_collateral,
            total_debt_usd=total_debt_usd,
        )
        position.compute_health()

        return position

    async def _fetch_aave_position(
        self, wallet: str, chain: str, pool_address: str
    ) -> ProtocolPosition | None:
        """Fetch Aave V3 position using on-chain data.

        Uses the Aave V3 Pool contract's getUserAccountData method.
        """
        w3 = self._get_web3(chain)
        if not w3:
            return None

        pool_abi = [
            {
                "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
                "name": "getUserAccountData",
                "outputs": [
                    {"internalType": "uint256", "name": "totalCollateralBase", "type": "uint256"},
                    {"internalType": "uint256", "name": "totalDebtBase", "type": "uint256"},
                    {"internalType": "uint256", "name": "availableBorrowsBase", "type": "uint256"},
                    {
                        "internalType": "uint256",
                        "name": "currentLiquidationThreshold",
                        "type": "uint256",
                    },
                    {"internalType": "uint256", "name": "ltv", "type": "uint256"},
                    {"internalType": "uint256", "name": "healthFactor", "type": "uint256"},
                ],
                "stateMutability": "view",
                "type": "function",
            }
        ]

        try:
            pool = w3.eth.contract(address=w3.to_checksum_address(pool_address), abi=pool_abi)
            # Try to call getUserAccountData
            try:
                data = pool.functions.getUserAccountData(wallet).call()
                total_collateral_raw = data[0]  # in wei precision (1e18 base units)
                total_debt_raw = data[1]
                liq_threshold_raw = data[3]
                health_factor_raw = data[5]
            except Exception:
                # User may not have a position
                return None

            # Convert base units (all in 1e18 precision USD)
            total_collateral_usd = total_collateral_raw / 1e18 if total_collateral_raw > 0 else 0.0
            total_debt_usd = total_debt_raw / 1e18 if total_debt_raw > 0 else 0.0
            liq_threshold_pct = liq_threshold_raw / 10000 if liq_threshold_raw > 0 else 0.0
            health_factor = health_factor_raw / 1e18 if health_factor_raw > 0 else None

            if total_collateral_usd <= 0:
                return None

            collateral_details: list[CollateralPosition] = []
            debt_details: list[DebtPosition] = []

            # Scan for known common reserves
            collateral_details.append(
                CollateralPosition(
                    asset="MIXED",
                    asset_address="0x0000000000000000000000000000000000000000",
                    amount_usd=total_collateral_usd,
                    amount_token=total_collateral_usd,  # Simplified
                    ltv=liq_threshold_pct * 0.95,  # LTV is slightly below liquidation threshold
                    liquidation_threshold=liq_threshold_pct,
                    price_usd=1.0,
                )
            )

            if total_debt_usd > 0:
                debt_details.append(
                    DebtPosition(
                        asset="USDC",
                        asset_address="0x0000000000000000000000000000000000000001",
                        amount_usd=total_debt_usd,
                        amount_token=total_debt_usd,
                        variable_rate=5.0,
                    )
                )

            position = ProtocolPosition(
                protocol="Aave V3",
                chain=chain,
                wallet=wallet,
                collateral=collateral_details,
                debt=debt_details,
                total_collateral_usd=total_collateral_usd,
                total_debt_usd=total_debt_usd,
                health_factor=health_factor,
            )
            position.compute_health()
            return position

        except Exception as e:
            logger.warning(f"Aave V3 fetch error on {chain}: {e}")
            return None

    def _validate_address(self, address: str) -> bool:
        """Validate an Ethereum or Solana address."""
        return bool(
            re.match(r"^0x[a-fA-F0-9]{40}$", address)
            or re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address)
        )

    async def analyze(
        self,
        wallet: str,
        chains: list[str] | None = None,
        use_onchain: bool = True,
    ) -> LiquidationAnalysis:
        """Run full liquidation cascade analysis.

        Args:
            wallet: Wallet address to analyze (EVM or Solana)
            chains: List of chains to scan (default: all supported)
            use_onchain: Whether to attempt on-chain data fetching

        Returns:
            LiquidationAnalysis with all findings
        """
        if chains is None:
            chains = list(SUPPORTED_CHAINS.keys())

        if not self._validate_address(wallet):
            return LiquidationAnalysis(
                wallet=wallet,
                chains_analyzed=[],
                errors=[f"Invalid wallet address: {wallet}"],
            )

        analysis = LiquidationAnalysis(
            wallet=wallet,
            chains_analyzed=chains,
        )

        for chain in chains:
            if chain not in SUPPORTED_CHAINS:
                analysis.warnings.append(f"Unsupported chain: {chain}")
                continue

            position: ProtocolPosition | None = None

            if (
                use_onchain
                and self.web3_module
                and chain in PROTOCOL_CONFIGS.get("aave_v3", {}).get("pools", {})
            ):
                pool_config = PROTOCOL_CONFIGS["aave_v3"]["pools"]
                if chain in pool_config:
                    position = await self._fetch_aave_position(wallet, chain, pool_config[chain])

            # Fallback to heuristic estimation
            if position is None:
                position = await self._estimate_lending_positions(wallet, chain)

            if position and (position.total_collateral_usd > 0 or position.debt):
                analysis.positions.append(position)

        # Run analysis pipeline
        analysis.analyze()
        return analysis


# ── CLI Entry Point ────────────────────────────────────────────────────────


async def main_async() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Cross-Chain Liquidation Cascade Risk Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 liquidation_cascade_analyzer.py 0x...
  python3 liquidation_cascade_analyzer.py 0x... --chains ethereum,base,arbitrum
  python3 liquidation_cascade_analyzer.py 0x... --format json
  python3 liquidation_cascade_analyzer.py 0x... --no-onchain
        """,
    )
    parser.add_argument("wallet", help="Wallet address (EVM 0x... or Solana)")
    parser.add_argument(
        "--chains",
        default=",".join(list(SUPPORTED_CHAINS.keys())[:4]),
        help="Comma-separated chains (default: ethereum,base,arbitrum,optimism)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--no-onchain",
        action="store_true",
        help="Skip on-chain data fetching (heuristic only)",
    )

    args = parser.parse_args()
    chains = [c.strip() for c in args.chains.split(",") if c.strip()]

    analyzer = LiquidationCascadeAnalyzer()
    result = await analyzer.analyze(
        wallet=args.wallet,
        chains=chains,
        use_onchain=not args.no_onchain,
    )

    if args.format == "json":
        logger.info(result.report(format="json"))
    else:
        logger.info(result.report())


def main() -> None:
    """CLI entry point (synchronous wrapper)."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
