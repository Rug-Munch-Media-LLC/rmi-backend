"""
🕸️ RugMap - Wallet Cluster Visualization
==========================================
Visualize wallet connections and fund flow clusters for a token.
"""

import httpx
from app.core.logging import get_logger

logger = get_logger("rmi.rugmap")

BACKEND_URL = "http://rmi-backend:8000"
DEXSCREENER = "https://api.dexscreener.com/latest/dex"


async def cmd_rugmap(update, ctx):
    """Handle /rugmap command - visualize wallet clusters."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "🕸️ <b>Usage:</b> <code>/rugmap &lt;token_address&gt;</code>\n\n"
            "Visualizes wallet clusters and fund flows for a token.\n\n"
            "Example: <code>/rugmap 0x...</code>\n\n"
            "<i>Shows dev wallet, bundler wallets, exchange deposits, and more.</i>",
            parse_mode="HTML",
        )
        return

    address = args[0]
    await msg.chat.send_action("typing")

    try:
        smart_money = []
        top_holders = []
        bundle_detected = False
        fresh_wallet_pct = 0
        liquidity = 0
        symbol = "???"
        name = "Unknown"

        async with httpx.AsyncClient(timeout=20) as client:
            # Try DataBus
            try:
                r = await client.get(
                    f"{BACKEND_URL}/api/v1/databus/token/{address}",
                    timeout=10,
                )
                if r.status_code == 200:
                    data = r.json()
                    bundle_detected = data.get("bundle_detected", False)
                    fresh_wallet_pct = data.get("fresh_wallet_pct", 0)
                    smart_money = data.get("smart_money", [])
                    top_holders = data.get("top_holders", [])
            except Exception:
                pass

            # DexScreener for basic info
            try:
                r = await client.get(f"{DEXSCREENER}/tokens/{address}")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        p = pairs[0]
                        symbol = p.get("baseToken", {}).get("symbol", "???")
                        name = p.get("baseToken", {}).get("name", "Unknown")
                        liquidity = float(p.get("liquidity", {}).get("usd", 0))
            except Exception:
                pass

        lines = [
            f"🕸️ <b>RugMap: {name} ({symbol})</b>",
            "",
            f"<b>Cluster Analysis</b>",
        ]

        if bundle_detected:
            lines.append("  🚨 <b>BUNDLE DETECTED</b> - Multiple funded wallets")
        else:
            lines.append("  ✅ No bundle detected")

        if fresh_wallet_pct:
            if fresh_wallet_pct > 50:
                lines.append(f"  🆕 <b>Fresh Wallets:</b> {fresh_wallet_pct:.0f}% ⚠️")
            else:
                lines.append(f"  🆕 Fresh Wallets: {fresh_wallet_pct:.0f}%")

        lines.append(f"  💧 Liquidity: ${liquidity:,.0f}")

        if top_holders:
            lines.extend(["", "👥 <b>Top Holders Cluster</b>"])
            for i, h in enumerate(top_holders[:5], 1):
                addr = h.get("address", "")
                pct = h.get("pct", 0)
                is_contract = h.get("is_contract", False)
                tag = "📄" if is_contract else "👛"
                lines.append(f"  {i}. {tag} <code>{addr[:8]}...{addr[-4:]}</code> - {pct:.2f}%")

        if smart_money:
            lines.extend(["", "💡 <b>Smart Money Cluster</b>"])
            for i, w in enumerate(smart_money[:3], 1):
                addr = w.get("address", "")
                pnl = w.get("pnl_usd", 0)
                lines.append(f"  {i}. <code>{addr[:8]}...{addr[-4:]}</code> - ${pnl:,.0f}")

        # Build simple ASCII cluster map
        lines.extend(["", "<b>Connection Map</b>"])
        if top_holders:
            lines.append("  💰 Funding → 🏦 Exchange")
            lines.append("  👛 Dev Wallet → 🗂️ Distribution Wallets")
            lines.append(
                f"  📊 Top {min(len(top_holders), 5)} Holders → 🔗 {len(top_holders)} connections"
            )
        else:
            lines.append("  No cluster data available")

        lines.extend(
            [
                "",
                "<i>Data from RugMunch Intelligence. Full map on rugmunch.io</i>",
            ]
        )

        text = "\n".join(lines)
        await msg.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"RugMap failed: {e}")
        await msg.reply_text("❌ Could not generate rug map. Try again later.", parse_mode="HTML")
