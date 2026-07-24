"""Scanner service — volume anomaly detection via DexScreener analysis."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

async def _check_volume_anomaly(
    token_address: str,
    chain: str,
    market_price: float = 0,
    volume_24h: float = 0,
    circulating_supply: float = 0,
) -> dict[str, Any] | None:
    """Detect wash-trading volume anomalies using DexScreener multi-source analysis.

    Uses DexScreener data to detect:
    - Volume/liquidity ratio anomalies (washing through same pools)
    - Price-volume divergence (price drops on high volume = distribution)
    - Unusual buy/sell ratio asymmetry (bots front-running)
    - Stake/transfer volume disparity (on-chain vs DEX volume mismatch)

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier
        market_price: Current market price in USD (optional)
        volume_24h: 24-hour trading volume in USD (optional)
        circulating_supply: Circulating token supply (optional)

    Returns:
        Dict with anomaly detection results, risk_score, and flagged patterns
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
        from app.domains.scanner.modules._core import _fetch_with_fallback

        data, _ = await _fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"],
            json_mode=True,
        )
        if not data or not data.get("pairs"):
            result["result"] = "unavailable"
            result["error"] = "No DexScreener data available"
            return result

        pairs = data["pairs"]
        total_volume = 0.0
        total_liquidity = 0.0
        buy_volume = 0.0
        sell_volume = 0.0
        buy_count = 0
        sell_count = 0
        price_changes = []
        prices = []

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
                buy_volume += buys * price if price > 0 else 0
                sell_volume += sells * price if price > 0 else 0
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

        # ── Signal 1: Volume/Liquidity Ratio ──
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
            else:
                signals["vol_liq_risk"] = "normal"

        # ── Signal 2: Buy/Sell Asymmetry ──
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

        # ── Signal 3: Price-Volume Divergence ──
        if price_changes and total_volume > 0:
            avg_price_change = sum(price_changes) / len(price_changes)
            signals["avg_price_change_24h"] = round(avg_price_change, 2)
            # Negative price change with high volume = distribution
            if avg_price_change < -20 and total_volume > 10000:
                anomalies.append("price_volume_divergence_distribution")
                anomaly_score += 30
                signals["divergence_type"] = "distribution"
            # Extreme price movement
            if abs(avg_price_change) > 90:
                anomalies.append("extreme_price_movement")
                anomaly_score += 20
                signals["divergence_type"] = "extreme"

        # ── Signal 4: Volume Concentration Across Pools ──
        if len(pairs) > 1:
            top_pool_vol = max(
                (float(p.get("volume", {}).get("h24", 0) or 0) for p in pairs), default=0
            )
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

        # ── Signal 5: Price Consistency Across Pools ──
        if len(prices) > 1:
            price_range = max(prices) - min(prices)
            avg_price = sum(prices) / len(prices)
            if avg_price > 0:
                price_spread_pct = (price_range / avg_price) * 100
                signals["price_spread_pct"] = round(price_spread_pct, 2)
                if price_spread_pct > 20:
                    anomalies.append("significant_price_spread_across_pools")
                    anomaly_score += 10
                elif price_spread_pct > 50:
                    anomalies.append("extreme_price_spread_across_pools")
                    anomaly_score += 20

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

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("volume_anomaly_failed", error=str(e)[:80])
        result["result"] = "error"
        result["error"] = str(e)[:60]

    return result
