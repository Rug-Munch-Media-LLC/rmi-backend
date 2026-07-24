"""
Velocity Risk Engine - Time-Series Anomaly Detection
=====================================================

Tracks how token metrics CHANGE over time, not just what they ARE.
Fast changes = higher risk than static bad metrics.

Premium feature: Catches rugs mid-flight, not just at launch.
"""

from __future__ import annotations
import time
from collections import defaultdict
from typing import Any

from app.core.logging import get_logger

logger = get_logger("sentinel.velocity")

# In-memory time-series cache (backed by Redis in production)
# { "chain:address": [(timestamp, {metrics}), ...] }
_series_cache: dict[str, list] = defaultdict(list)
MAX_SERIES_LENGTH = 10  # Keep last 10 snapshots per token

# ── Velocity thresholds for risk scoring ─────────────────────
# Holder concentration velocity thresholds (delta % per hour)
HOLDER_CONCENTRATION_SURGE_THRESHOLD = 20.0
HOLDER_CONCENTRATION_FAST_THRESHOLD = 10.0
HOLDER_CONCENTRATION_RISING_THRESHOLD = 5.0

# LP depth velocity thresholds (change % per hour)
LP_DRAINING_RAPIDLY_THRESHOLD = -30.0
LP_DECREASING_FAST_THRESHOLD = -15.0
LP_DECREASING_THRESHOLD = -5.0

# Volume/liquidity ratio thresholds
VOLUME_LIQ_RATIO_SPIKE_THRESHOLD = 10.0
VOLUME_LIQ_RATIO_RISING_THRESHOLD = 5.0

# Price velocity thresholds (change % per hour)
PRICE_CRASH_THRESHOLD = -50.0
PRICE_PUMP_ABNORMAL_THRESHOLD = 500.0

# Transaction count velocity threshold (tx per hour)
TX_VOLUME_SURGE_THRESHOLD = 1000

# Risk score weights
RISK_HOLDER_CONCENTRATION_SURGE = 25
RISK_HOLDER_CONCENTRATION_FAST = 15
RISK_HOLDER_CONCENTRATION_RISING = 8
RISK_LP_DRAINING_RAPIDLY = 30
RISK_LP_DECREASING_FAST = 20
RISK_LP_DECREASING = 10
RISK_VOLUME_SPIKE = 15
RISK_VOLUME_RISING = 8
RISK_PRICE_CRASH = 20
RISK_PRICE_PUMP = 12
RISK_TX_VOLUME_SURGE = 10

# Risk level thresholds
RISK_CRITICAL_THRESHOLD = 60
RISK_HIGH_THRESHOLD = 30
RISK_MEDIUM_THRESHOLD = 10

# Market context thresholds
FNG_EXTREME_GREED_THRESHOLD = 75
FNG_GREED_THRESHOLD = 60
FNG_EXTREME_FEAR_THRESHOLD = 25
FNG_FEAR_THRESHOLD = 40

# Market pressure factors
PRESSURE_EXTREME_GREED = 1.3
PRESSURE_GREED = 1.15
PRESSURE_EXTREME_FEAR = 0.85
PRESSURE_FEAR = 0.95
PRESSURE_NEUTRAL = 1.0

def record_snapshot(chain: str, address: str, metrics: dict[str, float]) -> None:
    """Record a metrics snapshot for velocity tracking."""
    key = f"{chain}:{address.lower()}"
    _series_cache[key].append((time.time(), metrics))
    if len(_series_cache[key]) > MAX_SERIES_LENGTH:
        _series_cache[key] = _series_cache[key][-MAX_SERIES_LENGTH:]

def analyze_velocity(
    chain: str, address: str, current: dict[str, float], window_seconds: int = 3600
) -> dict[str, Any]:
    """Analyze how fast metrics are changing. Returns velocity scores and flags.

    Metrics tracked:
    - holder_concentration: top10% change per hour
    - lp_depth: liquidity depth change per hour
    - volume_liquidity_ratio: vol/liq ratio acceleration
    - price: price change per hour
    - tx_count: transaction velocity
    """
    key = f"{chain}:{address.lower()}"
    history = _series_cache.get(key, [])

    # Record current
    record_snapshot(chain, address, current)

    if len(history) < 2:
        return {"status": "insufficient_data", "snapshots": len(history) + 1}

    # Find snapshots within window
    now = time.time()
    window_snapshots = [(ts, m) for ts, m in history if now - ts <= window_seconds]

    if len(window_snapshots) < 2:
        return {"status": "insufficient_window_data", "snapshots": len(window_snapshots) + 1}

    oldest = window_snapshots[0][1]
    newest = current
    time_span = now - window_snapshots[0][0]
    hours = max(time_span / 3600, 0.01)

    velocities = {}
    flags = []
    risk_score = 0

    # Holder concentration velocity
    if "top10_pct" in oldest and "top10_pct" in newest:
        holder_delta = newest["top10_pct"] - oldest["top10_pct"]
        holder_velocity = holder_delta / hours
        velocities["holder_concentration_delta_per_hour"] = round(holder_velocity, 2)

        if holder_velocity > HOLDER_CONCENTRATION_SURGE_THRESHOLD:
            flags.append("HOLDER_CONCENTRATION_SURGING")
            risk_score += RISK_HOLDER_CONCENTRATION_SURGE
        elif holder_velocity > HOLDER_CONCENTRATION_FAST_THRESHOLD:
            flags.append("HOLDER_CONCENTRATION_RISING_FAST")
            risk_score += RISK_HOLDER_CONCENTRATION_FAST
        elif holder_velocity > HOLDER_CONCENTRATION_RISING_THRESHOLD:
            flags.append("HOLDER_CONCENTRATION_RISING")
            risk_score += RISK_HOLDER_CONCENTRATION_RISING

    # LP depth velocity
    if "liquidity_usd" in oldest and "liquidity_usd" in newest:
        old_liq = max(oldest["liquidity_usd"], 1)
        new_liq = max(newest["liquidity_usd"], 1)
        lp_delta_pct = ((new_liq - old_liq) / old_liq) * 100
        lp_velocity = lp_delta_pct / hours
        velocities["lp_depth_change_pct_per_hour"] = round(lp_velocity, 2)

        if lp_velocity < LP_DRAINING_RAPIDLY_THRESHOLD:
            flags.append("LP_DRAINING_RAPIDLY")
            risk_score += RISK_LP_DRAINING_RAPIDLY
        elif lp_velocity < LP_DECREASING_FAST_THRESHOLD:
            flags.append("LP_DECREASING_FAST")
            risk_score += RISK_LP_DECREASING_FAST
        elif lp_velocity < LP_DECREASING_THRESHOLD:
            flags.append("LP_DECREASING")
            risk_score += RISK_LP_DECREASING

    # Volume/liquidity ratio acceleration
    if all(k in oldest and k in newest for k in ["volume_24h", "liquidity_usd"]):
        old_ratio = oldest["volume_24h"] / max(oldest["liquidity_usd"], 1)
        new_ratio = newest["volume_24h"] / max(newest["liquidity_usd"], 1)
        ratio_delta = new_ratio - old_ratio
        velocities["volume_liquidity_ratio_change"] = round(ratio_delta, 3)

        if ratio_delta > VOLUME_LIQ_RATIO_SPIKE_THRESHOLD:
            flags.append("VOLUME_SPIKE_VS_LIQUIDITY")
            risk_score += RISK_VOLUME_SPIKE
        elif ratio_delta > VOLUME_LIQ_RATIO_RISING_THRESHOLD:
            flags.append("VOLUME_RISING_VS_LIQUIDITY")
            risk_score += RISK_VOLUME_RISING

    # Price velocity
    if "price_usd" in oldest and "price_usd" in newest:
        old_price = max(oldest["price_usd"], 0.000001)
        new_price = max(newest["price_usd"], 0.000001)
        price_delta_pct = ((new_price - old_price) / old_price) * 100
        price_velocity = price_delta_pct / hours
        velocities["price_change_pct_per_hour"] = round(price_velocity, 2)

        if price_velocity < PRICE_CRASH_THRESHOLD:
            flags.append("PRICE_CRASHING")
            risk_score += RISK_PRICE_CRASH
        elif price_velocity > PRICE_PUMP_ABNORMAL_THRESHOLD:
            flags.append("PRICE_PUMPING_ABNORMALLY")
            risk_score += RISK_PRICE_PUMP

    # Tx count velocity
    if "tx_count" in oldest and "tx_count" in newest:
        tx_delta = newest["tx_count"] - oldest["tx_count"]
        tx_velocity = tx_delta / hours
        velocities["tx_count_change_per_hour"] = round(tx_velocity, 1)

        if tx_velocity > TX_VOLUME_SURGE_THRESHOLD:
            flags.append("TX_VOLUME_SURGING")
            risk_score += RISK_TX_VOLUME_SURGE

    return {
        "status": "ok",
        "snapshots": len(window_snapshots) + 1,
        "time_window_hours": round(hours, 2),
        "velocities": velocities,
        "flags": flags,
        "velocity_risk_score": min(risk_score, 100),
        "risk_level": "critical" if risk_score > RISK_CRITICAL_THRESHOLD
        else "high" if risk_score > RISK_HIGH_THRESHOLD
        else "medium" if risk_score > RISK_MEDIUM_THRESHOLD
        else "low",
    }

def get_market_context() -> dict[str, Any]:
    """Get current market conditions for contextualized scoring."""
    try:
        import asyncio

        import httpx

        async def _fetch() -> dict[str, int]:
            async with httpx.AsyncClient(timeout=5) as c:
                resp = await c.get("https://api.alternative.me/fng/?limit=1")
                if resp.status_code == 200:
                    data = resp.json()
                    fng = data.get("data", [{}])[0]
                    return {
                        "fear_greed_value": int(fng.get("value", 50)),
                        "fear_greed_classification": fng.get("value_classification", "Neutral"),
                        "timestamp": fng.get("timestamp", ""),
                    }
                return {"fear_greed_value": 50, "fear_greed_classification": "Unknown"}

        return asyncio.run(_fetch())
    except Exception as e:
        logger.warning("Failed to fetch market context: %s", e)
        return {"fear_greed_value": 50, "fear_greed_classification": "Unknown"}

def contextualize_score(base_safety: int, market_context: dict[str, Any]) -> dict[str, Any]:
    """Adjust safety score based on market conditions.

    During Extreme Greed (>75): scammers launch more - raise sensitivity
    During Extreme Fear (<25): fewer scams launch - lower sensitivity
    """
    fng = market_context.get("fear_greed_value", 50)

    # Market pressure: how much to adjust
    if fng > FNG_EXTREME_GREED_THRESHOLD:  # Extreme Greed - more scams
        pressure = PRESSURE_EXTREME_GREED
        context = "Scam alert elevated - market in Extreme Greed"
    elif fng > FNG_GREED_THRESHOLD:  # Greed
        pressure = PRESSURE_GREED
        context = "Elevated scam risk - market in Greed"
    elif fng < FNG_EXTREME_FEAR_THRESHOLD:  # Extreme Fear - fewer new scams
        pressure = PRESSURE_EXTREME_FEAR
        context = "Reduced scam activity - market in Extreme Fear"
    elif fng < FNG_FEAR_THRESHOLD:  # Fear
        pressure = PRESSURE_FEAR
        context = "Slightly reduced risk - market in Fear"
    else:  # Neutral
        pressure = PRESSURE_NEUTRAL
        context = "Normal market conditions"

    adjusted = max(0, min(100, round(base_safety / pressure)))

    return {
        "base_safety": base_safety,
        "contextualized_safety": adjusted,
        "market_pressure": round(pressure, 2),
        "fear_greed": fng,
        "classification": market_context.get("fear_greed_classification", "Neutral"),
        "context": context,
    }
