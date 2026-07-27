"""Viewer XP & leveling service."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.live import ViewerXPTransaction
from app.models.user import User

log = get_logger(__name__)

# ── XP constants ──────────────────────────────────────────────────────────────
XP_WATCH_PER_MINUTE = 1
XP_CHAT_PER_MESSAGE = 5
XP_GIFT_PER_GIFT = 10
XP_SHARE_PER_SHARE = 15
XP_CHALLENGE_BONUS = 50


# ── Level thresholds (Level 1-100) ──────────────────────────────────────────
# Exponential-ish curve: Level N requires ~ N * (N+1) * 25 XP cumulatively
def xp_for_level(level: int) -> int:
    """Return cumulative XP required to reach a given level."""
    if level <= 1:
        return 0
    # Formula: 25 * level * (level + 1) / 2   →  Level 10 = 1375, Level 50 = 31875, Level 100 = 126250
    return int(25 * level * (level + 1) / 2)


def level_from_xp(total_xp: int) -> int:
    """Derive level from cumulative XP."""
    level = 1
    while xp_for_level(level + 1) <= total_xp:
        level += 1
        if level >= 100:
            break
    return level


LEVEL_UNLOCKS: dict[int, str] = {
    10: "custom_chat_colors",
    25: "custom_emojis",
    50: "gift_discount",
    75: "exclusive_badges",
    100: "creator_recognition",
}


# ── XP Earning ────────────────────────────────────────────────────────────────


async def award_xp(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: int,
    source: str,
    reference_id: str | None = None,
    metadata_json: str | None = None,
) -> dict[str, Any]:
    """Award XP to a user and check for level-up."""
    user = await db.get(User, user_id)
    if not user:
        return {"success": False, "error": "User not found"}

    tx = ViewerXPTransaction(
        user_id=user_id,
        amount=amount,
        source=source,
        reference_id=reference_id,
        metadata_json=metadata_json,
    )
    db.add(tx)

    old_level = user.level
    user.xp = (user.xp or 0) + amount
    new_level = level_from_xp(user.xp)
    user.level = new_level

    await db.commit()

    result = {
        "success": True,
        "xp_awarded": amount,
        "total_xp": user.xp,
        "old_level": old_level,
        "new_level": new_level,
        "leveled_up": new_level > old_level,
        "source": source,
    }

    if new_level > old_level:
        unlocks = [v for k, v in LEVEL_UNLOCKS.items() if old_level < k <= new_level]
        result["new_unlocks"] = unlocks
        log.info("level_up user=%s level=%d->%d unlocks=%s", user_id, old_level, new_level, unlocks)

    return result


async def award_watch_xp(
    db: AsyncSession,
    user_id: uuid.UUID,
    minutes: int = 1,
) -> dict[str, Any]:
    """Award XP for watching a stream."""
    return await award_xp(
        db,
        user_id,
        amount=XP_WATCH_PER_MINUTE * minutes,
        source="watch",
        reference_id=f"watch_{minutes}m",
    )


async def award_chat_xp(
    db: AsyncSession,
    user_id: uuid.UUID,
    stream_id: str | None = None,
) -> dict[str, Any]:
    """Award XP for chatting."""
    return await award_xp(
        db,
        user_id,
        amount=XP_CHAT_PER_MESSAGE,
        source="chat",
        reference_id=stream_id,
    )


async def award_gift_xp(
    db: AsyncSession,
    user_id: uuid.UUID,
    gift_type: str = "standard",
) -> dict[str, Any]:
    """Award XP for sending a gift."""
    return await award_xp(
        db,
        user_id,
        amount=XP_GIFT_PER_GIFT,
        source="gift",
        reference_id=gift_type,
    )


async def award_share_xp(
    db: AsyncSession,
    user_id: uuid.UUID,
    platform: str = "twitter",
) -> dict[str, Any]:
    """Award XP for sharing a stream."""
    return await award_xp(
        db,
        user_id,
        amount=XP_SHARE_PER_SHARE,
        source="share",
        reference_id=platform,
    )


async def award_challenge_xp(
    db: AsyncSession,
    user_id: uuid.UUID,
    challenge_id: str,
) -> dict[str, Any]:
    """Award XP for completing a challenge."""
    return await award_xp(
        db,
        user_id,
        amount=XP_CHALLENGE_BONUS,
        source="challenge",
        reference_id=challenge_id,
    )


async def get_user_xp_info(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Get XP and level info for a user."""
    user = await db.get(User, user_id)
    if not user:
        return None

    current_level = user.level or 1
    xp_current = user.xp or 0
    xp_for_current = xp_for_level(current_level)
    xp_for_next = xp_for_level(current_level + 1)
    xp_in_level = xp_current - xp_for_current
    xp_needed = xp_for_next - xp_for_current

    unlocks = {str(k): v for k, v in LEVEL_UNLOCKS.items() if current_level >= k}

    return {
        "user_id": str(user_id),
        "xp": xp_current,
        "level": current_level,
        "xp_for_current_level": xp_for_current,
        "xp_for_next_level": xp_for_next,
        "xp_in_level": xp_in_level,
        "xp_needed_for_next": xp_needed,
        "progress_pct": round((xp_in_level / xp_needed * 100), 1) if xp_needed > 0 else 100,
        "unlocks": unlocks,
    }


async def get_xp_leaderboard(
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Get viewer XP leaderboard."""
    stmt = select(User).order_by(User.xp.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    users = result.scalars().all()

    leaderboard = []
    for i, u in enumerate(users):
        leaderboard.append(
            {
                "rank": offset + i + 1,
                "user_id": str(u.id),
                "username": getattr(u, "username", None) or str(u.id)[:8],
                "xp": u.xp or 0,
                "level": u.level or 1,
            }
        )
    return leaderboard


async def get_recent_xp_transactions(
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Get recent XP transactions."""
    stmt = (
        select(ViewerXPTransaction)
        .where(ViewerXPTransaction.user_id == user_id)
        .order_by(ViewerXPTransaction.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    txs = result.scalars().all()
    return [
        {
            "id": str(t.id),
            "amount": t.amount,
            "source": t.source,
            "reference_id": t.reference_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in txs
    ]
