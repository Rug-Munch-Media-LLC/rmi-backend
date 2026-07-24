"""
Token Supply Analyzer — Main analysis orchestration, batch analysis, and CLI.

Entry point for the full token supply analysis pipeline.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from enum import Enum
from typing import Any

from app.core.logging import get_logger
from app.token_supply_analyzer.analyzers import (
    _analyze_concentration,
    _analyze_liquidity,
    _analyze_supply_mechanics,
    _compute_overall_risk,
)
from app.token_supply_analyzer.fetchers import (
    _fetch_birdeye,
    _fetch_dexscreener,
    parse_birdeye_data,
    parse_dexscreener_data,
)
from app.token_supply_analyzer.models import (
    SupplyRisk,
    TokenSupplyProfile,
    _detect_chain,
)

logger = get_logger("token_supply_analyzer")

async def analyze_token_supply(token_address: str, chain: str = "") -> TokenSupplyProfile:
    """
    Main entry point. Analyzes token supply, liquidity, and holder concentration.

    Args:
        token_address: The token contract address.
        chain: Optional chain hint (ethereum, solana, bsc, polygon, etc.).

    Returns:
        TokenSupplyProfile with all analysis results.
    """
    profile = TokenSupplyProfile(address=token_address)

    if not chain:
        chain = _detect_chain(token_address)
    profile.chain = chain

    logger.info("Analyzing token supply for %s on %s", token_address[:16], chain)

    dexscreener_data = await _fetch_dexscreener(token_address, chain)
    birdeye_data = await _fetch_birdeye(token_address)

    await parse_dexscreener_data(profile, dexscreener_data)
    await parse_birdeye_data(profile, birdeye_data)

    _analyze_supply_mechanics(profile)
    _analyze_concentration(profile)
    _analyze_liquidity(profile)
    _compute_overall_risk(profile)

    if profile.overall_risk == SupplyRisk.CRITICAL:
        profile.recommendations.insert(0, "🚨 CRITICAL: Do NOT invest. Multiple severe risk factors detected.")
    elif profile.overall_risk == SupplyRisk.HIGH:
        profile.recommendations.insert(0, "⚠ HIGH RISK: Significant red flags. Proceed with extreme caution.")
    elif profile.overall_risk == SupplyRisk.MEDIUM:
        profile.recommendations.insert(0, "⚠ MEDIUM RISK: Some concerns. Verify all claims before investing.")
    elif profile.overall_risk == SupplyRisk.LOW:
        profile.recommendations.insert(0, "✅ LOW RISK: Reasonable tokenomics. Standard precautions advised.")
    else:
        profile.recommendations.insert(0, "✅ SAFE: Tokenomics appear healthy. Always DYOR.")

    return profile

async def analyze_multiple_tokens(addresses: list[str], chain: str = "") -> list[TokenSupplyProfile]:
    """Analyze multiple tokens concurrently."""
    tasks = [analyze_token_supply(addr, chain) for addr in addresses]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    valid: list[TokenSupplyProfile] = []
    for r in results:
        if isinstance(r, TokenSupplyProfile):
            valid.append(r)
    return valid

async def main() -> None:
    """Run supply analysis from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Token Supply & Liquidity Analyzer")
    parser.add_argument("address", help="Token contract address")
    parser.add_argument("--chain", default="", help="Chain hint (ethereum, solana, bsc, polygon)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    profile = await analyze_token_supply(args.address, args.chain)

    if args.json:
        import dataclasses

        def _serialize(obj: Any) -> str:
            if isinstance(obj, Enum):
                return str(obj.value)
            if isinstance(obj, datetime):
                return obj.isoformat()
            return str(obj)

        logger.info(json.dumps(dataclasses.asdict(profile), indent=2, default=_serialize))
    else:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"TOKEN SUPPLY ANALYSIS: {profile.symbol} ({profile.name})")
        logger.info(f"{'=' * 60}")
        logger.info(f"Address: {profile.address}")
        logger.info(f"Chain:   {profile.chain}")
        logger.info("\n── Supply ──")
        logger.info(f"  Total supply:     {profile.total_supply:,.2f}")
        logger.info(f"  Circulating:      {profile.circulating_supply:,.2f}")
        logger.info(f"  Max supply:       {profile.max_supply:,.2f}")
        logger.info(f"  Burned:           {profile.burned_supply:,.2f}")
        logger.info(f"  Has mint:         {'YES' if profile.has_mint_function else 'no'} ({profile.mint_function_type})")
        logger.info(f"  Has burn:         {'YES' if profile.has_burn_function else 'no'} ({profile.burn_function_type})")
        logger.info(f"  Deflationary:     {'YES' if profile.is_deflationary else 'no'}")
        logger.info("\n── Concentration ──")
        logger.info(f"  Holders:          {profile.holder_count:,}")
        logger.info(f"  Top 10 hold:      {profile.top_10_holder_pct:.1f}%")
        logger.info(f"  Top 50 hold:      {profile.top_50_holder_pct:.1f}%")
        logger.info(f"  Deployer holds:   {profile.deployer_hold_pct:.1f}%")
        logger.info("\n── Liquidity ──")
        logger.info(f"  Lock status:      {profile.lock_status.value}")
        logger.info(f"  Lock expiry:      {profile.liquidity_lock_expiry or 'N/A'}")
        logger.info(f"  Liquidity (USD):  ${profile.pair_liquidity_usd:,.2f}")
        logger.info(f"  Pair age (days):  {profile.pair_age_days:.1f}")
        logger.info(f"  LP by deployer:   {profile.lp_token_pct_held_by_deployer:.1f}%")
        logger.info("\n── Tax ──")
        logger.info(f"  Buy tax:          {profile.buy_tax_pct:.1f}%")
        logger.info(f"  Sell tax:         {profile.sell_tax_pct:.1f}%")
        logger.info(f"  Dynamic tax:      {'YES' if profile.has_dynamic_tax else 'no'}")
        logger.info(f"  Blacklist:        {'YES' if profile.has_blacklist else 'no'}")
        logger.info("\n── Ownership ──")
        logger.info(f"  Renounced:        {'YES' if profile.is_renounced else 'NO'}")
        logger.info(f"  Proxy admin:      {'YES' if profile.has_proxy_admin else 'no'}")
        logger.info(f"  Multi-sig:        {'YES' if profile.has_multisig else 'no'}")
        logger.info("\n── Risk Scores ──")
        logger.info(f"  Supply risk:      {profile.supply_risk.value} ({profile.supply_risk_score:.0f}/100)")
        logger.info(f"  Concentration:    {profile.concentration_risk.value} ({profile.concentration_score:.0f}/100)")
        logger.info(f"  Liquidity risk:   {profile.liquidity_risk.value} ({profile.liquidity_score:.0f}/100)")
        logger.info(f"  OVERALL RISK:     {profile.overall_risk.value} ({profile.overall_risk_score:.0f}/100)")
        logger.info("\n── Patterns Detected ──")
        for p in profile.patterns_detected:
            logger.info(f"  • {p}")
        logger.info("\n── Recommendations ──")
        for r in profile.recommendations:
            logger.info(f"  {r}")
        logger.info()

if __name__ == "__main__":
    asyncio.run(main())

__all__ = ["analyze_multiple_tokens", "analyze_token_supply", "main"]
