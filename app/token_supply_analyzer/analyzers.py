"""
Token Supply Analyzer — Analysis functions for supply, concentration, liquidity, and risk.

Each function mutates the TokenSupplyProfile in-place with scores, patterns, and recommendations.
"""

from __future__ import annotations
from app.token_supply_analyzer.models import (
    SupplyRisk,
    LockStatus,
    TokenSupplyProfile,
    CONCENTRATION_THRESHOLD_PCT,
    SUPPLY_RISK_RATIO,
    _classify_risk,
)

def _analyze_supply_mechanics(profile: TokenSupplyProfile) -> None:
    """Analyze token supply data for dilution risk and manipulation patterns."""
    score = 0.0
    total = profile.total_supply
    circ = profile.circulating_supply
    max_sup = profile.max_supply
    burned = profile.burned_supply

    if total > 0 and circ > 0:
        ratio = circ / total
        profile.patterns_detected.append(f"circ_supply_ratio:{ratio:.2%}")
        if ratio < SUPPLY_RISK_RATIO:
            score += 35
            profile.patterns_detected.append("high_dilution_risk:circ_supply_less_than_30%_of_total")
            profile.recommendations.append(
                f"⚠ Only {ratio:.0%} of total supply is circulating - high dilution risk. "
                "Check vesting/unlock schedule."
            )
        elif ratio < 0.5:
            score += 20
            profile.patterns_detected.append("moderate_dilution_risk:circ_supply_less_than_50%_of_total")
            profile.recommendations.append(
                f"⚠ ~{ratio:.0%} of supply is circulating. Verify lockup/vesting terms."
            )
    elif total > 0 and circ == 0:
        score += 15
        profile.patterns_detected.append("no_circulating_supply_data")

    if max_sup > 0:
        if total > max_sup:
            score += 40
            profile.patterns_detected.append(f"supply_exceeds_max:total_{total:.2f}_exceeds_max_{max_sup:.2f}")
            profile.recommendations.append("🚨 Total supply exceeds max supply - possible mint/printing.")
        else:
            remaining = max_sup - total
            remaining_pct = (remaining / max_sup) * 100
            if remaining_pct > 50:
                score += 25
                profile.patterns_detected.append(f"large_mint_headroom:{remaining_pct:.0f}%_of_max_supply_unminted")
                profile.recommendations.append(
                    f"🚨 {remaining_pct:.0f}% of max supply is unminted - significant dilution risk."
                )
    else:
        if profile.has_mint_function:
            score += 30
            profile.patterns_detected.append("no_max_supply_with_mint:unlimited_mint_possible")
            profile.recommendations.append(
                "🚨 No max supply set AND mint function exists - potentially unlimited mint."
            )

    if burned > 0 and total > 0:
        burn_pct = (burned / total) * 100
        profile.patterns_detected.append(f"burned_supply:{burn_pct:.1f}%")
        if burn_pct > 50:
            score -= 15
            profile.recommendations.append(
                f"✅ {burn_pct:.0f}% of supply has been burned - strong deflationary pressure."
            )

    if profile.has_mint_function:
        if profile.mint_function_type == "unlimited":
            score += 40
            profile.patterns_detected.append("unlimited_mint_function")
            profile.recommendations.append(
                "🚨 Token has an unrestricted mint function - supply can be inflated at will."
            )
        elif profile.mint_function_type == "ownable":
            score += 25
            profile.patterns_detected.append("ownable_mint_function:owner_can_mint")
            if not profile.is_renounced:
                score += 10
                profile.recommendations.append(
                    "⚠ Owner-controlled mint function - supply can be increased if ownership is not renounced."
                )
        elif profile.mint_function_type == "capped":
            score += 5
            profile.patterns_detected.append("capped_mint_function")

    if profile.is_deflationary:
        score -= 10
        profile.patterns_detected.append("deflationary_token")
        profile.recommendations.append("✅ Deflationary mechanism active (auto-burn on transactions).")

    profile.supply_risk_score = max(0.0, min(100.0, score))
    profile.supply_risk = _classify_risk(profile.supply_risk_score)

def _analyze_concentration(profile: TokenSupplyProfile) -> None:
    """Analyze holder concentration for whale dominance and insider risk."""
    score = 0.0
    top10 = profile.top_10_holder_pct
    top50 = profile.top_50_holder_pct
    deployer_hold = profile.deployer_hold_pct

    if top10 > 0:
        profile.patterns_detected.append(f"top_10_holder_pct:{top10:.1f}%")
        if top10 >= 99.0:
            score += 50
            profile.patterns_detected.append("extreme_concentration:top_10_holds_>99%")
            profile.recommendations.append(
                "🚨 Top 10 wallets hold >99% of supply - extremely concentrated, likely a scam."
            )
        elif top10 >= CONCENTRATION_THRESHOLD_PCT:
            score += 40
            profile.patterns_detected.append("critical_concentration:top_10_holds_>90%")
            profile.recommendations.append(
                f"🚨 Top 10 wallets hold {top10:.0f}% of supply - extreme concentration risk."
            )
        elif top10 >= 70:
            score += 25
            profile.patterns_detected.append("high_concentration:top_10_holds_>70%")
            profile.recommendations.append(
                f"⚠ Top 10 wallets hold {top10:.0f}% of supply - high concentration."
            )
        elif top10 >= 50:
            score += 15
            profile.patterns_detected.append("moderate_concentration:top_10_holds_>50%")
            profile.recommendations.append(
                f"⚠ Top 10 wallets hold {top10:.0f}% of supply - moderate concentration."
            )

    if top50 > 0:
        if top50 >= 99.0:
            score += 25
            profile.patterns_detected.append("extreme_concentration_top50")
        elif top50 >= 95:
            score += 15
            profile.patterns_detected.append("critical_concentration_top50")
        elif top50 >= 80:
            score += 10
            profile.patterns_detected.append("high_concentration_top50")

    if deployer_hold > 0:
        profile.patterns_detected.append(f"deployer_hold_pct:{deployer_hold:.1f}%")
        if deployer_hold > 50:
            score += 40
            profile.patterns_detected.append("deployer_holds_majority")
            profile.recommendations.append(
                f"🚨 Deployer holds {deployer_hold:.0f}% of supply - complete control, very high risk."
            )
        elif deployer_hold > 20:
            score += 20
            profile.patterns_detected.append("deployer_holds_significant")
            profile.recommendations.append(
                f"⚠ Deployer holds {deployer_hold:.0f}% of supply - significant insider allocation."
            )
        elif deployer_hold > 5:
            score += 10
            profile.patterns_detected.append("deployer_holds_moderate")
            profile.recommendations.append(
                f"(i) Deployer holds {deployer_hold:.0f}% of supply - moderate allocation."
            )

    for w in profile.whale_holdings:
        if w.get("pct", 0) > 5:
            score += 5
            profile.patterns_detected.append(
                f"whale_gt_5%:address_{w.get('address', '')[:8]}..._{w.get('pct', 0):.1f}%"
            )

    profile.concentration_score = max(0.0, min(100.0, score))
    profile.concentration_risk = _classify_risk(profile.concentration_score)

def _analyze_liquidity(profile: TokenSupplyProfile) -> None:
    """Analyze liquidity pool health and lock status."""
    score = 0.0

    if profile.lock_status == LockStatus.REMOVED:
        score += 50
        profile.patterns_detected.append("liquidity_removed")
        profile.recommendations.append("🚨 Liquidity has been removed from the pool - rug pull risk.")
    elif profile.lock_status == LockStatus.UNLOCKED:
        score += 30
        profile.patterns_detected.append("liquidity_unlocked")
        profile.recommendations.append("🚨 LP tokens are unlocked - deployer can remove liquidity at any time.")
    elif profile.lock_status == LockStatus.PARTIALLY_LOCKED:
        score += 15
        profile.patterns_detected.append("liquidity_partially_locked")
        profile.recommendations.append("⚠ Only part of the liquidity is locked. Verify the locked portion.")
    elif profile.lock_status == LockStatus.LOCKED:
        score -= 15
        profile.patterns_detected.append("liquidity_locked")
        if profile.liquidity_lock_expiry:
            profile.recommendations.append(f"✅ Liquidity locked until {profile.liquidity_lock_expiry}. Good practice.")
        else:
            profile.recommendations.append("✅ Liquidity is locked.")

    if profile.lp_token_pct_held_by_deployer > 50:
        score += 25
        profile.patterns_detected.append("deployer_holds_lp_majority")
        profile.recommendations.append("🚨 Deployer holds >50% of LP tokens - can drain pool.")
    elif profile.lp_token_pct_held_by_deployer > 10:
        score += 10
        profile.patterns_detected.append("deployer_holds_significant_lp")
        profile.recommendations.append(f"⚠ Deployer holds {profile.lp_token_pct_held_by_deployer:.0f}% of LP tokens.")

    if profile.pair_age_days > 0:
        if profile.pair_age_days < 1:
            score += 20
            profile.patterns_detected.append("very_new_pair:less_than_1_day_old")
            profile.recommendations.append("⚠ Liquidity pair is less than 1 day old - very high risk.")
        elif profile.pair_age_days < 7:
            score += 10
            profile.patterns_detected.append(f"new_pair:{profile.pair_age_days:.0f}_days_old")
            profile.recommendations.append(f"⚠ Pair is only {profile.pair_age_days:.0f} days old - higher risk.")
        elif profile.pair_age_days > 30:
            score -= 10
            profile.patterns_detected.append("established_pair:>30_days_old")

    if profile.pair_liquidity_usd > 0:
        if profile.pair_liquidity_usd < 1000:
            score += 25
            profile.patterns_detected.append("very_low_liquidity:<$1k")
            profile.recommendations.append("🚨 Very low liquidity (<$1K) - high slippage and manipulation risk.")
        elif profile.pair_liquidity_usd < 10000:
            score += 15
            profile.patterns_detected.append("low_liquidity:<$10k")
            profile.recommendations.append("⚠ Low liquidity (<$10K) - moderate slippage risk.")
        elif profile.pair_liquidity_usd > 100000:
            score -= 10
            profile.patterns_detected.append("healthy_liquidity:>$100k")

    profile.liquidity_score = max(0.0, min(100.0, score))
    profile.liquidity_risk = _classify_risk(profile.liquidity_score)

def _compute_overall_risk(profile: TokenSupplyProfile) -> None:
    """Compute overall risk score from all sub-scores."""
    weights = {"supply": 0.35, "concentration": 0.30, "liquidity": 0.20, "tax": 0.10, "ownership": 0.05}

    tax_score = 0.0
    if profile.has_dynamic_tax:
        tax_score += 30
        profile.patterns_detected.append("dynamic_tax:tax_can_change")
        profile.recommendations.append("⚠ Dynamic tax detected - buy/sell tax can change at any time.")
    if profile.has_blacklist:
        tax_score += 25
        profile.patterns_detected.append("blacklist_function")
        profile.recommendations.append("⚠ Token has a blacklist function - holders can be blocked from selling.")
    if profile.buy_tax_pct > 10:
        tax_score += 15
        profile.patterns_detected.append(f"high_buy_tax:{profile.buy_tax_pct:.1f}%")
    if profile.sell_tax_pct > 15:
        tax_score += 20
        profile.patterns_detected.append(f"very_high_sell_tax:{profile.sell_tax_pct:.1f}%")
        profile.recommendations.append(f"🚨 Sell tax of {profile.sell_tax_pct:.0f}% is very high - may be a honeypot.")

    ownership_score = 0.0
    if not profile.is_renounced:
        ownership_score += 15
        profile.patterns_detected.append("ownership_not_renounced")
        profile.recommendations.append("⚠ Ownership is NOT renounced - contract can be modified.")
    if profile.has_proxy_admin:
        ownership_score += 20
        profile.patterns_detected.append("proxy_admin:contract_can_be_upgraded")
        profile.recommendations.append("🚨 Token uses a proxy pattern with admin - contract can be upgraded (rug risk).")
    if profile.has_multisig:
        ownership_score -= 10
        profile.patterns_detected.append("multisig_owner")
        profile.recommendations.append("✅ Ownership uses multi-sig - adds security layer.")

    profile.overall_risk_score = (
        profile.supply_risk_score * weights["supply"]
        + profile.concentration_score * weights["concentration"]
        + profile.liquidity_score * weights["liquidity"]
        + tax_score * weights["tax"]
        + ownership_score * weights["ownership"]
    )
    profile.overall_risk = _classify_risk(profile.overall_risk_score)

__all__ = [
    "_analyze_supply_mechanics",
    "_analyze_concentration",
    "_analyze_liquidity",
    "_compute_overall_risk",
]
