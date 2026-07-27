"""Creator achievements and streak service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.live import AchievementDef, CreatorStreak, UserAchievement

log = get_logger(__name__)


# ── Achievement Definitions ───────────────────────────────────────────────────

SEED_ACHIEVEMENTS: list[dict[str, Any]] = [
    {
        "key": "streak_7",
        "title": "Weekly Warrior",
        "description": "Streamed 7 days in a row",
        "category": "streak",
        "target_value": 7,
        "badge_key": "streak_7",
    },
    {
        "key": "streak_30",
        "title": "Monthly Champion",
        "description": "Streamed 30 days in a row",
        "category": "streak",
        "target_value": 30,
        "badge_key": "streak_30",
    },
    {
        "key": "live_time_100h",
        "title": "Century Streamer",
        "description": "100 hours total live time",
        "category": "live_time",
        "target_value": 100,
        "badge_key": "live_100h",
    },
    {
        "key": "live_time_500h",
        "title": "Marathon Streamer",
        "description": "500 hours total live time",
        "category": "live_time",
        "target_value": 500,
        "badge_key": "live_500h",
    },
    {
        "key": "viewers_1k",
        "title": "Rising Star",
        "description": "1,000 total viewers across all streams",
        "category": "viewers",
        "target_value": 1000,
        "badge_key": "viewers_1k",
    },
    {
        "key": "viewers_10k",
        "title": "Stream Sensation",
        "description": "10,000 total viewers across all streams",
        "category": "viewers",
        "target_value": 10_000,
        "badge_key": "viewers_10k",
    },
    {
        "key": "earnings_100k_sats",
        "title": "Earning Intern",
        "description": "100,000 sats earned from gifts",
        "category": "earnings",
        "target_value": 100_000,
        "badge_key": "earnings_100k",
    },
    {
        "key": "earnings_1m_sats",
        "title": "Satoshi Whales",
        "description": "1,000,000 sats earned from gifts",
        "category": "earnings",
        "target_value": 1_000_000,
        "badge_key": "earnings_1m",
    },
    {
        "key": "battle_wins_10",
        "title": "Battle Hardened",
        "description": "Won 10 battles",
        "category": "battles",
        "target_value": 10,
        "badge_key": "battle_10",
    },
    {
        "key": "battle_wins_50",
        "title": "Battle Champion",
        "description": "Won 50 battles",
        "category": "battles",
        "target_value": 50,
        "badge_key": "battle_50",
    },
]


async def seed_achievements(db: AsyncSession) -> int:
    """Seed achievement definitions if empty."""
    result = await db.execute(select(sa_func.count()).select_from(AchievementDef))
    count = result.scalar()
    if count and count > 0:
        return 0

    seeded = 0
    for a in SEED_ACHIEVEMENTS:
        db.add(AchievementDef(**a))
        seeded += 1
    await db.commit()
    log.info("achievements_seeded count=%d", seeded)
    return seeded


async def get_user_achievements(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Get all achievements with user's progress."""
    defs_stmt = select(AchievementDef).order_by(AchievementDef.category, AchievementDef.target_value)
    defs_result = await db.execute(defs_stmt)
    defs = defs_result.scalars().all()

    user_stmt = select(UserAchievement).where(UserAchievement.user_id == user_id)
    user_result = await db.execute(user_stmt)
    user_achs = {ua.achievement_key: ua for ua in user_result.scalars().all()}

    results = []
    for d in defs:
        ua = user_achs.get(d.key)
        results.append(
            {
                "key": d.key,
                "title": d.title,
                "description": d.description,
                "category": d.category,
                "target_value": d.target_value,
                "badge_key": d.badge_key,
                "progress": ua.progress if ua else 0,
                "unlocked": ua.unlocked if ua else False,
                "unlocked_at": ua.unlocked_at.isoformat() if ua and ua.unlocked_at else None,
            }
        )

    return results


async def record_progress(
    db: AsyncSession,
    user_id: uuid.UUID,
    category: str,
    amount: int = 1,
) -> list[dict[str, Any]]:
    """Record progress for all achievements in a category. Returns newly unlocked."""
    stmt = select(AchievementDef).where(AchievementDef.category == category)
    result = await db.execute(stmt)
    defs = result.scalars().all()

    newly_unlocked = []

    for d in defs:
        # Find or create user achievement tracker
        ua_stmt = select(UserAchievement).where(
            and_(
                UserAchievement.user_id == user_id,
                UserAchievement.achievement_key == d.key,
            ),
        )
        ua_result = await db.execute(ua_stmt)
        ua = ua_result.scalar_one_or_none()

        if ua and ua.unlocked:
            continue

        if ua is None:
            ua = UserAchievement(
                user_id=user_id,
                achievement_key=d.key,
                progress=0,
            )
            db.add(ua)

        ua.progress += amount

        if ua.progress >= d.target_value and not ua.unlocked:
            ua.unlocked = True
            ua.unlocked_at = datetime.now(UTC)
            newly_unlocked.append(
                {
                    "key": d.key,
                    "title": d.title,
                    "badge_key": d.badge_key,
                }
            )
            log.info("achievement_unlocked user=%s key=%s", user_id, d.key)

    if newly_unlocked:
        await db.commit()

    return newly_unlocked


# ── Creator Streaks ───────────────────────────────────────────────────────────


async def update_stream_streak(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Update a creator's streaming streak. Call when a stream starts."""
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    stmt = select(CreatorStreak).where(CreatorStreak.user_id == user_id)
    result = await db.execute(stmt)
    streak = result.scalar_one_or_none()

    if streak is None:
        streak = CreatorStreak(
            user_id=user_id,
            streak_type="stream",
            current_count=1,
            longest_count=1,
            last_activity_date=today,
        )
        db.add(streak)
        await db.commit()
        return {"current": 1, "longest": 1, "new": True}

    # Check if last activity was yesterday
    if streak.last_activity_date:
        yesterday = today - timedelta(days=1)
        last_date = streak.last_activity_date.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        if last_date == today:
            # Already counted today
            return {"current": streak.current_count, "longest": streak.longest_count, "new": False}

        if last_date == yesterday:
            # Consecutive day
            streak.current_count += 1
        else:
            # Streak broken
            streak.current_count = 1
    else:
        streak.current_count = 1

    streak.last_activity_date = today
    streak.longest_count = max(streak.longest_count, streak.current_count)
    await db.commit()

    return {
        "current": streak.current_count,
        "longest": streak.longest_count,
        "new": True,
        "streak_broken": streak.current_count == 1,
    }


async def get_creator_streak(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Get current streak info for a creator."""
    stmt = select(CreatorStreak).where(CreatorStreak.user_id == user_id)
    result = await db.execute(stmt)
    streak = result.scalar_one_or_none()
    if not streak:
        return None

    return {
        "current_count": streak.current_count,
        "longest_count": streak.longest_count,
        "last_activity_date": streak.last_activity_date.isoformat() if streak.last_activity_date else None,
        "streak_type": streak.streak_type,
    }
