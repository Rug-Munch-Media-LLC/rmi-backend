"""Volume analysis — volume/liquidity ratio, anomaly detection."""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    _fetch_dexscreener,
    logger,
)


async def _check_volume_liquidity_ratio(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check volume-to-liquidity ratio.

    volume_24h / liquidity_usd = turnover ratio.
    High ratio (> 5) = potential wash trading (suspicious).
    Very high ratio (> 20) = almost certainly wash trading.
    Low ratio (< 0.5) + low absolute values = dead token.
    """
    result: dict[str, Any] = {
        "check": "volume_liquidity_ratio",
        "result": "scanned",
        "volume_24h": 0.0,
        "liquidity_usd": 0.0,
        "ratio": 0.0,
        "risk_level": "unknown",
        "is_wash_trading_suspicious": False,
        "risk_score": 0,
    }

    # Fetch DEX data (own ClickHouse first, DexScreener API fallback)
    try:
        data = await _fetch_dexscreener(token_address, chain)
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            volume = float(pair.get("volume", {}).get("h24", 0) or 0)
            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            price_usd = float(pair.get("priceUsd", 0) or 0)

            result["volume_24h"] = volume
            result["liquidity_usd"] = liquidity
            result["price_usd"] = price_usd
            result["source"] = "dexscreener"

            # Calculate ratio (avoid division by zero)
            if liquidity > 0 and volume > 0:
                ratio = volume / liquidity
                result["ratio"] = round(ratio, 4)
            elif volume > 0 and liquidity == 0:
                result["ratio"] = float("inf")
            else:
                result["ratio"] = 0.0

            # Risk classification
            ratio = result["ratio"]
            if ratio == float("inf") or ratio > 50:
                result["risk_level"] = "critical"
                result["is_wash_trading_suspicious"] = True
                result["risk_score"] = 45
            elif ratio > 20:
                result["risk_level"] = "high"
                result["is_wash_trading_suspicious"] = True
                result["risk_score"] = 35
            elif ratio > 5:
                result["risk_level"] = "medium"
                result["risk_score"] = 20
                if volume > 10000 and liquidity < 1000:
                    result["is_wash_trading_suspicious"] = True
                    result["risk_score"] = 35
                    result["risk_level"] = "high"
            elif ratio < 0.5 and volume < 100:
                result["risk_level"] = "low_activity"
                result["risk_score"] = 5
            else:
                result["risk_level"] = "healthy"
                result["risk_score"] = 0

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.debug("volume_liquidity_failed", error=str(e)[:60])
        result["result"] = "error"
        result["error"] = str(e)[:60]

    return result


async def _check_volume_anomaly(
    token_address: str,
    chain: str,
    market_price: float = 0,
    volume_24h: float = 0,
    circulating_supply: float = 0,
) -> dict[str, Any] | None:
    """Detect wash-trading and volume anomalies using DexScreener data.

    Analyzes buy/sell asymmetry, volume/liquidity ratios, and price-volume
    divergence patterns to flag suspicious trading activity.
    """
    logger.info("check_volume_anomaly", token=token_address, chain=chain)
    result: dict[str, Any] = {
        "check": "volume_anomaly",
        "result": "scanned",
        "anomaly_score": 0,
        "anomalies": [],
        "is_suspicious": False,
        "signals": {},
    }

    try:
        data = await _fetch_dexscreener(token_address, chain)
        if not data or not data.get("pairs"):
            if volume_24h > 0 and market_price > 0:
                result["total_volume_24h"] = volume_24h
                result["market_price"] = market_price
                result["source"] = "params"
                return result
            result["result"] = "unavailable"
            result["error"] = "No DEX data available"
            return result

        pairs = data["pairs"]
        total_volume = 0.0
        total_liquidity = 0.0
        buy_count = 0
        sell_count = 0
        price_changes: list[float] = []
        prices: list[float] = []

        for pair in pairs:
            try:
                vol = float(pair.get("volume", {}).get("h24", 0) or 0)
                liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                price = float(pair.get("priceUsd", 0) or 0)
                txns = pair.get("txns", {}).get("h24", {})
                buys = int(txns.get("buys", 0) or 0)
                sells = int(txns.get("sells", 0) or 0)
                price_change = pair.get("priceChange", {}).get("h24", 0) or 0

                total_volume += vol
                total_liquidity += liq
                buy_count += buys
                sell_count += sells
                if price > 0:
                    prices.append(price)
                if price_change != 0:
                    price_changes.append(price_change)
            except (ValueError, TypeError):
                continue

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
            elif vol_liq_ratio > 20:
                anomalies.append("high_volume_liquidity_ratio")
                anomaly_score += 25
            elif vol_liq_ratio > 5:
                anomalies.append("elevated_volume_liquidity_ratio")
                anomaly_score += 10

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

        # Signal 3: Negative price change + high volume = distribution
        if price_changes and total_volume > 0:
            avg_price_change = sum(price_changes) / len(price_changes)
            signals["avg_price_change_24h"] = round(avg_price_change, 2)
            if avg_price_change < -20 and total_volume > 10000:
                anomalies.append("price_volume_divergence")
                anomaly_score += 30

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
        result["source"] = "dexscreener"

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.debug("volume_anomaly_failed", error=str(e)[:80])
        result["result"] = "error"
        result["error"] = str(e)[:60]

    return result
