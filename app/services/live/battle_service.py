"""Battle service — TikTok-style creator battles with real-time scoring."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.live import BattleGift, LiveBattle

log = get_logger(__name__)

BATTLE_DURATIONS = {180: "3m", 300: "5m", 600: "10m"}


async def create_battle_request(
    db: AsyncSession,
    challenger_id: uuid.UUID,
    opponent_id: uuid.UUID,
    duration_seconds: int = 180,
) -> LiveBattle:
    """Create a new battle request (pending)."""
    if duration_seconds not in BATTLE_DURATIONS:
        duration_seconds = 180

    battle = LiveBattle(
        creator1_id=challenger_id,
        creator2_id=opponent_id,
        status="pending",
        timer_duration=duration_seconds,
    )
    db.add(battle)
    await db.commit()
    await db.refresh(battle)
    log.info("battle_created id=%s c1=%s c2=%s", battle.id, challenger_id, opponent_id)
    return battle


async def accept_battle(
    db: AsyncSession,
    battle_id: uuid.UUID,
    accepter_id: uuid.UUID,
) -> LiveBattle | None:
    """Accept a battle request and start it."""
    battle = await db.get(LiveBattle, battle_id)
    if not battle:
        return None
    if battle.status != "pending":
        return None
    if battle.creator2_id != accepter_id:
        return None

    battle.status = "live"
    battle.start_time = datetime.now(UTC)
    await db.commit()
    await db.refresh(battle)
    log.info("battle_started id=%s", battle.id)
    return battle


async def end_battle(
    db: AsyncSession,
    battle_id: uuid.UUID,
) -> LiveBattle | None:
    """End a battle and determine the winner."""
    battle = await db.get(LiveBattle, battle_id)
    if not battle or battle.status != "live":
        return None

    battle.status = "completed"
    battle.end_time = datetime.now(UTC)

    if battle.points_1 > battle.points_2:
        battle.winner_id = battle.creator1_id
    elif battle.points_2 > battle.points_1:
        battle.winner_id = battle.creator2_id
    # else: tie — no winner

    await db.commit()
    await db.refresh(battle)

    if battle.winner_id:
        log.info("battle_won id=%s winner=%s", battle.id, battle.winner_id)
    else:
        log.info("battle_tied id=%s", battle.id)

    return battle


async def record_gift(
    db: AsyncSession,
    battle_id: uuid.UUID,
    sender_id: uuid.UUID,
    recipient_id: uuid.UUID,
    gift_type: str,
    value_cents: int,
) -> BattleGift | None:
    """Record a gift sent during a battle. Gift value = battle points."""
    battle = await db.get(LiveBattle, battle_id)
    if not battle or battle.status != "live":
        return None

    # 1 cent = 1 point
    points = max(1, value_cents)

    gift = BattleGift(
        battle_id=battle_id,
        sender_id=sender_id,
        recipient_id=recipient_id,
        gift_type=gift_type,
        value_cents=value_cents,
        points=points,
    )
    db.add(gift)

    # Add points to the recipient's battle score
    if recipient_id == battle.creator1_id:
        battle.points_1 += points
    elif recipient_id == battle.creator2_id:
        battle.points_2 += points
    else:
        await db.rollback()
        return None

    await db.commit()
    await db.refresh(gift)
    return gift


async def get_battle_score(
    db: AsyncSession,
    battle_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Get real-time battle scores."""
    battle = await db.get(LiveBattle, battle_id)
    if not battle:
        return None

    time_left = 0
    if battle.start_time and battle.status == "live":
        elapsed = (datetime.now(UTC) - battle.start_time).total_seconds()
        time_left = max(0, battle.timer_duration - int(elapsed))

    return {
        "id": str(battle.id),
        "creator1_id": str(battle.creator1_id),
        "creator2_id": str(battle.creator2_id),
        "points_1": battle.points_1,
        "points_2": battle.points_2,
        "status": battle.status,
        "timer_duration": battle.timer_duration,
        "time_left_seconds": time_left,
        "start_time": battle.start_time.isoformat() if battle.start_time else None,
        "end_time": battle.end_time.isoformat() if battle.end_time else None,
        "winner_id": str(battle.winner_id) if battle.winner_id else None,
    }


async def get_battle_history(
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Get battle history for a user."""
    stmt = (
        select(LiveBattle)
        .where(
            and_(
                LiveBattle.status == "completed",
                (LiveBattle.creator1_id == user_id) | (LiveBattle.creator2_id == user_id),
            ),
        )
        .order_by(LiveBattle.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    battles = result.scalars().all()

    history = []
    for b in battles:
        won = b.winner_id == user_id if b.winner_id else None
        history.append(
            {
                "id": str(b.id),
                "opponent_id": str(b.creator2_id if b.creator1_id == user_id else b.creator1_id),
                "my_points": b.points_1 if b.creator1_id == user_id else b.points_2,
                "opponent_points": b.points_2 if b.creator1_id == user_id else b.points_1,
                "won": won,
                "tied": b.winner_id is None,
                "duration_seconds": b.timer_duration,
                "ended_at": b.end_time.isoformat() if b.end_time else None,
            }
        )
    return history


async def get_win_loss_record(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict[str, int]:
    """Get win/loss record for a user."""
    stmt = select(LiveBattle).where(
        and_(
            LiveBattle.status == "completed",
            (LiveBattle.creator1_id == user_id) | (LiveBattle.creator2_id == user_id),
        ),
    )
    result = await db.execute(stmt)
    battles = result.scalars().all()

    wins = sum(1 for b in battles if b.winner_id == user_id)
    losses = sum(1 for b in battles if b.winner_id is not None and b.winner_id != user_id)
    ties = sum(1 for b in battles if b.winner_id is None)

    return {"wins": wins, "losses": losses, "ties": ties, "total": len(battles)}


async def get_live_battle_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> LiveBattle | None:
    """Get the current live battle for a user, if any."""
    stmt = (
        select(LiveBattle)
        .where(
            and_(
                LiveBattle.status == "live",
                (LiveBattle.creator1_id == user_id) | (LiveBattle.creator2_id == user_id),
            ),
        )
        .order_by(LiveBattle.start_time.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def auto_expire_battles(db: AsyncSession) -> int:
    """Auto-expire battles past their timer duration. Returns count expired."""
    stmt = select(LiveBattle).where(LiveBattle.status == "live")
    result = await db.execute(stmt)
    live_battles = result.scalars().all()
    now = datetime.now(UTC)
    expired = 0

    for battle in live_battles:
        if battle.start_time:
            elapsed = (now - battle.start_time).total_seconds()
            if elapsed >= battle.timer_duration:
                battle.status = "completed"
                battle.end_time = now

                if battle.points_1 > battle.points_2:
                    battle.winner_id = battle.creator1_id
                elif battle.points_2 > battle.points_1:
                    battle.winner_id = battle.creator2_id
                expired += 1

    if expired:
        await db.commit()
        log.info("battles_auto_expired count=%d", expired)

    return expired
