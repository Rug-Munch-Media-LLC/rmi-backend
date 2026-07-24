"""
Liquidation Cascade — Report Generation
=========================================
"""
from __future__ import annotations

import json

from app.domains.analysis.liquidation.models import LiquidationAnalysis, RiskTier


def generate_report(analysis: LiquidationAnalysis, format: str = "text") -> str:
    """Generate a human-readable or JSON report."""
    if format == "json":
        return json.dumps(analysis.to_dict(), indent=2, default=str)

    lines = []
    lines.append("=" * 70)
    lines.append("  LIQUIDATION CASCADE RISK ANALYSIS")
    lines.append(f"  Wallet: {analysis.wallet}")
    lines.append(f"  Generated: {analysis.generated_at}")
    lines.append("=" * 70)
    lines.append("")
    lines.append("📊 OVERALL PORTFOLIO HEALTH")
    lines.append(f"   Total Collateral: ${analysis.total_collateral_usd:,.2f}")
    lines.append(f"   Total Debt:      ${analysis.total_debt_usd:,.2f}")
    lines.append(
        f"   Health Factor:   {analysis.overall_health_factor:.2f}x"
        if analysis.overall_health_factor else "   Health Factor:   N/A"
    )
    tier_icon = "🔴" if analysis.overall_risk_tier == RiskTier.CRITICAL else "🟡" if analysis.overall_risk_tier == RiskTier.DANGER else "🟢"
    lines.append(f"   Risk Tier:       {analysis.overall_risk_tier.value} {tier_icon}")
    lines.append(f"   Chains: {', '.join(analysis.chains_analyzed)}")
    lines.append("")

    if analysis.positions:
        lines.append("🔍 POSITION BREAKDOWN")
        lines.append("-" * 70)
        for i, pos in enumerate(analysis.positions, 1):
            tier_icon = "🔴" if pos.risk_tier == RiskTier.CRITICAL else "🟠" if pos.risk_tier == RiskTier.DANGER else "🟡" if pos.risk_tier == RiskTier.WATCH else "🟢"
            lines.append(f"\n  {tier_icon} Position #{i}: {pos.protocol} on {pos.chain.upper()}")
            lines.append(f"     Collateral: ${pos.total_collateral_usd:,.2f}  |  Debt: ${pos.total_debt_usd:,.2f}")
            if pos.health_factor and pos.health_factor != float("inf"):
                lines.append(f"     Health: {pos.health_factor:.2f}x  |  Tier: {pos.risk_tier.value}")
            if pos.liquidation_price_usd:
                primary = max(pos.collateral, key=lambda c: c.amount_usd)
                lines.append(f"     Liquidation Price: ${pos.liquidation_price_usd:.2f} ({primary.asset})")
            for c in pos.collateral:
                lines.append(f"       📌 Collateral: {c.amount_token:.4f} {c.asset} (${c.amount_usd:,.2f}) @ ${c.price_usd:.2f}")
            for d in pos.debt:
                lines.append(f"       💳 Debt: {d.amount_token:.4f} {d.asset} (${d.amount_usd:,.2f}) @ {d.variable_rate:.2f}% APR")
        lines.append("")

    if analysis.cascade_scenarios:
        lines.append("🌊 CASCADE SCENARIO ANALYSIS")
        lines.append("-" * 70)
        for scenario in analysis.cascade_scenarios:
            lines.append(f"\n  📋 {scenario.name}")
            lines.append(f"     {scenario.description}")
            lines.append(f"     Positions liquidated: {scenario.liquidated_positions} (${scenario.total_liquidated_value_usd:,.2f})")
            lines.append(f"     Secondary affected: {scenario.secondary_affected_positions} (${scenario.total_secondary_value_usd:,.2f})")
            if scenario.market_impact_pct:
                lines.append(f"     Estimated market impact: {scenario.market_impact_pct:.1%}")
        lines.append("")

    if analysis.liquidation_clusters:
        lines.append("🎯 LIQUIDATION CLUSTERS")
        lines.append("-" * 70)
        for cluster in analysis.liquidation_clusters:
            lines.append(f"\n  Chain: {cluster.chain.upper()}  |  Collateral: {cluster.primary_collateral}")
            lines.append(f"     Price Range: ${cluster.price_range_low:.2f} - ${cluster.price_range_high:.2f}")
            lines.append(f"     Wallets: {cluster.wallet_count}")
            lines.append(f"     Total Debt at Risk: ${cluster.total_debt_usd:,.2f}")
        lines.append("")

    if analysis.warnings:
        lines.append("⚠️  WARNINGS")
        for w in analysis.warnings:
            lines.append(f"  - {w}")
        lines.append("")
    if analysis.errors:
        lines.append("❌ ERRORS")
        for e in analysis.errors:
            lines.append(f"  - {e}")
        lines.append("")

    lines.append("=" * 70)
    lines.append("  Risk Legend: 🟢 SAFE (>2.0 HF) | 🟡 WATCH (1.5-2.0) | 🟠 DANGER (1.1-1.5) | 🔴 CRITICAL (<1.1)")
    lines.append("=" * 70)
    return "\n".join(lines)
