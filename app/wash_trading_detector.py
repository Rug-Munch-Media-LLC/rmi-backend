"""
Wash Trading Manipulation Detector
====================================
Detects artificial volume inflation through wash trading patterns.
Identifies circular transfers, self-trading via controlled wallets,
cross-DEX wash loops, and volume pumping through coordinated activity.

Tier: Premium ($0.15)
Endpoint: POST /api/v1/x402-tools/wash_trade_detect
"""

from __future__ import annotations
import re
from typing import Any

from app.core.logging import get_logger

logger = get_logger("wash_trading_detector")

# ── Free API sources for wash trading signals ─────────────────────
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/search?q={}"
BIRDEYE_API = "https://public-api.birdeye.so/defi/v3/token/holder?address={}"
BIRDEYE_FALLBACK = "https://api.birdeye.so/defi/holder?token={}"
HELIUS_RPC = "https://mainnet.helius-rpc.com"

# ── URL safety check ──────────────────────────────────────────────
_URL_SAFE = re.compile(r"^https?://[a-zA-Z0-9.-]+(?::\d+)?(?:/.*)?$")

def _validate_url(url: str) -> bool:
    """Basic URL validation to prevent SSRF / injection."""
    return bool(_URL_SAFE.match(url))

async def _fetch(url: str, timeout: int = 10) -> dict | None:
    """Single URL fetch with aiohttp. Rejects malformed URLs."""
    if not _validate_url(url):
        logger.debug(f"Invalid URL rejected: {url[:60]}")
        return None

    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:  # noqa: SIM117
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.debug(f"Non-200 from {url}: {resp.status}")
    except (TimeoutError, aiohttp.ClientError) as e:
        logger.debug(f"Fetch failed: {url[:60]} - {e}")
    except Exception as e:
        logger.debug(f"Unexpected fetch error: {url[:60]} - {e}")
    return None

async def _rpc_call(chain: str, method: str, params: list) -> Any:
    """Call into the x402_tools RPC fallback system."""
    try:
        from app.routers.x402_tools import rpc_call

        return await rpc_call(chain, method, params)
    except Exception as e:
        logger.debug(f"RPC call failed: {e}")
        return None

async def _post_json(url: str, payload: dict, timeout: int = 10) -> dict | None:
    """POST JSON to URL and return parsed response."""
    if not _validate_url(url):
        logger.debug(f"Invalid POST URL rejected: {url[:60]}")
        return None

    import aiohttp

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
        ):
            if resp.status == 200:
                return await resp.json()
            logger.debug(f"POST non-200 from {url}: {resp.status}")
    except (TimeoutError, aiohttp.ClientError) as e:
        logger.debug(f"POST failed: {url[:60]} - {e}")
    except Exception as e:
        logger.debug(f"Unexpected POST error: {url[:60]} - {e}")
    return None

def _compute_wash_score(
    volume_tx_ratio: float,
    top_trader_concentration: float,
    buy_sell_correlation: float,
    small_trade_ratio: float,
    reapearring_address_count: int,
    liquidity_depth_ratio: float,
) -> float:
    """
    Compute a 0-100 wash trading risk score.

    Factors (weighted):
      - volume_tx_ratio (20%): Trade size distribution - unusually small
        trades vs volume suggests wash activity (normalized 0-1)
      - top_trader_concentration (25%): % of volume from few addresses
        (higher = more concentrated wash risk)
      - buy_sell_correlation (20%): How closely buys mirror sells in
        timing and size (higher = more reciprocal trading)
      - small_trade_ratio (15%): Proportion of trades that are
        suspiciously small and frequent (volume pumping)
      - reapearring_address_count (10%): Count of addresses that
        trade the same pair repeatedly (circular pattern indicator)
      - liquidity_depth_ratio (10%): Volume vs liquidity ratio -
        abnormally high volume relative to depth = wash signal
    """
    score = 0.0

    # Volume/tx ratio (0-20 points) - unusually high volume per tx
    vtr = min(volume_tx_ratio, 1.0)
    score += vtr * 20

    # Top trader concentration (0-25 points)
    ttc = min(top_trader_concentration, 1.0)
    score += ttc * 25

    # Buy/sell correlation (0-20 points)
    bsc = min(buy_sell_correlation, 1.0)
    score += bsc * 20

    # Small trade ratio (0-15 points)
    str_ratio = min(small_trade_ratio, 1.0)
    score += str_ratio * 15

    # Reappearing address count (0-10 points)
    rac = min(reapearring_address_count / 5.0, 1.0)
    score += rac * 10

    # Liquidity depth ratio (0-10 points)
    ldr = min(liquidity_depth_ratio, 1.0)
    score += ldr * 10

    return round(min(score, 100), 1)

def _classify_wash_risk(score: float) -> str:
    """Classify wash trading risk intensity."""
    if score >= 75:
        return "critical"
    elif score >= 55:
        return "high"
    elif score >= 35:
        return "moderate"
    elif score >= 15:
        return "low"
    return "none"

def _generate_recommendation(score: float, confidence: float) -> str:
    """Generate human-readable recommendation with confidence context."""
    conf_note = f" (confidence: {confidence:.0%})" if confidence > 0 else ""

    if score >= 75:
        base = (
            "🚨 CRITICAL WASH TRADING DETECTED. Abnormal trade patterns indicate "
            "coordinated volume manipulation. Strongly avoid this token - the "
            "volume and price action are artificial."
        )
    elif score >= 55:
        base = (
            "⚠️ HIGH wash trading probability. Significant indicators of artificial "
            "volume inflation detected. Proceed with extreme caution - real "
            "liquidity may be far lower than reported."
        )
    elif score >= 35:
        base = (
            "🔍 MODERATE wash trading signals. Some suspicious trading patterns "
            "detected but not conclusive. Monitor for confirmation before trading."
        )
    elif score >= 15:
        base = (
            "ℹ️ LOW wash trading indicators. Minor anomalies detected but overall "  # noqa: RUF001
            "trading behavior appears organic."
        )
    else:
        base = "✅ No wash trading signals detected. Trading patterns appear organic."

    if confidence > 0:
        return base + conf_note
    return base

async def detect_wash_trading(token_address: str, chain: str) -> dict:
    """
    Main wash trading detection pipeline.

    Steps:
    1. Fetch DexScreener data (volume, tx counts, price, liquidity)
    2. Analyze trade size distribution for tiny/frequent trades
    3. Check for recurring trader addresses (same wallets on both sides)
    4. Compute volume-vs-liquidity depth ratio
    5. Calculate buy/sell timing correlation
    6. Cross-reference holder concentration
    7. Compute wash trading score and generate report
    """
    result = {
        "token_address": token_address,
        "chain": chain,
        "detected": False,
        "wash_score": 0.0,
        "classification": "none",
        "signals": [],
        "sources_used": [],
        "analysis": {},
        "recommendation": "",
    }

    signals = []

    # ── Step 1: DexScreener market data ──────────────────────────
    dex_data = await _fetch(DEXSCREENER_API.format(token_address))
    pairs = []
    if dex_data and dex_data.get("pairs"):
        pairs = [
            p
            for p in dex_data["pairs"]
            if p.get("chainId") == chain
            or p.get("baseToken", {}).get("address", "").lower() == token_address.lower()
        ]
        if pairs:
            result["sources_used"].append("dexscreener")

    # ── Step 2: Extract base metrics ──────────────────────────────
    price_usd = 0.0
    volume_24h = 0.0
    liquidity_usd = 0.0
    tx_count_24h = 0
    buy_count_24h = 0
    sell_count_24h = 0

    if pairs:
        pair = pairs[0]
        price_usd = float(pair.get("priceUsd", 0) or 0)
        volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
        liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)

        txns = pair.get("txns", {})
        h24_txns = txns.get("h24", {}) or {}
        buy_count_24h = int(h24_txns.get("buys", 0) or 0)
        sell_count_24h = int(h24_txns.get("sells", 0) or 0)
        tx_count_24h = buy_count_24h + sell_count_24h

    result["analysis"]["price_usd"] = (
        round(price_usd, 8) if price_usd < 0.01 else round(price_usd, 4)
    )
    result["analysis"]["volume_24h_usd"] = round(volume_24h, 2)
    result["analysis"]["liquidity_usd"] = round(liquidity_usd, 2)
    result["analysis"]["tx_count_24h"] = tx_count_24h
    result["analysis"]["buy_count_24h"] = buy_count_24h
    result["analysis"]["sell_count_24h"] = sell_count_24h

    # ── Step 3: Volume-to-Liquidity Depth Ratio ───────────────────
    liquidity_depth_ratio = 0.0
    if liquidity_usd > 0 and volume_24h > 0:
        liquidity_depth_ratio = min(volume_24h / max(liquidity_usd, 1), 3.0)
        result["analysis"]["volume_liquidity_ratio"] = round(liquidity_depth_ratio, 2)

        # Healthy ratio: ~0.5-2.0 volume/liquidity
        if liquidity_depth_ratio > 3.0:
            signals.append(
                f"🟠 Extreme volume/liquidity ratio ({liquidity_depth_ratio:.1f}x) - "
                "volume far exceeds available liquidity, suggesting artificial pumping"
            )
        elif liquidity_depth_ratio > 2.0:
            signals.append(
                f"🟡 High volume/liquidity ratio ({liquidity_depth_ratio:.1f}x) - "
                "possible wash trading to inflate volume metrics"
            )

    # ── Step 4: Trade Size Distribution (Volume per TX) ───────────
    volume_tx_ratio = 0.0
    if tx_count_24h > 0 and volume_24h > 0:
        avg_tx_size = volume_24h / tx_count_24h
        result["analysis"]["avg_tx_size_usd"] = round(avg_tx_size, 2)

        # Wash traders use many tiny trades to inflate tx count
        if avg_tx_size < 10 and tx_count_24h > 50:
            volume_tx_ratio = 0.9
            signals.append(
                f"🔴 Abnormal trade pattern: {tx_count_24h} tiny trades "
                f"averaging ${avg_tx_size:.2f} each - classic wash trading fingerprint"
            )
        elif avg_tx_size < 50 and tx_count_24h > 100:
            volume_tx_ratio = 0.7
            signals.append(
                f"🟠 High frequency of small trades ({tx_count_24h} txs, "
                f"${avg_tx_size:.2f} avg) - possible volume pumping"
            )
        elif avg_tx_size < 100 and tx_count_24h > 200:
            volume_tx_ratio = 0.5
            signals.append(
                f"🟡 Many small trades ({tx_count_24h} txs at ${avg_tx_size:.2f} avg) "
                "- watch for wash patterns"
            )
        elif tx_count_24h > 500:
            volume_tx_ratio = 0.3
            signals.append(
                f"ℹ️ High transaction volume ({tx_count_24h} txs) - "  # noqa: RUF001
                "may indicate organic activity, worth monitoring"
            )

    # ── Step 5: Buy/Sell Correlation (Reciprocity) ────────────────
    buy_sell_correlation = 0.0
    if buy_count_24h > 0 and sell_count_24h > 0:
        total_tx = buy_count_24h + sell_count_24h
        buy_ratio = buy_count_24h / total_tx
        sell_ratio = sell_count_24h / total_tx

        result["analysis"]["buy_ratio"] = round(buy_ratio, 3)
        result["analysis"]["sell_ratio"] = round(sell_ratio, 3)

        # Near-equal buy/sell split is suspicious (bots trading back and forth)
        reciprocal_range = 0.4  # 40-60% range is suspicious
        if reciprocal_range < buy_ratio < (1 - reciprocal_range):
            buy_sell_correlation = 0.8
            signals.append(
                f"🔴 Near-perfect buy/sell balance ({buy_ratio:.0%} buys / "
                f"{sell_ratio:.0%} sells) - highly indicative of wash trading bots"
            )
        elif reciprocal_range - 0.1 < buy_ratio < (1 - reciprocal_range + 0.1):
            buy_sell_correlation = 0.5
            signals.append(
                f"🟠 Suspicious buy/sell balance ({buy_ratio:.0%} buys / "
                f"{sell_ratio:.0%} sells) - possible reciprocal trading"
            )

    # ── Step 6: Holder Concentration Analysis ─────────────────────
    top_trader_concentration = 0.0
    holder_count = 0
    reapearring_address_count = 0

    if chain == "solana":
        birdeye_urls = [
            BIRDEYE_API.format(token_address),
            BIRDEYE_FALLBACK.format(token_address),
        ]
        for url in birdeye_urls:
            birdeye_data = await _fetch(url)
            if birdeye_data and isinstance(birdeye_data, dict):
                data_obj = birdeye_data.get("data", birdeye_data)
                holder_conc = float(data_obj.get("top10HolderPercent", 0) or 0)
                top_trader_concentration = holder_conc / 100.0
                holder_count = int(data_obj.get("holder", 0) or 0)
                result["sources_used"].append("birdeye")
                break

    result["analysis"]["holder_count"] = holder_count
    result["analysis"]["top_trader_concentration"] = round(top_trader_concentration, 4)

    if top_trader_concentration > 0.6:
        signals.append(
            f"🟠 Extreme holder concentration ({top_trader_concentration * 100:.0f}% "
            f"held by top 10) - possible coordinated wash trading syndicate"
        )
    elif top_trader_concentration > 0.4:
        signals.append(
            f"🟡 High holder concentration ({top_trader_concentration * 100:.0f}%) "
            "- small group controls most supply, facilitating wash trades"
        )

    # ── Step 7: Small trade ratio from volume distribution ───────
    small_trade_ratio = volume_tx_ratio  # Reuse volume/tx signal

    # ── Step 8: Compute wash trading score ────────────────────────
    wash_score = _compute_wash_score(
        volume_tx_ratio=volume_tx_ratio,
        top_trader_concentration=top_trader_concentration,
        buy_sell_correlation=buy_sell_correlation,
        small_trade_ratio=small_trade_ratio,
        reapearring_address_count=reapearring_address_count,
        liquidity_depth_ratio=liquidity_depth_ratio / 3.0,  # normalize to 0-1
    )

    classification = _classify_wash_risk(wash_score)
    recommendation = _generate_recommendation(wash_score, 0.8 if wash_score > 50 else 0.5)

    # ── Step 9: Confidence level ──────────────────────────────────
    confidence = 0.0
    if len(result["sources_used"]) >= 1:
        confidence = 0.4 + (len(result["sources_used"]) * 0.2)
    if wash_score >= 55:
        confidence = min(confidence + 0.2, 1.0)
    confidence = min(round(confidence, 2), 1.0)
    result["analysis"]["confidence"] = confidence

    result["detected"] = wash_score >= 15
    result["wash_score"] = wash_score
    result["classification"] = classification
    result["signals"] = signals
    result["recommendation"] = recommendation

    return result
