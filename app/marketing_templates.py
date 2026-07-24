"""
Marketing Content Templates - Auto-generated posts for X, Telegram, Discord.
Professional copy for wins, losses, KOL scorecards, security alerts.
"""

# ── X/Twitter Post Templates ──────────────────────────────────

from __future__ import annotations

X_TEMPLATES = {
    "big_win": """🎉 BIG WIN ALERT! 🎉

Wallet: {wallet_short}
Token: {token_symbol}
Profit: +${pnl_usd:,.0f} ({pnl_pct:.1f}%)

This whale called it early and rode it all the way up! 🐋

Track smart money: rugmunch.io/wallet/{wallet_address}

#Crypto #MemeCoin #SmartMoney #Trading

@cryptorugmunch
""",
    "big_loss": """💀 LOSS PORN 💀

Wallet: {wallet_short}
Token: {token_symbol}
Loss: -${pnl_usd:,.0f} ({pnl_pct:.1f}%)

Oof. Another reminder to take profits, friends. 📉

Learn from their mistakes: rugmunch.io/wallet/{wallet_address}

#Crypto #Trading #LossPorn #DeFi

@cryptorugmunch
""",
    "kol_scorecard": """📊 KOL SCORECARD: @{kol_handle}

Win Rate: {win_rate:.1f}%
Total Calls: {total_calls}
Avg PnL: {avg_pnl:.1f}%
Followers: {follower_count:,}

Tier: {tier}

Track their calls: rugmunch.io/kol/{kol_handle}

#CryptoTwitter #KOL #Alpha #Trading

@cryptorugmunch
""",
    "rugpull": """🚨 RUG PULL DETECTED 🚨

Token: {token_symbol}
Stolen: ${amount_stolen:,.0f}
Victims: {victims}
Type: {rug_type}

Deployer: {deployer_short}

⚠️ STAY AWAY FROM THIS TOKEN

Full analysis: rugmunch.io/rug/{token_address}

#RugPull #CryptoScam #DeFi #StaySafe

@cryptorugmunch
""",
    "whale_move": """🐋 WHALE ALERT 🐋

{wallet_label}
{action} {amount:,.0f} {symbol}
Value: ${usd_value:,.0f}

Destination: {destination}

Track this whale: rugmunch.io/whale/{wallet_address}

#WhaleAlert #Crypto #SmartMoney

@cryptorugmunch
""",
    "smart_money": """🧠 SMART MONEY PICK 🧠

{wallet_label}
Win Rate: {win_rate:.1f}%

Just bought: {token_symbol}
Position: ${position_size:,.0f}
Entry: ${entry_price}
Current PnL: +{current_pnl:.1f}%

Follow their moves: rugmunch.io/wallet/{wallet_address}

#SmartMoney #Crypto #Alpha #Trading

@cryptorugmunch
""",
    "hack_alert": """🚨 HACK ALERT 🚨

Protocol: {protocol_name}
Amount: ${amount_usd:,.0f}
Chain: {chain}
Type: {attack_type}

{description}

Stay safe out there! 🔒

#CryptoSecurity #DeFi #HackAlert

@cryptorugmunch
""",
    "launch_announcement": """🚀 RMI INTELLIGENCE PLATFORM IS LIVE

Track smart money. Avoid rugs. Find alpha.

✅ Real-time whale tracking
✅ KOL scorecards
✅ Rugpull detection
✅ Meme intelligence

Join now: rugmunch.io

#Crypto #DeFi #Trading #Alpha

@cryptorugmunch
""",
}

# ── Telegram Post Templates ───────────────────────────────────

TELEGRAM_TEMPLATES = {
    "big_win": """🎉 *BIG WIN ALERT!* 🎉

*Wallet:* `{wallet_short}`
*Token:* {token_symbol}
*Profit:* +${pnl_usd:,.0f} (*{pnl_pct:.1f}%*)

This whale called it early and rode it all the way up! 🐋

📊 *Track smart money:* rugmunch.io/wallet/{wallet_address}

#Crypto #MemeCoin #SmartMoney #Trading
""",
    "big_loss": """💀 *LOSS PORN* 💀

*Wallet:* `{wallet_short}`
*Token:* {token_symbol}
*Loss:* -${pnl_usd:,.0f} (*{pnl_pct:.1f}%)

Oof. Another reminder to take profits, friends. 📉

📚 *Learn from their mistakes:* rugmunch.io/wallet/{wallet_address}

#Crypto #Trading #LossPorn #DeFi
""",
    "kol_scorecard": """📊 *KOL SCORECARD: @{kol_handle}*

*Win Rate:* {win_rate:.1f}%
*Total Calls:* {total_calls}
*Avg PnL:* {avg_pnl:.1f}%
*Followers:* {follower_count:,}

*Tier:* {tier}

📈 *Track their calls:* rugmunch.io/kol/{kol_handle}

#CryptoTwitter #KOL #Alpha #Trading
""",
    "rugpull": """🚨 *RUG PULL DETECTED* 🚨

*Token:* {token_symbol}
*Stolen:* ${amount_stolen:,.0f}
*Victims:* {victims}
*Type:* {rug_type}

*Deployer:* `{deployer_short}`

⚠️ *STAY AWAY FROM THIS TOKEN*

🔍 *Full analysis:* rugmunch.io/rug/{token_address}

#RugPull #CryptoScam #DeFi #StaySafe
""",
    "whale_move": """🐋 *WHALE ALERT* 🐋

*{wallet_label}*
*{action}* {amount:,.0f} {symbol}
*Value:* ${usd_value:,.0f}

*Destination:* {destination}

📊 *Track this whale:* rugmunch.io/whale/{wallet_address}

#WhaleAlert #Crypto #SmartMoney
""",
    "smart_money": """🧠 *SMART MONEY PICK* 🧠

*{wallet_label}*
*Win Rate:* {win_rate:.1f}%

Just bought: *{token_symbol}*
*Position:* ${position_size:,.0f}
*Entry:* ${entry_price}
*Current PnL:* +{current_pnl:.1f}%

📈 *Follow their moves:* rugmunch.io/wallet/{wallet_address}

#SmartMoney #Crypto #Alpha #Trading
""",
    "daily_roundup": """📊 *DAILY INTELLIGENCE ROUNDUP*

📅 {date}

🎯 *Top Wins:*
{top_wins}

💀 *Top Losses:*
{top_losses}

🐋 *Notable Whale Moves:*
{whale_moves}

🚨 *Security Alerts:*
{security_alerts}

Full report: rugmunch.io/daily/{date}

#Crypto #DailyRoundup #Intelligence
""",
    "weekly_kol_rankings": """📊 *WEEKLY KOL RANKINGS*

Week {week_number}

🥇 *#1:* @{kol_1} - {win_rate_1:.1f}% win rate
🥈 *#2:* @{kol_2} - {win_rate_2:.1f}% win rate
🥉 *#3:* @{kol_3} - {win_rate_3:.1f}% win rate

See full leaderboard: rugmunch.io/kol/leaderboard

#KOL #CryptoTwitter #Rankings
""",
}

# ── Discord Post Templates ────────────────────────────────────

DISCORD_TEMPLATES = {
    "big_win": """
🎉 **BIG WIN ALERT!** 🎉

**Wallet:** `{wallet_short}`
**Token:** {token_symbol}
**Profit:** +${pnl_usd:,.0f} (**{pnl_pct:.1f}%**)

This whale called it early and rode it all the way up! 🐋

📊 **Track smart money:** https://rugmunch.io/wallet/{wallet_address}
""",
    "big_loss": """
💀 **LOSS PORN** 💀

**Wallet:** `{wallet_short}`
**Token:** {token_symbol}
**Loss:** -${pnl_usd:,.0f} (**{pnl_pct:.1f}%**)

Oof. Another reminder to take profits, friends. 📉

📚 **Learn from their mistakes:** https://rugmunch.io/wallet/{wallet_address}
""",
    "kol_scorecard": """
📊 **KOL SCORECARD: @{kol_handle}**

**Win Rate:** {win_rate:.1f}%
**Total Calls:** {total_calls}
**Avg PnL:** {avg_pnl:.1f}%
**Followers:** {follower_count:,}

**Tier:** {tier}

📈 **Track their calls:** https://rugmunch.io/kol/{kol_handle}
""",
    "rugpull": """
🚨 **RUG PULL DETECTED** 🚨

**Token:** {token_symbol}
**Stolen:** ${amount_stolen:,.0f}
**Victims:** {victims}
**Type:** {rug_type}

**Deployer:** `{deployer_short}`

⚠️ **STAY AWAY FROM THIS TOKEN**

🔍 **Full analysis:** https://rugmunch.io/rug/{token_address}

@everyone Stay safe!
""",
    "announcement": """
📢 **RMI ANNOUNCEMENT**

{message}

🔗 **Learn more:** https://rugmunch.io

{reactions}
""",
}

# ── Content Generation Functions ──────────────────────────────

def format_wallet_short(address: str) -> str:
    """Format wallet address for display."""
    if len(address) >= 14:
        return f"{address[:8]}...{address[-6:]}"
    return address

def generate_x_post(template_name: str, data: dict) -> str:
    """Generate X/Twitter post from template."""
    template = X_TEMPLATES.get(template_name, "")

    # Add wallet_short if wallet_address provided
    if "wallet_address" in data and "wallet_short" not in data:
        data["wallet_short"] = format_wallet_short(data["wallet_address"])
    if "deployer_address" in data and "deployer_short" not in data:
        data["deployer_short"] = format_wallet_short(data["deployer_address"])

    # Format numbers
    for key, value in data.items():
        if isinstance(value, float):
            data[key] = round(value, 2)

    return template.format(**data)

def generate_telegram_post(template_name: str, data: dict) -> str:
    """Generate Telegram post from template (Markdown)."""
    template = TELEGRAM_TEMPLATES.get(template_name, "")

    # Add wallet_short if wallet_address provided
    if "wallet_address" in data and "wallet_short" not in data:
        data["wallet_short"] = format_wallet_short(data["wallet_address"])
    if "deployer_address" in data and "deployer_short" not in data:
        data["deployer_short"] = format_wallet_short(data["deployer_address"])

    # Format numbers
    for key, value in data.items():
        if isinstance(value, float):
            data[key] = round(value, 2)

    return template.format(**data)

def generate_discord_post(template_name: str, data: dict) -> str:
    """Generate Discord post from template."""
    template = DISCORD_TEMPLATES.get(template_name, "")

    # Add wallet_short if wallet_address provided
    if "wallet_address" in data and "wallet_short" not in data:
        data["wallet_short"] = format_wallet_short(data["wallet_address"])
    if "deployer_address" in data and "deployer_short" not in data:
        data["deployer_short"] = format_wallet_short(data["deployer_address"])

    # Format numbers
    for key, value in data.items():
        if isinstance(value, float):
            data[key] = round(value, 2)

    return template.format(**data)

def generate_all_posts(template_name: str, data: dict) -> dict:
    """Generate posts for all platforms."""
    return {
        "x": generate_x_post(template_name, data),
        "telegram": generate_telegram_post(template_name, data),
        "discord": generate_discord_post(template_name, data),
    }
