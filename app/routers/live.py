"""Live Gamification API — battles, viewer XP, challenges, achievements, events.

Routes:
  Creator Battles:
    POST   /api/v1/live/battles/request         — challenge another creator
    POST   /api/v1/live/battles/accept           — accept a battle
    POST   /api/v1/live/battles/{id}/gift        — send gift during battle
    POST   /api/v1/live/battles/{id}/end         — end battle
    GET    /api/v1/live/battles/{id}             — get battle state + score
    GET    /api/v1/live/battles/history          — battle history
    GET    /api/v1/live/battles/record           — win/loss record
    GET    /api/v1/live/battles/active           — my active battle

  Viewer XP:
    GET    /api/v1/live/viewers/me/xp           — my XP and level
    GET    /api/v1/live/viewers/me/transactions  — my XP transactions
    POST   /api/v1/live/viewers/xp/award        — award XP (watch/chat/gift/share)
    GET    /api/v1/live/viewers/leaderboard      — XP leaderboard

  Daily/Weekly Challenges:
    GET    /api/v1/live/challenges               — active challenges
    GET    /api/v1/live/challenges/me            — my challenge progress

  Creator Achievements:
    GET    /api/v1/live/creator/achievements     — my achievements
    GET    /api/v1/live/creator/streak           — my streak

  Live Events:
    POST   /api/v1/live/events/create            — create event
    GET    /api/v1/live/events/upcoming          — upcoming events
    GET    /api/v1/live/events/live              — live events
    POST   /api/v1/live/events/{id}/start        — start event
    POST   /api/v1/live/events/{id}/complete     — complete event
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import get_current_user, require_auth
from app.core.dependencies import get_db
from app.core.logging import get_logger
from app.services.live.achievement_service import (
    get_creator_streak as get_streak_svc,
    get_user_achievements,
    seed_achievements,
)
from app.services.live.battle_service import (
    accept_battle,
    auto_expire_battles,
    create_battle_request,
    end_battle,
    get_battle_history,
    get_battle_score,
    get_live_battle_for_user,
    get_win_loss_record,
    record_gift,
)
from app.services.live.challenge_service import (
    get_active_challenges as get_active_challenges_svc,
    get_user_challenges,
    seed_challenges,
)
from app.services.live.event_service import (
    complete_event,
    create_event as create_event_svc,
    get_live_events,
    get_upcoming_events,
    start_event,
)
from app.services.live.xp_service import (
    award_chat_xp,
    award_gift_xp,
    award_share_xp,
    award_watch_xp,
    get_recent_xp_transactions,
    get_user_xp_info,
    get_xp_leaderboard,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/live", tags=["live"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────


# -- Battles --
class BattleRequest(BaseModel):
    opponent_id: str
    duration_seconds: int = Field(default=180, ge=60, le=600)


class BattleAccept(BaseModel):
    battle_id: str


class BattleGiftSend(BaseModel):
    battle_id: str
    recipient_id: str
    gift_type: str = "standard"
    value_cents: int = Field(default=100, ge=1)


class BattleHistoryParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


# -- XP --
class AwardXPRequest(BaseModel):
    source: str = Field(..., pattern=r"^(watch|chat|gift|share)$")
    amount: int = Field(default=1, ge=1, le=1000)
    reference_id: str | None = None


# -- Events --
class CreateEventRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1, max_length=5000)
    event_type: str = Field(..., pattern=r"^(ama|music|trading|interview|other)$")
    scheduled_at: str  # ISO datetime
    duration_minutes: int = Field(default=60, ge=15, le=480)
    hashtag: str | None = Field(default=None, max_length=64)
    thumbnail_url: str | None = None
    max_viewers: int | None = Field(default=None, ge=1, le=100000)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_user(user_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID") from None


async def _get_current_user_id(current_user: dict[str, Any] = Depends(require_auth)) -> str:
    """Extract user ID from auth claims."""
    uid = current_user.get("id") or current_user.get("sub")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid auth token")
    return str(uid)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1: CREATOR BATTLES
# ═════════════════════════════════════════════════════════════════════════════


@router.post("/battles/request", summary="Challenge another creator to a battle")
async def post_battle_request(
    body: BattleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Send a battle challenge to another creator."""
    challenger_id = uuid.UUID(await _get_current_user_id(current_user))
    opponent_id = await _get_user(body.opponent_id)

    if challenger_id == opponent_id:
        raise HTTPException(400, "Cannot battle yourself")

    battle = await create_battle_request(db, challenger_id, opponent_id, body.duration_seconds)
    return {"success": True, "battle_id": str(battle.id), "status": "pending"}


@router.post("/battles/accept", summary="Accept a battle challenge")
async def post_battle_accept(
    body: BattleAccept,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Accept a pending battle and start it."""
    accepter_id = uuid.UUID(await _get_current_user_id(current_user))
    battle_id = uuid.UUID(body.battle_id)

    battle = await accept_battle(db, battle_id, accepter_id)
    if not battle:
        raise HTTPException(404, "Battle not found or already started")

    return {
        "success": True,
        "battle_id": str(battle.id),
        "status": "live",
        "start_time": battle.start_time.isoformat() if battle.start_time else None,
        "timer_duration": battle.timer_duration,
    }


@router.post("/battles/{battle_id}/gift", summary="Send a gift during a battle")
async def post_battle_gift(
    battle_id: str,
    body: BattleGiftSend,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Send a gift that converts to battle points."""
    sender_id = uuid.UUID(await _get_current_user_id(current_user))
    b_id = uuid.UUID(battle_id)
    recipient_id = await _get_user(body.recipient_id)

    gift = await record_gift(db, b_id, sender_id, recipient_id, body.gift_type, body.value_cents)
    if not gift:
        raise HTTPException(400, "Battle is not live or invalid recipient")

    # Also award XP to the sender for gifting
    await award_gift_xp(db, sender_id, body.gift_type)

    return {
        "success": True,
        "points_awarded": gift.points,
        "recipient_id": body.recipient_id,
    }


@router.post("/battles/{battle_id}/end", summary="End a battle")
async def post_battle_end(
    battle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """End a battle and declare the winner."""
    user_id = uuid.UUID(await _get_current_user_id(current_user))
    b_id = uuid.UUID(battle_id)

    battle = await end_battle(db, b_id)
    if not battle:
        raise HTTPException(404, "Battle not found or not live")

    result = {
        "success": True,
        "battle_id": str(battle.id),
        "status": "completed",
        "points_1": battle.points_1,
        "points_2": battle.points_2,
        "winner_id": str(battle.winner_id) if battle.winner_id else None,
        "tied": battle.winner_id is None,
    }

    if battle.winner_id:
        result["winner_label"] = "You won!" if battle.winner_id == user_id else "Opponent won!"
    else:
        result["winner_label"] = "It's a tie!"

    return result


@router.get("/battles/{battle_id}", summary="Get battle state and real-time score")
async def get_battle(
    battle_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Get real-time battle scores and state."""
    b_id = uuid.UUID(battle_id)
    score = await get_battle_score(db, b_id)
    if not score:
        raise HTTPException(404, "Battle not found")
    return score


@router.get("/battles/history", summary="Get battle history")
async def get_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get current user's battle history."""
    user_id = uuid.UUID(await _get_current_user_id(current_user))
    history = await get_battle_history(db, user_id, limit, offset)
    return {"history": history, "count": len(history)}


@router.get("/battles/record", summary="Get win/loss record")
async def get_record(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get current user's battle win/loss record."""
    user_id = uuid.UUID(await _get_current_user_id(current_user))
    record = await get_win_loss_record(db, user_id)
    return record


@router.get("/battles/active", summary="Get my active battle")
async def get_active_battle(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get the current live battle for the user."""
    user_id = uuid.UUID(await _get_current_user_id(current_user))
    battle = await get_live_battle_for_user(db, user_id)
    if not battle:
        return {"active": False}
    score = await get_battle_score(db, battle.id)
    return {"active": True, "battle": score}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2: VIEWER XP & LEVELING
# ═════════════════════════════════════════════════════════════════════════════


@router.get("/viewers/me/xp", summary="Get my XP and level")
async def get_my_xp(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get current user's XP, level, and next level progress."""
    user_id = uuid.UUID(await _get_current_user_id(current_user))
    info = await get_user_xp_info(db, user_id)
    if not info:
        raise HTTPException(404, "User not found")
    return info


@router.get("/viewers/me/transactions", summary="Get my XP transactions")
async def get_my_xp_transactions(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get recent XP earning transactions."""
    user_id = uuid.UUID(await _get_current_user_id(current_user))
    txs = await get_recent_xp_transactions(db, user_id, limit)
    return {"transactions": txs, "count": len(txs)}


@router.post("/viewers/xp/award", summary="Award XP to a user")
async def post_award_xp(
    body: AwardXPRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Award XP for watching, chatting, gifting, or sharing."""
    user_id = uuid.UUID(await _get_current_user_id(current_user))

    award_map: dict[str, Any] = {
        "watch": award_watch_xp,
        "chat": award_chat_xp,
        "gift": award_gift_xp,
        "share": award_share_xp,
    }

    handler = award_map[body.source]
    result = await handler(db, user_id, body.reference_id or body.source)

    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Failed to award XP"))

    # Also check for challenge progress
    challenge_type_map = {
        "watch": "watch_time",
        "chat": "chat_streams",
        "gift": "send_gifts",
        "share": "share_stream",
    }
    from app.services.live.challenge_service import record_progress as record_challenge_progress

    completed = await record_challenge_progress(
        db,
        user_id,
        challenge_type_map[body.source],
        amount=body.amount,
    )

    return {
        **result,
        "challenges_completed": [c["title"] for c in completed],
    }


@router.get("/viewers/leaderboard", summary="Viewer XP leaderboard")
async def get_viewer_leaderboard(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get the global XP leaderboard."""
    leaderboard = await get_xp_leaderboard(db, limit, offset)
    return {"leaderboard": leaderboard, "count": len(leaderboard)}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3: DAILY/WEEKLY CHALLENGES
# ═════════════════════════════════════════════════════════════════════════════


@router.get("/challenges", summary="Get active challenges")
async def get_challenges(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Get all currently active challenges."""
    await seed_challenges(db)
    challenges = await get_active_challenges_svc(db)
    return {"challenges": challenges, "count": len(challenges)}


@router.get("/challenges/me", summary="Get my challenge progress")
async def get_my_challenges(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get active challenges with the current user's progress."""
    user_id = uuid.UUID(await _get_current_user_id(current_user))
    await seed_challenges(db)
    challenges = await get_user_challenges(db, user_id)
    return {"challenges": challenges, "count": len(challenges)}


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4: CREATOR ACHIEVEMENTS & STREAKS
# ═════════════════════════════════════════════════════════════════════════════


@router.get("/creator/achievements", summary="Get creator achievements")
async def get_achievements(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get all achievements with the current creator's progress."""
    user_id = uuid.UUID(await _get_current_user_id(current_user))
    await seed_achievements(db)
    achievements = await get_user_achievements(db, user_id)
    return {"achievements": achievements, "count": len(achievements)}


@router.get("/creator/streak", summary="Get creator streak")
async def get_streak(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get the current creator's streaming streak."""
    user_id = uuid.UUID(await _get_current_user_id(current_user))
    streak = await get_streak_svc(db, user_id)
    if streak is None:
        return {"current_count": 0, "longest_count": 0, "streak_type": "stream"}
    return streak


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5: LIVE EVENTS
# ═════════════════════════════════════════════════════════════════════════════


@router.post("/events/create", summary="Create a scheduled live event")
async def post_create_event(
    body: CreateEventRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new scheduled live event."""
    creator_id = uuid.UUID(await _get_current_user_id(current_user))

    try:
        scheduled_at = datetime.fromisoformat(body.scheduled_at)
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid scheduled_at format. Use ISO 8601.") from None

    if scheduled_at <= datetime.now(UTC):
        raise HTTPException(400, "Scheduled time must be in the future")

    event = await create_event_svc(
        db=db,
        creator_id=creator_id,
        title=body.title,
        description=body.description,
        event_type=body.event_type,
        scheduled_at=scheduled_at,
        duration_minutes=body.duration_minutes,
        hashtag=body.hashtag,
        thumbnail_url=body.thumbnail_url,
        max_viewers=body.max_viewers,
    )

    return {
        "success": True,
        "event_id": str(event.id),
        "hashtag": event.hashtag or f"#{event.event_type}_{event.id.hex[:8]}",
    }


@router.get("/events/upcoming", summary="Get upcoming events")
async def get_events_upcoming(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get all upcoming scheduled events."""
    events = await get_upcoming_events(db, limit, offset, event_type)
    return {"events": events, "count": len(events)}


@router.get("/events/live", summary="Get currently live events")
async def get_events_live(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get all currently live events."""
    events = await get_live_events(db)
    return {"events": events, "count": len(events)}


@router.post("/events/{event_id}/start", summary="Start a scheduled event")
async def post_start_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Mark a scheduled event as live."""
    creator_id = uuid.UUID(await _get_current_user_id(current_user))
    e_id = uuid.UUID(event_id)

    result = await start_event(db, e_id, creator_id)
    if not result:
        raise HTTPException(404, "Event not found or already started")
    return {"success": True, **result}


@router.post("/events/{event_id}/complete", summary="Complete an event")
async def post_complete_event(
    event_id: str,
    total_viewers: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Mark an event as completed."""
    e_id = uuid.UUID(event_id)
    result = await complete_event(db, e_id, total_viewers)
    if not result:
        raise HTTPException(404, "Event not found")
    return {"success": True, **result}


# ═════════════════════════════════════════════════════════════════════════════
# HEALTH / MAINTENANCE
# ═════════════════════════════════════════════════════════════════════════════


@router.post("/maintenance/expire-battles", summary="Auto-expire timed-out battles")
async def post_expire_battles(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Auto-expire battles past their timer duration."""
    expired = await auto_expire_battles(db)
    return {"expired": expired}
