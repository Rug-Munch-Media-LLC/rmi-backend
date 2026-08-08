"""Analysis helper functions for MEV detection."""

from __future__ import annotations

from typing import Any


def _check_pool_vulnerability(pool: dict[str, Any]) -> list[str]:
    """Score a DEX pool for MEV vulnerability based on characteristics. Returns vulnerability flags."""
    flags: list[str] = []
    liquidity = pool.get("liquidity_usd", 0) or 0
    volume_24h = pool.get("volume_24h_usd", 0) or 0
    if liquidity < 10_000:
        flags.append("low_liquidity_high_vuln")
    elif liquidity < 100_000:
        flags.append("low_liquidity_medium_vuln")
    if liquidity > 0 and volume_24h / liquidity < 0.1:
        flags.append("stale_pricing_vuln")
    fee = pool.get("fee_tier", 3000)
    if fee == 100:
        flags.append("low_fee_arb_vuln")
    if fee == 10000:
        flags.append("high_fee_sandwich_vuln")
    return flags


def _estimate_mev_vulnerability_score(flags: list[str]) -> float:
    """Compute a 0.0-1.0 MEV vulnerability score from flags."""
    score = 0.0
    weights = {
        "low_liquidity_high_vuln": 0.8,
        "low_liquidity_medium_vuln": 0.5,
        "stale_pricing_vuln": 0.3,
        "low_fee_arb_vuln": 0.4,
        "high_fee_sandwich_vuln": 0.6,
    }
    for flag in flags:
        score += weights.get(flag, 0.1)
    return min(score, 1.0)


def _is_sandwich_pattern(frontrun: dict[str, Any], victim: dict[str, Any], backrun: dict[str, Any]) -> float:
    """Heuristic check: are these three transactions a sandwich attack? Returns confidence 0.0-1.0."""
    confidence = 0.0

    # 1. Same pool / target contract
    if frontrun.get("to_address", "").lower() == backrun.get("to_address", "").lower():
        confidence += 0.3
        if frontrun.get("to_address", "").lower() == victim.get("to_address", "").lower():
            confidence += 0.2

    # 2. Same from address for frontrun/backrun (bot address)
    if frontrun.get("from_address", "").lower() == backrun.get("from_address", "").lower():
        confidence += 0.25
        if frontrun.get("from_address", "").lower() != victim.get("from_address", "").lower():
            confidence += 0.1

    # 3. Both are swap transactions
    frontrun_method = frontrun.get("method_signature", "")
    backrun_method = backrun.get("method_signature", "")
    if "swap" in frontrun_method.lower():
        confidence += 0.05
        if "swap" in backrun_method.lower():
            confidence += 0.05

    # 4. Gas price premium (bots pay more to frontrun)
    frontrun_gas = frontrun.get("gas_price_gwei", 0) or 0
    victim_gas = victim.get("gas_price_gwei", 0) or 0
    backrun_gas = backrun.get("gas_price_gwei", 0) or 0
    if frontrun_gas > victim_gas * 1.1:
        confidence += 0.05
    if backrun_gas > victim_gas * 1.1:
        confidence += 0.05

    # 5. Consecutive tx indices (victim sandwiched between bot's txs)
    f_idx = frontrun.get("tx_index", 0)
    v_idx = victim.get("tx_index", 0)
    b_idx = backrun.get("tx_index", 0)
    if f_idx < v_idx < b_idx:
        confidence += 0.1
        if b_idx - f_idx <= 5:
            confidence += 0.1

    return min(confidence, 1.0)
