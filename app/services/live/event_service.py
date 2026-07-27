"""Live event scheduling and management service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.live import LiveEvent

log = get_logger(__name__)


async def create_event(
    db: AsyncSession,
    creator_id: uuid.UUID,
    title: str,
    description: str,
    event_type: str,
    scheduled_at: datetime,
    duration_minutes: int = 60,
    hashtag: str | None = None,
    thumbnail_url: str | None = None,
    max_viewers: int | None = None,
) -> LiveEvent:
    """Create a scheduled live event."""
    event = LiveEvent(
        creator_id=creator_id,
        title=title,
        description=description,
        event_type=event_type,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        hashtag=hashtag,
        thumbnail_url=thumbnail_url,
        max_viewers=max_viewers,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    log.info("event_created id=%s type=%s creator=%s", event.id, event_type, creator_id)
    return event


async def get_upcoming_events(
    db: AsyncSession,
    limit: int = 20,
    offset: int = 0,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Get upcoming scheduled events."""
    now = datetime.now(UTC)
    conditions = [
        LiveEvent.status.in_(["scheduled", "live"]),
        LiveEvent.scheduled_at >= now,
    ]
    if event_type:
        conditions.append(LiveEvent.event_type == event_type)

    stmt = select(LiveEvent).where(and_(*conditions)).order_by(LiveEvent.scheduled_at.asc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    events = result.scalars().all()

    return [
        {
            "id": str(e.id),
            "creator_id": str(e.creator_id),
            "title": e.title,
            "description": e.description,
            "event_type": e.event_type,
            "scheduled_at": e.scheduled_at.isoformat(),
            "duration_minutes": e.duration_minutes,
            "status": e.status,
            "hashtag": e.hashtag,
            "thumbnail_url": e.thumbnail_url,
            "max_viewers": e.max_viewers,
            "total_viewers": e.total_viewers,
            "countdown_seconds": max(0, int((e.scheduled_at - now).total_seconds())),
        }
        for e in events
    ]


async def get_live_events(
    db: AsyncSession,
) -> list[dict[str, Any]]:
    """Get currently live events."""
    stmt = select(LiveEvent).where(LiveEvent.status == "live").order_by(LiveEvent.scheduled_at.asc())
    result = await db.execute(stmt)
    events = result.scalars().all()

    return [
        {
            "id": str(e.id),
            "creator_id": str(e.creator_id),
            "title": e.title,
            "event_type": e.event_type,
            "scheduled_at": e.scheduled_at.isoformat(),
            "duration_minutes": e.duration_minutes,
            "status": e.status,
            "hashtag": e.hashtag,
        }
        for e in events
    ]


async def start_event(
    db: AsyncSession,
    event_id: uuid.UUID,
    creator_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Mark a scheduled event as live."""
    event = await db.get(LiveEvent, event_id)
    if not event or event.status != "scheduled":
        return None
    if event.creator_id != creator_id:
        return None

    event.status = "live"
    await db.commit()
    await db.refresh(event)
    log.info("event_started id=%s", event.id)

    return {
        "id": str(event.id),
        "title": event.title,
        "status": "live",
        "hashtag": event.hashtag,
    }


async def complete_event(
    db: AsyncSession,
    event_id: uuid.UUID,
    total_viewers: int | None = None,
) -> dict[str, Any] | None:
    """Mark an event as completed."""
    event = await db.get(LiveEvent, event_id)
    if not event or event.status not in ("scheduled", "live"):
        return None

    event.status = "completed"
    if total_viewers is not None:
        event.total_viewers = total_viewers
    await db.commit()

    return {"id": str(event.id), "title": event.title, "status": "completed"}
