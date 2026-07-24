"""
Meme Intelligence Platform - Meme Coin Tracking, Smart Money in Memes,
Big Wins/Losses, KOL Scorecards, Social Monitoring.

Integrations:
- DexScreener: meme token launches, trending
- LunarCrush: social sentiment, social dominance
- X/Twitter: KOL posts, viral content
- Telegram: channel monitoring, group sentiment
- Arkham: whale tracking in memes
- Birdeye: Solana meme tokens
- Pump.fun: new meme launches
"""

import os
from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Meme Intelligence Data Structures ─────────────────────────

MEME_CHAINS = ["solana", "ethereum", "base", "bsc", "arbitrum"]

MEME_CATEGORIES = {
    "dog": ["dog", "doge", "shib", "akita", "kishu"],
    "cat": ["cat", "pepe", "mog", "meow"],
    "politi": ["trump", "biden", "maga", "politics"],
    "celeb": ["celeb", "influencer", "famous"],
    "ai": ["ai", "gpt", "neural", "chat"],
    "gaming": ["game", "gaming", "nft", "metaverse"],
    "other": [],
}

# KOL Database Schema
KOL_DATABASE = {
    # Example structure - populate from research
    "twitter_handles": [],
    "telegram_channels": [],
    "wallet_addresses": [],
    "track_record": {},  # past calls, win rate
    "follower_counts": {},
    "engagement_rates": {},
}

# ── Meme Token Intelligence ────────────────────────────────────


async def get_meme_trending(limit: int = 50) -> list[dict]:
    """Get trending meme tokens across chains."""
    from app.unified_provider import get_unified_provider

    provider = get_unified_provider()
    memes = []

    for chain in MEME_CHAINS:
        try:
            # Get trending from DexScreener
            trending = await provider.get_dexscreener_trending(chain)

            for token in trending[:20]:
                # Classify as meme based on name/symbol
                category = classify_meme_category(
                    token.get("baseToken", {}).get("symbol", ""),
                    token.get("baseToken", {}).get("name", ""),
                )

                if category != "other":
                    memes.append(
                        {
                            "address": token.get("baseToken", {}).get("address"),
                            "symbol": token.get("baseToken", {}).get("symbol"),
                            "name": token.get("baseToken", {}).get("name"),
                            "chain": chain,
                            "category": category,
                            "price_usd": token.get("priceUsd"),
                            "volume_24h": token.get("volume", {}).get("h24"),
                            "price_change_24h": token.get("priceChange", {}).get("h24"),
                            "liquidity_usd": token.get("liquidity", {}).get("usd"),
                            "fdv": token.get("fdv"),
                            "pair_age_hours": token.get("pairCreatedAt"),
                            "is_meme": True,
                        }
                    )
        except Exception as e:
            logger.debug(f"Error getting meme trending for {chain}: {e}")

    # Sort by volume
    memes.sort(key=lambda x: x.get("volume_24h", 0), reverse=True)

    return memes[:limit]


async def get_smart_money_in_memes(limit: int = 20) -> list[dict]:
    """Track known smart money wallets trading memes."""
    from supabase import create_client

    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    # Query for smart money activities in meme tokens
    result = (
        supabase.table("smart_money_activities")
        .select("""
        *,
        wallets (
            wallet_address,
            wallet_label,
            wallet_category,
            win_rate,
            total_pnl_usd
        )
    """)
        .eq("is_meme", True)
        .order("amount_usd", desc=True)
        .limit(limit)
        .execute()
    )

    return result.data or []


async def get_meme_wins_losses(period: str = "24h") -> dict:
    """Get biggest wins and losses in meme tokens."""
    from supabase import create_client

    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    # Calculate time range
    if period == "24h":
        time_range = datetime.now(UTC) - timedelta(hours=24)
    elif period == "7d":
        time_range = datetime.now(UTC) - timedelta(days=7)
    elif period == "30d":
        time_range = datetime.now(UTC) - timedelta(days=30)
    else:
        time_range = datetime.now(UTC) - timedelta(hours=24)

    # Get biggest wins
    wins = (
        supabase.table("whale_movements")
        .select("*")
        .gte("collected_at", time_range.isoformat())
        .eq("transaction_type", "sell")
        .order("amount_usd", desc=True)
        .limit(20)
        .execute()
    )

    # Get biggest losses
    losses = (
        supabase.table("whale_movements")
        .select("*")
        .gte("collected_at", time_range.isoformat())
        .eq("transaction_type", "buy")
        .order("amount_usd", desc=True)
        .limit(20)
        .execute()
    )

    return {
        "period": period,
        "wins": wins.data or [],
        "losses": losses.data or [],
    }


def classify_meme_category(symbol: str, name: str) -> str:
    """Classify meme token into category."""
    text = f"{symbol} {name}".lower()

    for category, keywords in MEME_CATEGORIES.items():
        if any(kw in text for kw in keywords):
            return category

    return "other"


# ── KOL Intelligence ──────────────────────────────────────────


async def get_kol_scorecard(kol_handle: str) -> dict | None:
    """Get KOL scorecard with track record."""
    from supabase import create_client

    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    # Get KOL profile
    result = supabase.table("kols").select("*").eq("twitter_handle", kol_handle).execute()

    if not result.data:
        return None

    kol = result.data[0]

    # Get their past calls
    calls = (
        supabase.table("kol_calls")
        .select("*")
        .eq("kol_id", kol["id"])
        .order("called_at", desc=True)
        .limit(50)
        .execute()
    )

    # Calculate stats
    total_calls = len(calls.data) if calls.data else 0
    winning_calls = len([c for c in (calls.data or []) if c.get("pnl_pct", 0) > 0])
    win_rate = (winning_calls / total_calls * 100) if total_calls > 0 else 0

    avg_pnl = (
        sum([c.get("pnl_pct", 0) for c in (calls.data or [])]) / total_calls
        if total_calls > 0
        else 0
    )

    return {
        "kol": kol,
        "stats": {
            "total_calls": total_calls,
            "winning_calls": winning_calls,
            "win_rate": round(win_rate, 2),
            "average_pnl": round(avg_pnl, 2),
            "follower_count": kol.get("follower_count"),
            "engagement_rate": kol.get("engagement_rate"),
        },
        "recent_calls": calls.data[:10] if calls.data else [],
    }


async def get_top_kols_by_category(category: str = "memes", limit: int = 20) -> list[dict]:
    """Get top KOLs by category with scorecards."""
    from supabase import create_client

    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    result = (
        supabase.table("kols")
        .select("*")
        .eq("primary_category", category)
        .order("win_rate", desc=True)
        .order("follower_count", desc=True)
        .limit(limit)
        .execute()
    )

    return result.data or []


# ── Social Monitoring ─────────────────────────────────────────


async def monitor_social_sentiment(token_address: str) -> dict:
    """Monitor social sentiment for a token."""
    # LunarCrush integration
    try:
        from app.lunarcrush_connector import get_lunarcrush_connector

        lc = get_lunarcrush_connector()

        sentiment = await lc.get_sentiment(token_address)

        return {
            "token": token_address,
            "social_volume": sentiment.get("social_volume"),
            "social_dominance": sentiment.get("social_dominance"),
            "sentiment_score": sentiment.get("sentiment_score"),
            "mentions_24h": sentiment.get("mentions_24h"),
            "mentions_change_24h": sentiment.get("mentions_change_24h"),
        }
    except (AttributeError, ImportError):
        return {"error": "LunarCrush not available"}


async def get_viral_crypto_posts(hours: int = 24) -> list[dict]:
    """Get viral crypto posts from X/Twitter."""
    from supabase import create_client

    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    time_range = datetime.now(UTC) - timedelta(hours=hours)

    result = (
        supabase.table("viral_posts")
        .select("*")
        .gte("posted_at", time_range.isoformat())
        .order("engagement_score", desc=True)
        .limit(50)
        .execute()
    )

    return result.data or []


# ── Hack/Drain Alerts ────────────────────────────────────────


async def get_recent_hacks_drains(hours: int = 24) -> list[dict]:
    """Get recent hacks and drains from monitoring."""
    from supabase import create_client

    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    time_range = datetime.now(UTC) - timedelta(hours=hours)

    result = (
        supabase.table("security_alerts")
        .select("*")
        .gte("detected_at", time_range.isoformat())
        .in_("alert_type", ["hack", "drain", "exploit", "rugpull"])
        .order("amount_usd", desc=True)
        .limit(50)
        .execute()
    )

    return result.data or []


async def format_hack_alert_for_social(hack_data: dict) -> dict:
    """Format hack alert for X/Telegram posting."""
    return {
        "x_post": f"""🚨 HACK ALERT 🚨

Protocol: {hack_data.get("protocol_name", "Unknown")}
Amount: ${hack_data.get("amount_usd", 0):,.0f}
Chain: {hack_data.get("chain", "Unknown")}
Type: {hack_data.get("attack_type", "Unknown")}

{hack_data.get("description", "")[:200]}

#CryptoSecurity #DeFi #HackAlert""",
        "telegram_post": f"""🚨 *HACK ALERT* 🚨

*Protocol:* {hack_data.get("protocol_name", "Unknown")}
*Amount:* ${hack_data.get("amount_usd", 0):,.0f}
*Chain:* {hack_data.get("chain", "Unknown")}
*Type:* {hack_data.get("attack_type", "Unknown")}

{hack_data.get("description", "")[:500]}

Stay safe out there! 🔒""",
        "severity": hack_data.get("severity", "medium"),
        "amount_usd": hack_data.get("amount_usd", 0),
    }


# ── Wallet Screenshot Generation ──────────────────────────────


async def generate_wallet_screenshot(wallet_address: str, pnl_data: dict) -> str:
    """Generate wallet PnL screenshot for sharing."""
    # This would use a graphics API (Alibaba, etc.) to generate images
    # For now, return placeholder

    return {
        "wallet": wallet_address,
        "total_pnl": pnl_data.get("total_pnl_usd", 0),
        "win_rate": pnl_data.get("win_rate", 0),
        "top_wins": pnl_data.get("top_wins", []),
        "top_losses": pnl_data.get("top_losses", []),
        "image_url": f"/api/v1/images/wallet/{wallet_address}/pnl-summary",
        "share_url": f"https://rugmunch.io/wallet/{wallet_address}",
    }


# ── Content Generation ────────────────────────────────────────


async def generate_marketing_content(content_type: str, data: dict) -> dict:
    """Generate marketing content using AI."""
    # This would call Alibaba's AI API for content generation

    templates = {
        "win_announcement": """
🎉 BIG WIN ALERT! 🎉

Wallet: {wallet_address[:8]}...{wallet_address[-6:]}
Token: {token_symbol}
Profit: ${pnl_usd:,.0f} ({pnl_pct:.1f}%)

This whale called it early and rode it all the way up! 🐋

Track smart money: https://rugmunch.io/wallet/{wallet_address}

#Crypto #MemeCoin #SmartMoney
""",
        "loss_announcement": """
💀 LOSS PORN 💀

Wallet: {wallet_address[:8]}...{wallet_address[-6:]}
Token: {token_symbol}
Loss: ${pnl_usd:,.0f} ({pnl_pct:.1f}%)

Oof. Another reminder to take profits! 📉

Learn from their mistakes: https://rugmunch.io/wallet/{wallet_address}

#Crypto #Trading #LossPorn
""",
        "kol_scorecard": """
📊 KOL SCORECARD: @{kol_handle}

Win Rate: {win_rate:.1f}%
Total Calls: {total_calls}
Avg PnL: {avg_pnl:.1f}%
Followers: {follower_count:,}

Track their calls: https://rugmunch.io/kol/{kol_handle}

#CryptoTwitter #KOL #Alpha
""",
    }

    template = templates.get(content_type, "")

    # Format template with data
    content = template.format(**data) if template else ""

    return {
        "content_type": content_type,
        "x_post": content[:280],
        "telegram_post": content,
        "generated_at": datetime.now(UTC).isoformat(),
    }
