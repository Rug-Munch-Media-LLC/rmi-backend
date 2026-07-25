"""Live gamification ORM models — battles, viewer XP, challenges, achievements, events."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin


# ── Creator Battles ───────────────────────────────────────────────────────────


class LiveBattle(Base, TimestampMixin):
    """A TikTok-style live battle between two creators."""

    __tablename__ = "live_battles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    creator1_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    creator2_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
    )  # pending | accepted | live | completed | cancelled
    timer_duration: Mapped[int] = mapped_column(
        Integer,
        default=180,
        nullable=False,
    )  # seconds: 180, 300, 600
    points_1: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    points_2: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    start_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    winner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )


class BattleGift(Base, TimestampMixin):
    """A gift sent during a battle — each gift = points for the recipient."""

    __tablename__ = "battle_gifts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    battle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("live_battles.id"),
        nullable=False,
        index=True,
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    gift_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# ── Viewer XP & Leveling ──────────────────────────────────────────────────────


class ViewerXPTransaction(Base, TimestampMixin):
    """Individual XP-earning event for a viewer."""

    __tablename__ = "viewer_xp_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )  # watch | chat | gift | share | challenge | battle_bonus
    reference_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    metadata_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )


# ── Daily/Weekly Challenges ───────────────────────────────────────────────────


class Challenge(Base, TimestampMixin):
    """A predefined challenge template (daily/weekly)."""

    __tablename__ = "challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    challenge_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )  # send_gifts | watch_time | chat_streams | share_stream
    requirement_value: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )  # e.g. 5 gifts, 30 minutes, 3 streams
    reward_xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reward_badge: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    reward_gift_discount_pct: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    reset_frequency: Mapped[str] = mapped_column(
        String(16),
        default="daily",
        nullable=False,
    )  # daily | weekly
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    emoji: Mapped[str | None] = mapped_column(String(8), nullable=True)


class UserChallenge(Base, TimestampMixin):
    """Tracks a user's progress on a specific challenge instance."""

    __tablename__ = "user_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("challenges.id"),
        nullable=False,
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("user_id", "challenge_id", "period_start", name="uq_user_challenge_period"),)


# ── Creator Achievements & Streaks ────────────────────────────────────────────


class AchievementDef(Base, TimestampMixin):
    """Definition of an achievable milestone."""

    __tablename__ = "achievement_defs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    key: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )  # streak | live_time | viewers | earnings | battles
    target_value: Mapped[int] = mapped_column(Integer, nullable=False)
    badge_key: Mapped[str | None] = mapped_column(String(64), nullable=True)


class UserAchievement(Base, TimestampMixin):
    """Tracks progress and unlock state for a user on an achievement."""

    __tablename__ = "user_achievements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    achievement_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unlocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unlocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (UniqueConstraint("user_id", "achievement_key", name="uq_user_achievement"),)


class CreatorStreak(Base, TimestampMixin):
    """Tracks creator streaming streaks."""

    __tablename__ = "creator_streaks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    streak_type: Mapped[str] = mapped_column(
        String(32),
        default="stream",
        nullable=False,
    )
    current_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    longest_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_activity_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


# ── Live Events ───────────────────────────────────────────────────────────────


class LiveEvent(Base, TimestampMixin):
    """Scheduled live event like AMA, performance, trading session."""

    __tablename__ = "live_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )  # ama | music | trading | interview | other
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default="scheduled",
        nullable=False,
    )  # scheduled | live | completed | cancelled
    hashtag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    max_viewers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_viewers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
