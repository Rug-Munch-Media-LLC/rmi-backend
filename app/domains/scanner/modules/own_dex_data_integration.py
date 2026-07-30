"""
Own DEX Data Integration — Wire Our Data Into the Scanner Pipeline
====================================================================
Bridges own_dex_data.py and own_price_oracle.py into the existing scanner
callback chain so volume_analysis.py and market.py use local ClickHouse
data instead of external API calls.

DEPRECATION NOTE (2026-07-30):
    Direct integration is now done at the source — _core._fetch_dexscreener
    already tries ClickHouse own-dex-data first when PREFER_OWN_DATA=true.
    The monkey-patching here is preserved as a legacy fallback only.

    See _core.py for the primary integration:
      - app.domains.scanner.modules._core._fetch_dexscreener()
      - Controlled by PREFER_OWN_DATA env var (default: true)
      - Logs "Using own DEX data" vs "Falling back to DexScreener API"

Strategy (legacy):
  - Monkey-patch the _fetch_dexscreener function in _core.py at import time
  - Provide new callback functions that match existing callback signatures
  - Never failing: always fall through to the existing DexScreener/CoinGecko
    implementation if our data is insufficient or ClickHouse is down

Usage (legacy):
    from app.domains.scanner.modules.own_dex_data_integration import (
        analyze_token_dex,
        patch_scanner_modules,
    )
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_patched = False


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT — Drop-in replacement for DexScreener + CoinGecko
# ═══════════════════════════════════════════════════════════════════════════


async def analyze_token_dex(
    token_address: str,
    chain: str,
) -> dict[str, Any]:
    """Full DEX analysis for a token — replaces DexScreener + CoinGecko.

    Returns a unified result dict combining:
      - DEX pairs (dexscreener-compatible)
      - Price data (coingecko-compatible)
      - Derived metrics (our augmentation)

    This is called by the scanner to replace individual DexScreener
    and CoinGecko calls during token analysis.
    """
    from app.domains.scanner.engines.own_dex_data import get_own_dex_data
    from app.domains.scanner.engines.own_price_oracle import get_token_price

    logger.info("analyze_token_dex: %s on %s (own data)", token_address, chain)

    # Parallel fetch our data
    import asyncio

    dex_data_task = asyncio.create_task(get_own_dex_data(token_address, chain))
    price_data_task = asyncio.create_task(get_token_price(token_address, chain))

    dex_data = await dex_data_task
    price_data = await price_data_task

    # Unpack DEX data
    pairs = dex_data.get("pairs", [])
    dex_metrics = dex_data.get("metrics", {})
    dex_source = dex_data.get("source", "unavailable")

    # Unpack price data
    price_usd = price_data.get("price_usd", 0.0)
    price_confidence = price_data.get("confidence", 0.0)
    price_source = price_data.get("source", "unavailable")
    volume_24h = price_data.get("volume_24h_usd", dex_metrics.get("total_volume_24h", 0))
    price_change_24h = price_data.get("price_change_24h_pct", 0.0)

    # Build unified result
    result: dict[str, Any] = {
        "token_address": token_address,
        "chain": chain,
        "price_usd": price_usd,
        "price_confidence": price_confidence,
        "volume_24h_usd": volume_24h,
        "liquidity_usd": dex_metrics.get("total_liquidity_usd", 0),
        "price_change_24h_pct": price_change_24h,
        "swap_count_24h": dex_metrics.get("swap_count_24h", 0),
        "unique_pools": dex_metrics.get("unique_pools", 0),
        # DEX data (compatible with existing consumers)
        "pairs": pairs,
        "pair_count": len(pairs),
        # Top pair for quick access
        "top_pair": pairs[0] if pairs else None,
        # Source tracking
        "dex_source": dex_source,
        "price_source": price_source,
        "is_own_data": dex_source == "own" or price_source == "own",
        # Data quality
        "has_dex_data": dex_source != "unavailable",
        "has_price_data": price_source != "unavailable",
    }

    # Price consensus (multiple source check)
    price_sources = price_data.get("sources", [])
    result["price_sources"] = price_sources
    result["price_consensus"] = len(price_sources) >= 2

    # Augment with cross-source data if available
    if pairs and price_usd > 0:
        # Estimate pool utilization
        total_volume = dex_metrics.get("total_volume_24h", 0)
        total_liquidity = dex_metrics.get("total_liquidity_usd", 0)
        if total_liquidity > 0 and total_volume > 0:
            result["pool_turnover_ratio"] = round(total_volume / total_liquidity, 4)

        # Top DEX identification
        dex_counts: dict[str, int] = {}
        for p in pairs:
            d = p.get("dexId", "unknown")
            dex_counts[d] = dex_counts.get(d, 0) + 1
        result["top_dex"] = max(dex_counts, key=dex_counts.get) if dex_counts else ""
        result["dex_diversity"] = len(dex_counts)

    # Conservatively estimate market cap from volume (when we have no supply data)
    if volume_24h > 100 and price_usd > 0:
        # Very rough: assume daily vol is ~5% of mcap (typical for mid-cap tokens)
        result["estimated_market_cap"] = round(volume_24h * 20, 2)
    else:
        result["estimated_market_cap"] = None

    return result


# ═══════════════════════════════════════════════════════════════════════════
# RETURN DATA FOR SPECIFIC SCANNER MODULES
# ═══════════════════════════════════════════════════════════════════════════


async def get_price_consensus(
    token_address: str,
    chain: str,
) -> dict[str, Any]:
    """Drop-in for market.py _get_price_consensus().

    Returns the EXACT same structure but uses own data first.
    """
    from app.domains.scanner.engines.own_price_oracle import get_token_price

    price_data = await get_token_price(token_address, chain)
    source = price_data.get("source", "unavailable")

    result: dict[str, Any] = {
        "check": "price",
        "result": "scanned" if source != "unavailable" else "unavailable",
        "price_usd": price_data.get("price_usd", 0),
        "sources": price_data.get("sources", []),
        "price_consensus": len(price_data.get("sources", [])) >= 2,
        "volume_24h": price_data.get("volume_24h_usd", 0),
        "price_change_24h": price_data.get("price_change_24h_pct", 0),
    }

    # Include market cap if available
    mcap = price_data.get("market_cap_usd")
    if mcap is not None:
        result["market_cap_usd"] = mcap

    return result


async def get_volume_liquidity_data(
    token_address: str,
    chain: str,
) -> dict[str, Any]:
    """Drop-in for market.py _check_volume_liquidity_ratio().

    Returns the EXACT same structure using own data.
    """
    from app.domains.scanner.engines.own_dex_data import get_own_dex_data

    dex_data = await get_own_dex_data(token_address, chain)
    pairs = dex_data.get("pairs", [])
    metrics = dex_data.get("metrics", {})

    total_volume = metrics.get("total_volume_24h", 0)
    total_liquidity = metrics.get("total_liquidity_usd", 0)

    # Calculate ratio (same logic as market.py)
    ratio = 0.0
    if total_liquidity > 0 and total_volume > 0:
        ratio = total_volume / total_liquidity
    elif total_volume > 0 and total_liquidity == 0:
        ratio = float("inf")

    # Risk classification (same as market.py)
    risk_level = "unknown"
    risk_score = 0
    is_wash_trading = False

    if ratio == float("inf") or ratio > 50:
        risk_level = "critical"
        is_wash_trading = True
        risk_score = 45
    elif ratio > 20:
        risk_level = "high"
        is_wash_trading = True
        risk_score = 35
    elif ratio > 5:
        risk_level = "medium"
        risk_score = 20
        if total_volume > 10000 and total_liquidity < 1000:
            is_wash_trading = True
            risk_score = 35
            risk_level = "high"
    elif ratio < 0.5 and total_volume < 100:
        risk_level = "low_activity"
        risk_score = 5
    else:
        risk_level = "healthy"
        risk_score = 0

    # Best price from pairs
    price_usd = 0.0
    if pairs:
        from contextlib import suppress
        with suppress(ValueError, TypeError):
            price_usd = float(pairs[0].get("priceUsd", 0) or 0)

    return {
        "check": "volume_liquidity_ratio",
        "result": "scanned",
        "volume_24h": total_volume,
        "liquidity_usd": total_liquidity,
        "price_usd": price_usd,
        "ratio": round(ratio, 4) if ratio != float("inf") else ratio,
        "risk_level": risk_level,
        "is_wash_trading_suspicious": is_wash_trading,
        "risk_score": risk_score,
        "source": "own_data",
        "pools_analyzed": len(pairs),
    }


async def get_volume_anomaly(
    token_address: str,
    chain: str,
) -> dict[str, Any]:
    """Drop-in for volume_analysis.py _check_volume_anomaly().

    Returns the EXACT same structure using own dex data.
    Detects wash-trading anomalies from on-chain events.
    """
    from app.domains.scanner.engines.own_dex_data import get_own_dex_data

    dex_data = await get_own_dex_data(token_address, chain)
    pairs = dex_data.get("pairs", [])
    source = dex_data.get("source", "unavailable")

    result: dict[str, Any] = {
        "check": "volume_anomaly",
        "result": "scanned",
        "anomaly_score": 0,
        "anomalies": [],
        "is_suspicious": False,
        "signals": {},
        "source": source,
    }

    if not pairs:
        result["result"] = "unavailable" if source == "unavailable" else "no_pairs"
        result["error"] = "No DEX pair data available"
        return result

    # Same anomaly detection logic as volume_analysis.py
    total_volume = sum(float(p.get("volume", {}).get("h24", 0) or 0) for p in pairs)
    total_liquidity = sum(float(p.get("liquidity", {}).get("usd", 0) or 0) for p in pairs)
    buy_count = sum(int(p.get("txns", {}).get("h24", {}).get("buys", 0) or 0) for p in pairs)
    sell_count = sum(int(p.get("txns", {}).get("h24", {}).get("sells", 0) or 0) for p in pairs)

    signals: dict[str, Any] = {}
    anomaly_score = 0
    anomalies: list[str] = []

    # Signal 1: Volume/Liquidity Ratio
    if total_liquidity > 0 and total_volume > 0:
        vol_liq_ratio = total_volume / total_liquidity
        signals["volume_liquidity_ratio"] = round(vol_liq_ratio, 2)
        if vol_liq_ratio > 50:
            anomalies.append("extreme_volume_liquidity_ratio")
            anomaly_score += 40
            signals["vol_liq_risk"] = "critical"
        elif vol_liq_ratio > 20:
            anomalies.append("high_volume_liquidity_ratio")
            anomaly_score += 25
            signals["vol_liq_risk"] = "high"
        elif vol_liq_ratio > 5:
            anomalies.append("elevated_volume_liquidity_ratio")
            anomaly_score += 10
            signals["vol_liq_risk"] = "medium"

    # Signal 2: Buy/Sell Asymmetry
    total_txns = buy_count + sell_count
    if total_txns > 0:
        buy_ratio = buy_count / total_txns
        signals["buy_ratio"] = round(buy_ratio, 3)
        if buy_ratio > 0.95:
            anomalies.append("extreme_buy_dominance")
            anomaly_score += 35
        elif buy_ratio > 0.85:
            anomalies.append("high_buy_dominance")
            anomaly_score += 20
        elif buy_ratio < 0.05:
            anomalies.append("extreme_sell_dominance")
            anomaly_score += 35
        elif buy_ratio < 0.15:
            anomalies.append("high_sell_dominance")
            anomaly_score += 20

    # Signal 3: Price changes
    price_changes = [float(p.get("priceChange", {}).get("h24", 0) or 0) for p in pairs]
    if price_changes and total_volume > 0:
        avg_price_change = sum(price_changes) / len(price_changes)
        signals["avg_price_change_24h"] = round(avg_price_change, 2)
        if avg_price_change < -20 and total_volume > 10000:
            anomalies.append("price_volume_divergence_distribution")
            anomaly_score += 30
            signals["divergence_type"] = "distribution"

    # Signal 4: Volume Concentration
    if len(pairs) > 1:
        top_pool_vol = max(float(p.get("volume", {}).get("h24", 0) or 0) for p in pairs)
        if total_volume > 0:
            top_pool_share = top_pool_vol / total_volume
            signals["top_pool_volume_share"] = round(top_pool_share, 3)
            if top_pool_share > 0.95:
                anomalies.append("volume_concentrated_single_pool")
                anomaly_score += 15
                signals["pool_concentration_risk"] = "critical"
            elif top_pool_share > 0.80:
                anomalies.append("high_volume_concentration")
                anomaly_score += 8
                signals["pool_concentration_risk"] = "high"

    result["anomaly_score"] = min(100, anomaly_score)
    result["is_suspicious"] = anomaly_score >= 30
    result["anomalies"] = anomalies
    result["signals"] = signals
    result["risk_level"] = (
        "critical"
        if anomaly_score >= 70
        else "high"
        if anomaly_score >= 40
        else "medium"
        if anomaly_score >= 20
        else "low"
    )
    result["total_volume_24h"] = total_volume
    result["total_liquidity_usd"] = total_liquidity
    result["buy_count_24h"] = buy_count
    result["sell_count_24h"] = sell_count

    return result


# ═══════════════════════════════════════════════════════════════════════════
# PATCH — Monkey-patch scanner modules to use our data
# ═══════════════════════════════════════════════════════════════════════════


def patch_scanner_modules() -> bool:
    """Replace DexScreener calls in scanner modules with own data.

    This monkey-patches _core._fetch_dexscreener to first query
    ClickHouse, only falling back to the real DexScreener API when
    insufficient data exists.

    Call once at scanner startup. Safe to call multiple times (idempotent).
    """
    global _patched
    if _patched:
        return True

    try:
        import app.domains.scanner.engines.own_dex_data as own_dex
        import app.domains.scanner.modules._core as core_module

        original_fetch = core_module._fetch_dexscreener

        async def _patched_fetch_dexscreener(token_address: str) -> dict | None:
            """Patched version — tries ClickHouse first, falls back to API."""
            logger.debug(
                "Using own_dex_data for %s (patched _fetch_dexscreener)",
                token_address,
            )
            dex_result = await own_dex.get_own_dex_data(token_address, "ethereum")

            if dex_result.get("source") == "own" and dex_result.get("pairs"):
                # Convert our format back to the format _core expects
                pairs = dex_result["pairs"]
                # Build DexScreener-format response
                return {
                    "pairs": [
                        {
                            "chainId": p.get("chainId", ""),
                            "dexId": p.get("dexId", ""),
                            "pairAddress": p.get("pairAddress", ""),
                            "priceUsd": p.get("priceUsd", "0"),
                            "volume": p.get("volume", {}),
                            "liquidity": p.get("liquidity", {}),
                            "txns": p.get("txns", {}),
                            "priceChange": p.get("priceChange", {}),
                            "fdv": p.get("fdv", 0),
                        }
                        for p in pairs
                    ],
                    "pairCount": len(pairs),
                }

            # Fall through to original
            logger.debug("Falling back to original DexScreener for %s", token_address)
            return await original_fetch(token_address)

        # Apply the patch
        core_module._fetch_dexscreener = _patched_fetch_dexscreener
        _patched = True
        logger.info("Scanner modules patched: DexScreener calls redirected through ClickHouse")
        return True

    except (ImportError, AttributeError) as e:
        logger.warning("Could not patch scanner modules: %s", e)
        return False
