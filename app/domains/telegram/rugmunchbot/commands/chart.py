"""
📊 Price Chart - Powered by DexScreener
=========================================
Generate simple price charts for tokens.
"""

import httpx

from app.core.logging import get_logger

logger = get_logger("rmi.chart")

DEXSCREENER = "https://api.dexscreener.com/latest/dex"


async def cmd_chart(update, ctx):
    """Handle /chart command - show price chart for a token."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "📊 <b>Usage:</b> <code>/chart &lt;token_address&gt;</code>\n\n"
            "Example: <code>/chart 0x... or So11111111111111111111111111111111111111112</code>",
            parse_mode="HTML",
        )
        return

    address = args[0]
    await msg.chat.send_action("typing")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{DEXSCREENER}/tokens/{address}")
            if r.status_code != 200:
                await msg.reply_text("❌ Could not fetch token data.", parse_mode="HTML")
                return

            data = r.json()
            pairs = data.get("pairs", [])
            if not pairs:
                await msg.reply_text("❌ No trading pairs found for this token.", parse_mode="HTML")
                return

            p = pairs[0]
            symbol = p.get("baseToken", {}).get("symbol", "???")
            name = p.get("baseToken", {}).get("name", "Unknown")
            price = float(p.get("priceUsd", 0))
            change_1h = float(p.get("priceChange", {}).get("h1", 0))
            change_24h = float(p.get("priceChange", {}).get("h24", 0))
            change_7d = float(p.get("priceChange", {}).get("d7", 0))
            vol_24h = float(p.get("volume", {}).get("h24", 0))

            # Simple text-based sparkline using price direction indicators
            trend_1h = "🟢" if change_1h > 0 else "🔴" if change_1h < 0 else "⚪"
            trend_24h = "🟢" if change_24h > 0 else "🔴" if change_24h < 0 else "⚪"
            trend_7d = "🟢" if change_7d > 0 else "🔴" if change_7d < 0 else "⚪"

            lines = [
                f"📊 <b>{name} ({symbol})</b>",
                f"💰 <b>Price:</b> ${price:.8f}",
                "",
                "<b>Price Change</b>",
                f"  {trend_1h} 1h:  <b>{change_1h:+.2f}%</b>",
                f"  {trend_24h} 24h: <b>{change_24h:+.2f}%</b>",
                f"  {trend_7d} 7d:  <b>{change_7d:+.2f}%</b>",
                "",
                f"📈 <b>24h Volume:</b> ${vol_24h:,.0f}",
                "",
                "<i>Real-time data from DexScreener</i>",
            ]

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:4000] + "\n\n<i>...truncated</i>"

            await msg.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Chart failed: {e}")
        await msg.reply_text("❌ Error generating chart. Try again later.", parse_mode="HTML")
