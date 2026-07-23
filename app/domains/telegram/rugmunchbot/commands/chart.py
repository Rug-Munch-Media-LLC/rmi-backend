"""
📊 Price Chart - Powered by DexScreener + CoinGecko
=====================================================
Generate text-based sparkline charts for any token using
DexScreener market data with CoinGecko price history fallback.
"""

import httpx

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot.config import (
    COINGECKO,
    DEXSCREENER,
    fmt_number,
    fmt_pct,
)

logger = get_logger("rmi.chart")

# Unicode sparkline blocks (8 levels)
_SPARKLINE_CHARS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

# DexScreener chart URL template
_DEXSCREENER_CHART_URL = "https://dexscreener.com/{chain}/{pair_address}"


def _sparkline(prices: list[float], width: int = 8) -> str:
    """Map price points to unicode sparkline blocks.

    Args:
        prices: Chronological price points (oldest → newest).
        width: Number of sparkline characters to output.

    Returns:
        Sparkline string of unicode block characters.
    """
    if not prices:
        return ""
    mn, mx = min(prices), max(prices)
    if mx == mn:
        return _SPARKLINE_CHARS[3] * min(width, len(prices))
    # Sample `width` points evenly
    step = max(len(prices) // width, 1)
    sampled = [prices[min(i * step, len(prices) - 1)] for i in range(width)]
    return "".join(_SPARKLINE_CHARS[min(int((p - mn) / (mx - mn) * 7), 7)] for p in sampled)


def _pseudo_sparkline(
    current_price: float,
    change_1h: float,
    change_6h: float | None,
    change_24h: float,
    change_7d: float,
) -> str:
    """Generate a sparkline from DexScreener price-change percentages.

    When CoinGecko has no historical data (common for shitcoins), we
    reconstruct approximate price points from the available change data.
    """
    # Build relative price history: 7d → 24h → 6h → 1h → now
    points: list[float] = []
    if change_7d != 0:
        points.append(current_price / (1 + change_7d / 100))
    if change_24h != 0:
        if change_7d == 0:
            points.append(current_price / (1 + change_24h / 100))
        else:
            # Interpolate midpoint between 7d and 24h
            p_7d = current_price / (1 + change_7d / 100)
            p_24h = current_price / (1 + change_24h / 100)
            points.append((p_7d + p_24h) / 2)
    if change_6h is not None and change_6h != 0:
        points.append(current_price / (1 + change_6h / 100))
    if change_1h != 0:
        points.append(current_price / (1 + change_1h / 100))
    else:
        # Duplicate previous point if no 1h change
        points.append(points[-1] if points else current_price)
    points.append(current_price)

    return _sparkline(points, width=8)


async def _fetch_cg_price_history(
    client: httpx.AsyncClient,
    token_name: str,
    token_symbol: str,
) -> list[float] | None:
    """Try CoinGecko for historical price data.

    Searches by name/symbol, fetches 7-day price history,
    returns list of prices (oldest → newest).
    """
    search_query = token_name or token_symbol
    if not search_query or search_query == "Unknown":
        return None
    try:
        # Step 1: Search for coin ID
        sr = await client.get(
            f"{COINGECKO}/search?query={search_query[:50]}",
            timeout=8,
        )
        if sr.status_code != 200:
            return None
        coins = sr.json().get("coins", [])
        if not coins:
            return None
        coin_id = coins[0].get("id")
        if not coin_id:
            return None

        # Step 2: Fetch 7-day market chart
        chart_r = await client.get(
            f"{COINGECKO}/coins/{coin_id}/market_chart?vs_currency=usd&days=7",
            timeout=10,
        )
        if chart_r.status_code != 200:
            return None
        raw = chart_r.json().get("prices", [])
        if not raw or len(raw) < 2:
            return None
        return [p[1] for p in raw]  # {timestamp, price}[]
    except Exception as exc:
        logger.debug("CoinGecko price history failed: %s", exc)
        return None


async def cmd_chart(update, ctx) -> None:
    """Handle /chart command — show price chart with sparkline."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "📊 <b>Usage:</b> <code>/chart &lt;token_address&gt;</code>\n\n"
            "Example: <code>/chart 0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "         <code>/chart So11111111111111111111111111111111111111112</code>",
            parse_mode="HTML",
        )
        return

    address = args[0]
    await msg.chat.send_action("typing")

    async with httpx.AsyncClient(timeout=20) as client:
        # ── Fetch DexScreener data ──
        try:
            r = await client.get(f"{DEXSCREENER}/tokens/{address}")
            if r.status_code != 200:
                await msg.reply_text(
                    "❌ Could not fetch token data from DexScreener.",
                    parse_mode="HTML",
                )
                return

            data = r.json()
            pairs = data.get("pairs", [])
            if not pairs:
                await msg.reply_text(
                    "❌ No trading pairs found for this token.",
                    parse_mode="HTML",
                )
                return
        except Exception as e:
            logger.error("DexScreener fetch failed: %s", e)
            await msg.reply_text(
                "❌ Error fetching token data. Try again later.",
                parse_mode="HTML",
            )
            return

        p = pairs[0]
        symbol = p.get("baseToken", {}).get("symbol", "???")
        name = p.get("baseToken", {}).get("name", "Unknown")
        price = float(p.get("priceUsd", 0))
        change_1h = float(p.get("priceChange", {}).get("h1", 0))
        change_6h = p.get("priceChange", {}).get("h6")  # may be None
        change_6h = float(change_6h) if change_6h is not None else None
        change_24h = float(p.get("priceChange", {}).get("h24", 0))
        change_7d = float(p.get("priceChange", {}).get("d7", 0))
        vol_24h = float(p.get("volume", {}).get("h24", 0))
        mcap = float(p.get("marketCap") or p.get("fdv") or 0)
        chain = p.get("chainId", "")
        pair_address = p.get("pairAddress", "")

        # ── Build sparkline ──
        sparkline = ""
        # Primary: CoinGecko price history
        cg_prices = await _fetch_cg_price_history(client, name, symbol)
        if cg_prices:
            sparkline = _sparkline(cg_prices, width=8)
        else:
            # Fallback: pseudo sparkline from DexScreener change data
            sparkline = _pseudo_sparkline(price, change_1h, change_6h, change_24h, change_7d)

        # ── Price change arrows ──
        def _arrow(val: float) -> str:
            if val > 0:
                return "🟢"
            if val < 0:
                return "🔴"
            return "⚪"

        # ── Build message ──
        dex_url = _DEXSCREENER_CHART_URL.format(chain=chain, pair_address=pair_address)

        lines = [
            f"📊 <b>{name}</b> ({symbol})",
            f"<code>{address[:12]}...{address[-6:]}</code>",
            "",
            sparkline,
            "",
            f"💰 <b>Price:</b> ${price:.10f}".rstrip("0").rstrip("."),
            f"📈 <b>Market Cap:</b> {fmt_number(mcap)}",
            f"📊 <b>24h Volume:</b> {fmt_number(vol_24h)}",
            "",
            "<b>Price Change</b>",
            f"  {_arrow(change_1h)} 1h:  <b>{fmt_pct(change_1h)}</b>",
            f"  {_arrow(change_24h)} 24h: <b>{fmt_pct(change_24h)}</b>",
            f"  {_arrow(change_7d)} 7d:  <b>{fmt_pct(change_7d)}</b>",
            "",
            f'📉 <a href="{dex_url}">View on DexScreener</a>',
            "",
            "<i>Sparkline: CoinGecko 7d history</i>"
            if cg_prices
            else "<i>Sparkline: DexScreener change estimates</i>",
        ]

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n\n<i>...truncated</i>"

        await msg.reply_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


__all__ = ["cmd_chart"]
