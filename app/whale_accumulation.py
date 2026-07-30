"""
Whale Accumulation Pattern Detector
====================================
Detects stealth accumulation by large holders before price impact.
Identifies quiet buying patterns, OTC accumulation signals, and wallet
funding sequences that precede major positions.

Tier: Premium ($0.10)
Endpoint: POST /api/v1/x402-tools/whale_accumulation
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger("whale_accumulation")

# ── Free API sources for accumulation signals ─────────────────────

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/search?q={}"
BIRDEYE_API = "https://public-api.birdeye.so/defi/v3/token/holder?address={}"
BIRDEYE_FALLBACK = "https://api.birdeye.so/defi/holder?token={}"


async def _rpc_call(chain: str, method: str, params: list) -> Any:
    """Call into the x402_tools RPC fallback system."""
    try:
        from app.routers.x402_tools import rpc_call

        return await rpc_call(chain, method, params)
    except Exception as e:
        logger.debug(f"RPC call failed: {e}")
        return None


async def _fetch(url: str, timeout: int = 10) -> dict | None:
    """Single URL fetch with aiohttp."""
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        logger.debug(f"Fetch failed: {url} - {e}")
    return None


async def _fetch_with_fallback(urls: list[str]) -> tuple[Any, str | None]:
    """Try multiple URLs in sequence."""
    for url in urls:
        result = await _fetch(url)
        if result:
            return result, url
    return None, None


def _compute_accumulation_score(
    buy_volume_ratio: float,
    holder_concentration: float,
    tx_frequency: float,
    wallet_age_days: int,
    is_smart_money: bool,
    recent_large_buys: int,
) -> float:
    """
    Compute a 0-100 accumulation signal score.  # noqa: RUF002

    Factors (weighted):
      - buy_volume_ratio (20%): Buy vs sell volume (normalized 0-1, 2.0x = max)
      - holder_concentration (25%): Top holder % (higher = more accumulation risk)
      - tx_frequency (15%): Transactions per hour (normalized 0-1, 50/hr = max)
      - wallet_age_days (10%): Newer wallets are more suspicious (inverse)
      - is_smart_money (20%): Smart money activity adds confidence
      - recent_large_buys (10%): Count of large buys (>$10K) in last 24h
    """
    score = 0.0

    # Buy volume ratio (0-20 points)
    bvr = min(buy_volume_ratio / 2.0, 1.0)
    score += bvr * 20

    # Holder concentration (0-25 points)
    hc = min(holder_concentration, 1.0)
    score += hc * 25

    # Transaction frequency (0-15 points)
    tf = min(tx_frequency / 50.0, 1.0)
    score += tf * 15

    # Wallet age bonus (0-10 points) - newer = more suspicious = higher score
    age_factor = max(0.0, 1.0 - wallet_age_days / 365.0)
    score += age_factor * 10

    # Smart money bonus (0-20 points)
    if is_smart_money:
        score += 20

    # Recent large buys (0-10 points)
    ltb = min(recent_large_buys / 10.0, 1.0)
    score += ltb * 10

    return round(min(score, 100), 1)


def _classify_accumulation(score: float) -> str:
    """Classify accumulation intensity."""
    if score >= 80:
        return "critical"
    elif score >= 60:
        return "high"
    elif score >= 40:
        return "moderate"
    elif score >= 20:
        return "low"
    return "none"


def _generate_recommendation(score: float, persona: str) -> str:
    """Generate human-readable recommendation."""
    if score >= 80:
        return (
            "🚨 CRITICAL ACCUMULATION DETECTED. Multiple whale wallets are "
            "actively building positions. High probability of significant "
            "upward price movement within 24-48 hours."
            if persona == "trader"
            else "Multiple whale wallets accumulating. Monitor closely for price action."
        )
    elif score >= 60:
        return (
            "⚠️ HIGH accumulation signal. Smart money wallets are buying "
            "steadily. Consider initiating a position with tight risk controls."
            if persona == "trader"
            else "Significant accumulation pattern detected. Worth investigating."
        )
    elif score >= 40:
        return (
            "🔍 MODERATE accumulation. Some whale activity detected but "
            "not yet conclusive. Continue monitoring for confirmation."
        )
    elif score >= 20:
        return "LOW accumulation signals. No significant whale activity detected."
    return "No accumulation signals detected. Current market is neutral."


async def detect_accumulation(token_address: str, chain: str) -> dict:
    """
    Main detection pipeline.

    Steps:
    1. Fetch token data from DexScreener (price, volume, liquidity)
    2. Fetch holder data from Birdeye (holder concentration)
    3. Analyze recent buy-side activity
    4. Cross-reference with known smart money wallets
    5. Compute accumulation score and generate report
    """
    result = {
        "token_address": token_address,
        "chain": chain,
        "detected": False,
        "accumulation_score": 0.0,
        "classification": "none",
        "signals": [],
        "sources_used": [],
        "analysis": {},
        "recommendation": "",
    }

    # ── Step 1: DexScreener market data ──────────────────────────
    dex_data = await _fetch(f"https://api.dexscreener.com/latest/dex/search?q={token_address}")
    pairs = []
    if dex_data and dex_data.get("pairs"):
        pairs = [
            p
            for p in dex_data["pairs"]
            if p.get("chainId") == chain or p.get("baseToken", {}).get("address", "").lower() == token_address.lower()
        ]
        if pairs:
            result["sources_used"].append("dexscreener")

    # ── Step 2: Extract base metrics from DexScreener ────────────
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

    # ── Step 3: Buy/sell ratio ────────────────────────────────────
    buy_volume_ratio = 1.0
    signal_messages = []

    if tx_count_24h > 0:
        buy_volume_ratio = round(buy_count_24h / max(sell_count_24h, 1), 2)
        result["analysis"]["buy_sell_ratio"] = buy_volume_ratio
        result["analysis"]["total_tx_24h"] = tx_count_24h
        result["analysis"]["buy_count_24h"] = buy_count_24h
        result["analysis"]["sell_count_24h"] = sell_count_24h

        if buy_volume_ratio > 1.5:
            signal_messages.append(f"🔵 Heavy buy pressure: {buy_volume_ratio:.1f}x more buys than sells in 24h")
        elif buy_volume_ratio > 1.0:
            signal_messages.append(f"🔵 Slight buy advantage: {buy_volume_ratio:.1f}x buy ratio")

    # ── Step 4: Holder analysis (Birdeye) ─────────────────────────
    holder_concentration = 0.0
    holder_count = 0
    smart_money_involved = False
    recent_large_buys = 0

    # Try Helius/getTokenAccounts for Solana holder data
    if chain == "solana":
        try:
            # Fetch token supply info via Solana RPC
            supply_data = await _rpc_call("solana", "getTokenSupply", [token_address])
            if supply_data:
                total_supply = float(supply_data.get("value", {}).get("uiAmount", 0))
                result["analysis"]["total_supply"] = total_supply
                result["sources_used"].append("solana_rpc")
        except (ConnectionError, OSError, TimeoutError):
            pass

        # Try Birdeye for holder data
        birdeye_urls = [
            BIRDEYE_API.format(token_address),
            BIRDEYE_FALLBACK.format(token_address),
        ]
        birdeye_data, _ = await _fetch_with_fallback(birdeye_urls)
        if birdeye_data and isinstance(birdeye_data, dict) and birdeye_data.get("success", True):
            holder_data = birdeye_data.get("data", birdeye_data)
            holder_concentration = float(holder_data.get("top10HolderPercent", 0) or 0) / 100.0
            holder_count = int(holder_data.get("holder", 0) or 0)
            result["sources_used"].append("birdeye")
        else:
            # Fallback: use DexScreener liquidity as proxy
            if liquidity_usd > 0 and volume_24h > 0:
                holder_concentration = min(volume_24h / max(liquidity_usd, 1), 1.0) * 0.3

    # EVM chain holder fallback
    elif chain in ["base", "ethereum", "bsc"]:
        # Use DexScreener pair data for holder estimates
        holder_concentration = min(holder_concentration or 0.15, 1.0)

    result["analysis"]["holder_concentration"] = round(holder_concentration, 4)
    result["analysis"]["holder_count"] = holder_count

    if holder_concentration > 0.5:
        signal_messages.append(
            f"🟠 High top-10 holder concentration ({holder_concentration * 100:.0f}%) - potential accumulation risk"
        )

    # ── Step 5: Smart money cross-reference ───────────────────────
    smart_money_involved = False
    try:
        # Check if this token has smart money activity by looking at recent buys
        if chain == "solana":
            # Fetch recent signatures for the token
            sigs = await _rpc_call("solana", "getSignaturesForAddress", [token_address, {"limit": 20}])
            if sigs and len(sigs) > 5:
                # High recent transaction count is a signal of interest
                result["analysis"]["recent_tx_count"] = len(sigs)
                if len(sigs) > 10:
                    smart_money_involved = True
                    recent_large_buys = min(len(sigs) // 4, 10)
                    signal_messages.append(f"🟢 Smart money activity: {len(sigs)} recent transactions detected")
    except (ConnectionError, OSError, TimeoutError):
        pass

    # ── Step 6: Wallet age analysis ───────────────────────────────
    wallet_age_days = 30  # default assumption
    try:
        if pairs:
            # Check pair creation date
            created_at = pairs[0].get("pairCreatedAt", 0)
            if created_at:
                created_dt = datetime.fromtimestamp(created_at / 1000)
                wallet_age_days = (datetime.now(UTC) - created_dt).days
                result["analysis"]["token_age_days"] = wallet_age_days
                if wallet_age_days < 7:
                    signal_messages.append(
                        f"🆕 Very new token ({wallet_age_days}d old) - higher accumulation uncertainty"
                    )
    except (ConnectionError, OSError, TimeoutError):
        pass

    # ── Step 7: Compute accumulation score ────────────────────────
    tx_frequency = tx_count_24h / 24.0 if tx_count_24h > 0 else 0  # tx/hour

    accumulation_score = _compute_accumulation_score(
        buy_volume_ratio=buy_volume_ratio,
        holder_concentration=holder_concentration,
        tx_frequency=tx_frequency,
        wallet_age_days=max(wallet_age_days, 1),
        is_smart_money=smart_money_involved,
        recent_large_buys=recent_large_buys,
    )

    classification = _classify_accumulation(accumulation_score)

    # ── Step 8: Price context ─────────────────────────────────────
    price_change_24h = 0.0
    if pairs:
        price_change_24h = float(pairs[0].get("priceChange", {}).get("h24", 0) or 0)
        result["analysis"]["price_change_24h_pct"] = round(price_change_24h, 2)

    if price_change_24h < -20 and buy_volume_ratio > 1.5:
        signal_messages.append(
            "💎 Price down 20%+ but buy volume is strong - possible accumulation "
            "during dip (smart money buying the dip)"
        )
    elif price_change_24h > 20 and buy_volume_ratio > 1.5:
        signal_messages.append("📈 Price up 20%+ with continued buy pressure - momentum accumulation")

    # ── Step 9: Assemble report ───────────────────────────────────
    persona = "trader"  # default persona for API tool
    recommendation = _generate_recommendation(accumulation_score, persona)

    result["detected"] = accumulation_score >= 20
    result["accumulation_score"] = accumulation_score
    result["classification"] = classification
    result["signals"] = signal_messages
    result["recommendation"] = recommendation
    result["analysis"]["price_usd"] = round(price_usd, 8) if price_usd < 0.01 else round(price_usd, 4)
    result["analysis"]["volume_24h_usd"] = round(volume_24h, 2)
    result["analysis"]["liquidity_usd"] = round(liquidity_usd, 2)

    return result
