"""
👛 Wallet Portfolio Scanner
=============================
Scan a wallet and return its token portfolio.
"""

import httpx

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot.config import BACKEND_URL, DEXSCREENER

logger = get_logger("rmi.portfolio")


async def cmd_portfolio(update, ctx) -> None:
    """Handle /portfolio command - scan wallet holdings."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "👛 <b>Usage:</b> <code>/portfolio &lt;wallet_address&gt;</code>\n\n"
            "Example: <code>/portfolio 0x... or So1...11112</code>\n\n"
            "Shows token holdings, estimated value, and risk flags.",
            parse_mode="HTML",
        )
        return

    address = args[0]
    await msg.chat.send_action("typing")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{BACKEND_URL}/api/v1/databus/wallet/{address}", timeout=15)
            if r.status_code == 200:
                data = r.json()
            else:
                # Fallback: try basic Solana/EVM portfolio from external APIs
                data = {"top_tokens": [], "balance_usd": 0, "tx_count": 0}
                try:
                    r2 = await client.get(
                        f"{DEXSCREENER}/tokens/{address}",
                        timeout=10,
                    )
                    if r2.status_code == 200:
                        dex_data = r2.json()
                        pairs = dex_data.get("pairs", [])
                        if pairs:
                            p = pairs[0]
                            data["balance_usd"] = float(p.get("priceUsd", 0))
                            data["top_tokens"] = [
                                {
                                    "symbol": p.get("baseToken", {}).get("symbol", "?"),
                                    "name": p.get("baseToken", {}).get("name", "?"),
                                    "value_usd": float(p.get("liquidity", {}).get("usd", 0)),
                                }
                            ]
                except Exception as exc:
                    logger.debug("DexScreener detail failed: %s", exc)

        top_tokens = data.get("top_tokens", [])
        balance = data.get("balance_usd", 0)
        tx_count = data.get("tx_count", 0)

        lines = [
            f"👛 <b>Portfolio: <code>{address[:12]}...{address[-6:]}</code></b>",
            "",
            f"💰 <b>Estimated Balance:</b> ${balance:,.2f}",
            f"📝 <b>Transactions:</b> {tx_count:,}",
        ]

        if top_tokens:
            lines.extend(["", "🪙 <b>Token Holdings</b>"])
            for i, t in enumerate(top_tokens[:10], 1):
                sym = t.get("symbol", "???")
                val = t.get("value_usd", 0)
                pct = t.get("portfolio_pct", 0)
                if pct:
                    lines.append(f"  {i}. <b>{sym}</b> - ${val:,.2f} ({pct:.1f}%)")
                else:
                    lines.append(f"  {i}. <b>{sym}</b> - ${val:,.2f}")

        lines.append("")
        lines.append("<i>Data from RugMunch Intelligence</i>")

        text = "\n".join(lines)
        await msg.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Portfolio failed: {e}")
        await msg.reply_text(
            "❌ Could not scan portfolio. Make sure the address is valid.",
            parse_mode="HTML",
        )
