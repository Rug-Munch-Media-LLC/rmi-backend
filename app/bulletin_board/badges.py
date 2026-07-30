"""Badges, x402 bot payments, and RAG ingestion for bulletin board."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .manager import BulletinBoardManager

if TYPE_CHECKING:
    from .models import Post

BADGES = {
    "first_post": {"name": "First Words", "icon": "💬", "desc": "Made your first post", "tier": "bronze"},
    "10_posts": {"name": "Chatterbox", "icon": "📢", "desc": "10 posts", "tier": "bronze"},
    "50_posts": {"name": "Board Regular", "icon": "🎙️", "desc": "50 posts", "tier": "silver"},
    "100_posts": {"name": "Terminally Online", "icon": "🖥️", "desc": "100 posts - touch grass", "tier": "gold"},
    "10_upvotes": {"name": "Approved", "icon": "👍", "desc": "10 upvotes on a post", "tier": "bronze"},
    "50_upvotes": {"name": "Crowd Favorite", "icon": "⭐", "desc": "50 upvotes", "tier": "silver"},
    "100_upvotes": {"name": "Legendary", "icon": "👑", "desc": "100 upvotes", "tier": "gold"},
    "rug_reporter": {"name": "Rug Detective", "icon": "🔍", "desc": "Reported 3 verified rugs", "tier": "silver"},
    "scam_buster": {"name": "Scam Buster", "icon": "🛡️", "desc": "10 scam alerts verified", "tier": "gold"},
    "honeypot_hunter": {"name": "Honeypot Hunter", "icon": "🍯", "desc": "Found 5 honeypots", "tier": "silver"},
    "whale_watcher": {"name": "Whale Watcher", "icon": "🐋", "desc": "Tracked 10 whale moves", "tier": "silver"},
    "alpha_caller": {"name": "Alpha Caller", "icon": "📈", "desc": "Called 5 pumps", "tier": "gold"},
    "degen": {"name": "Certified Degen", "icon": "🎰", "desc": "Posted in every category", "tier": "gold"},
    "rug_survivor": {"name": "Rug Survivor", "icon": "💀", "desc": "Posted about getting rugged", "tier": "bronze"},
    "diamond_hands": {"name": "Diamond Hands", "icon": "💎", "desc": "Held through -90%", "tier": "diamond"},
    "based": {"name": "Based", "icon": "🧠", "desc": "Called a 10x before it happened", "tier": "diamond"},
    "bot_verified": {"name": "Verified Bot", "icon": "🤖", "desc": "Registered x402 bot", "tier": "silver"},
    "bot_pro": {"name": "Bot Pro", "icon": "⚡", "desc": "100+ API calls", "tier": "gold"},
}
BADGE_TIERS = {"bronze": "#CD7F32", "silver": "#C0C0C0", "gold": "#FFD700", "diamond": "#B9F2FF"}
X402_BB_POST_PRICE = "$1.00"


async def get_user_badges(user_id: str) -> list:
    try:
        r = await BulletinBoardManager._get_redis()
        ids = await r.smembers(f"bb:badges:{user_id}")
        await r.close()
        return [{"id": bid, **BADGES[bid]} for bid in ids if bid in BADGES]
    except (ConnectionError, OSError, TimeoutError):
        return []


async def award_badge(user_id: str, badge_id: str) -> bool:
    if badge_id not in BADGES:
        return False
    try:
        r = await BulletinBoardManager._get_redis()
        ok = await r.sadd(f"bb:badges:{user_id}", badge_id)
        await r.close()
        return ok > 0
    except (ConnectionError, OSError, TimeoutError):
        return False


async def get_user_reputation(user_id: str) -> dict:
    try:
        r = await BulletinBoardManager._get_redis()
        p = r.pipeline()
        p.get(f"bb:rep:{user_id}")
        p.scard(f"bb:badges:{user_id}")
        p.get(f"bb:posts:{user_id}")
        rep, bc, pc = await p.execute()
        await r.close()
        return {"reputation": int(rep or 0), "badge_count": bc or 0, "post_count": int(pc or 0)}
    except (ConnectionError, OSError):
        return {"reputation": 0, "badge_count": 0, "post_count": 0}


async def verify_x402_bot(tx_hash: str, bot_addr: str) -> bool:
    try:
        r = await BulletinBoardManager._get_redis()
        if await r.get(f"bb:x402:tx:{tx_hash}"):
            await r.close()
            return False
        await r.setex(f"bb:x402:tx:{tx_hash}", 86400, bot_addr)
        await r.setex(f"bb:x402:bot:{bot_addr}", 2592000, "1")
        await r.close()
        return True
    except (ConnectionError, OSError, TimeoutError):
        return False


async def ingest_post_to_rag(post: Post) -> dict:
    """Ingest a published bulletin board post into the RAG research collection."""
    try:
        from app.rag.ingestion import ingest_research_post as _ingest_research

        report = await _ingest_research(
            post_id=post.post_id,
            title=post.title,
            content=post.content,
            category=post.category,
            tags=post.tags,
            author=post.author_name,
        )
        return {
            "success": report.status in ("completed", "partial"),
            "status": report.status,
            "chunks": report.chunks_total,
        }
    except Exception as exc:
        return {"success": False, "status": "failed", "error": str(exc)[:300]}
