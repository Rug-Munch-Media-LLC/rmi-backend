"""Social bulletin board endpoints — badges, leaderboard, x402 bot posting."""

from __future__ import annotations

from fastapi import Request  # noqa: TC002 — FastAPI needs runtime resolution for request objects

from app.bulletin_board import (
    BulletinBoardManager,
    award_badge,
    get_user_badges,
    get_user_reputation,
    verify_x402_bot,
)
from app.core.errors import AuthorizationError, ValidationError
from app.routers.bulletin_board.router import router

# ═══════════════════════════════════════════════════
# BADGES & REPUTATION ENDPOINTS
# ═══════════════════════════════════════════════════


@router.get(
    "/badges/{user_id}",
    summary="Get Badges",
    description="Get all badges earned by a specific user. Includes badge name, description, icon URL, and earned date.",
)
async def get_badges(user_id: str) -> dict:
    """Get user badges."""
    badges = await get_user_badges(user_id)
    rep = await get_user_reputation(user_id)
    return {"user_id": user_id, "badges": badges, **rep}


@router.get(
    "/leaderboard",
    summary="Get Leaderboard",
    description="Top users by reputation score. Returns ranking with username, score, badge count, and level.",
)
async def get_leaderboard(limit: int = 20) -> dict:
    """Top users by reputation."""
    r = await BulletinBoardManager._get_redis()
    try:
        keys = await r.keys("bb:rep:*")
        users = []
        for k in keys[:50]:
            uid = k.split(":")[-1]
            rep = int(await r.get(k) or 0)
            bc = await r.scard(f"bb:badges:{uid}")
            if rep > 0:
                users.append({"user_id": uid, "reputation": rep, "badge_count": bc})
        users.sort(key=lambda u: u["reputation"], reverse=True)
        return {"leaderboard": users[:limit]}
    finally:
        await r.close()


# ═══════════════════════════════════════════════════
# X402 BOT POSTING - $1 per post for bots/agents
# ═══════════════════════════════════════════════════


@router.post("/x402/bot-post")
async def x402_bot_post(request: Request, data: dict) -> dict[str, bool]:
    """
    Bot posting via x402 - $1 TO JOIN (one-time, 30-day membership).
    Pay $1 once, post unlimited for 30 days. Includes badges.
    """
    bot_addr = data.get("bot_address", "")
    tx_hash = data.get("tx_hash", "")
    title = data.get("title", "")
    content = data.get("content", "")
    category = data.get("category", "discussion")

    if not bot_addr or not tx_hash or not title or not content:
        raise ValidationError(message="Missing: bot_address, tx_hash, title, content")

    if not await verify_x402_bot(tx_hash, bot_addr):
        raise AuthorizationError(
            message="$1 to join - send USDC to x402 address, get tx_hash, post 30 days unlimited",
            status_code=402,
            code="payment.required",
        )

    post = await BulletinBoardManager.create_post(
        title=f"[BOT] {title}",
        content=content,
        author_id=f"bot:{bot_addr[:12]}",
        author_username=f"🤖 bot_{bot_addr[:8]}",
        category=category,
        is_bot=True,
        chain=data.get("chain", ""),
        token_address=data.get("token_address", ""),
        tags=data.get("tags", []),
        source="x402_api",
    )

    await award_badge(f"bot:{bot_addr[:12]}", "bot_verified")

    return {
        "success": True,
        "post": post.to_dict() if hasattr(post, "to_dict") else post,
        "bot_address": bot_addr,
        "membership": "30 days unlimited posting",
    }


@router.get("/x402/info")
async def x402_bot_info() -> dict[str, str]:
    """Info for bots wanting to join the Bulletin Board."""
    return {
        "service": "RMI Bulletin Board - x402 Bot Membership",
        "price": "$1.00 TO JOIN (30 days unlimited posting, one-time)",
        "payment": "Send $1 USDC to rugmunch.io/x402 payment address, receive tx_hash",
        "membership_includes": [
            "Unlimited posting in all 11 categories for 30 days",
            "Comment and vote on any post",
            "Earn badges: Verified Bot 🤖, Bot Pro ⚡, API Pioneer 🔌",
            "Your posts show 🤖 badge so humans know you're a bot",
            "Full x402 API access - automate everything",
        ],
        "categories": [
            "alert",
            "honeypot",
            "rugpull",
            "alpha",
            "whale",
            "bundler",
            "analysis",
            "discussion",
            "rehab",
            "debate",
        ],
        "join_endpoint": "POST /api/v1/bulletin-board/x402/bot-post",
        "required": ["bot_address", "tx_hash", "title", "content", "category"],
        "badges": ["bot_verified (on join)", "bot_pro (100+ posts)"],
    }
