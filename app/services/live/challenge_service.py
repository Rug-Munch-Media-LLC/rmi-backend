"""Daily/weekly challenge service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.live import Challenge, UserChallenge
from app.services.live.xp_service import award_xp

log = get_logger(__name__)


def _period_bounds(frequency: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Get start/end of the current period."""
    now = now or datetime.now(UTC)
    if frequency == "weekly":
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
    else:  # daily
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    return start, end


# ── Seeded Challenges ─────────────────────────────────────────────────────────

SEED_CHALLENGES: list[dict[str, Any]] = [
    {
        "title": "Gift Giver",
        "description": "Send 5 gifts to creators during live streams",
        "challenge_type": "send_gifts",
        "requirement_value": 5,
        "reward_xp": 150,
        "reward_badge": None,
        "reset_frequency": "daily",
        "emoji": "🎁",
    },
    {
        "title": "Super Gifter",
        "description": "Send 20 gifts to creators during live streams",
        "challenge_type": "send_gifts",
        "requirement_value": 20,
        "reward_xp": 500,
        "reward_badge": "super_gifter",
        "reset_frequency": "weekly",
        "emoji": "💎",
    },
    {
        "title": "Loyal Viewer",
        "description": "Watch 30 minutes of live streams today",
        "challenge_type": "watch_time",
        "requirement_value": 30,
        "reward_xp": 100,
        "reward_badge": None,
        "reset_frequency": "daily",
        "emoji": "👁️",
    },
    {
        "title": "Stream Binger",
        "description": "Watch 3 hours of live streams this week",
        "challenge_type": "watch_time",
        "requirement_value": 180,
        "reward_xp": 400,
        "reward_badge": "stream_binger",
        "reset_frequency": "weekly",
        "emoji": "🍿",
    },
    {
        "title": "Social Butterfly",
        "description": "Chat in 3 different streams",
        "challenge_type": "chat_streams",
        "requirement_value": 3,
        "reward_xp": 100,
        "reward_badge": None,
        "reset_frequency": "daily",
        "emoji": "🦋",
    },
    {
        "title": "Networker",
        "description": "Chat in 10 different streams this week",
        "challenge_type": "chat_streams",
        "requirement_value": 10,
        "reward_xp": 350,
        "reward_badge": "networker",
        "reset_frequency": "weekly",
        "emoji": "🌐",
    },
    {
        "title": "Stream Ambassador",
        "description": "Share a live stream with friends",
        "challenge_type": "share_stream",
        "requirement_value": 1,
        "reward_xp": 50,
        "reward_badge": None,
        "reset_frequency": "daily",
        "emoji": "📢",
    },
    {
        "title": "Viral Promoter",
        "description": "Share 5 live streams this week",
        "challenge_type": "share_stream",
        "requirement_value": 5,
        "reward_xp": 200,
        "reward_badge": "viral_promoter",
        "reset_frequency": "weekly",
        "emoji": "🔥",
    },
]


async def seed_challenges(db: AsyncSession) -> int:
    """Seed challenges if table is empty. Returns count of seeded rows."""
    result = await db.execute(select(sa_func.count()).select_from(Challenge))
    count = result.scalar()
    if count and count > 0:
        return 0

    seeded = 0
    for c in SEED_CHALLENGES:
        challenge = Challenge(**c)
        db.add(challenge)
        seeded += 1
    await db.commit()
    log.info("challenges_seeded count=%d", seeded)
    return seeded


async def get_active_challenges(
    db: AsyncSession,
) -> list[dict[str, Any]]:
    """Get all active challenges with their current period info."""
    stmt = select(Challenge).where(Challenge.active.is_(True))
    result = await db.execute(stmt)
    challenges = result.scalars().all()

    return [
        {
            "id": str(c.id),
            "title": c.title,
            "description": c.description,
            "challenge_type": c.challenge_type,
            "requirement_value": c.requirement_value,
            "reward_xp": c.reward_xp,
            "reward_badge": c.reward_badge,
            "reset_frequency": c.reset_frequency,
            "emoji": c.emoji,
        }
        for c in challenges
    ]


async def get_user_challenges(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Get active challenges with user's progress for the current period."""
    challenges = await get_active_challenges(db)
    now = datetime.now(UTC)
    results = []

    for c in challenges:
        period_start, period_end = _period_bounds(c["reset_frequency"], now)

        # Find existing progress
        stmt = select(UserChallenge).where(
            and_(
                UserChallenge.user_id == user_id,
                UserChallenge.challenge_id == uuid.UUID(c["id"]),
                UserChallenge.period_start == period_start,
            ),
        )
        result = await db.execute(stmt)
        uc = result.scalar_one_or_none()

        progress = uc.progress if uc else 0
        completed = uc.completed if uc else False

        results.append(
            {
                **c,
                "progress": progress,
                "completed": completed,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            }
        )

    return results


async def record_progress(
    db: AsyncSession,
    user_id: uuid.UUID,
    challenge_type: str,
    amount: int = 1,
) -> list[dict[str, Any]]:
    """Record progress for all matching challenges. Returns completed challenges."""
    now = datetime.now(UTC)
    stmt = select(Challenge).where(
        and_(
            Challenge.active.is_(True),
            Challenge.challenge_type == challenge_type,
        ),
    )
    result = await db.execute(stmt)
    challenges = result.scalars().all()

    completed_list = []

    for challenge in challenges:
        period_start, period_end = _period_bounds(challenge.reset_frequency, now)

        # Find or create UserChallenge
        uc_stmt = select(UserChallenge).where(
            and_(
                UserChallenge.user_id == user_id,
                UserChallenge.challenge_id == challenge.id,
                UserChallenge.period_start == period_start,
            ),
        )
        uc_result = await db.execute(uc_stmt)
        uc = uc_result.scalar_one_or_none()

        if uc and uc.completed:
            continue

        if uc is None:
            uc = UserChallenge(
                user_id=user_id,
                challenge_id=challenge.id,
                progress=0,
                period_start=period_start,
                period_end=period_end,
            )
            db.add(uc)

        uc.progress += amount

        if uc.progress >= challenge.requirement_value and not uc.completed:
            uc.completed = True
            uc.completed_at = now

            # Award XP reward
            if challenge.reward_xp > 0:
                await award_xp(
                    db,
                    user_id,
                    challenge.reward_xp,
                    source="challenge",
                    reference_id=str(challenge.id),
                )

            completed_list.append(
                {
                    "id": str(challenge.id),
                    "title": challenge.title,
                    "reward_xp": challenge.reward_xp,
                    "reward_badge": challenge.reward_badge,
                }
            )

            log.info(
                "challenge_completed user=%s challenge=%s type=%s",
                user_id,
                challenge.id,
                challenge_type,
            )

    await db.commit()
    return completed_list
