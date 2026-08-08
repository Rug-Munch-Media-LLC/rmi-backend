"""Creator Analytics ORM models.

Postgres tables for content metadata, view events, engagement events,
tips, cross-protocol metrics, and audience analytics.

All analytics models use UUID PKs, TimestampMixin, and JSON for flexible
metadata.  The heavy time-series aggregation lives in the analytics service
(in-memory + optional ClickHouse); these tables store the raw event records
for auditability and accurate counting.
"""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003 — SQLAlchemy Mapped[] needs runtime resolution
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base
from app.models.base import TimestampMixin


class CreatorProfile(Base, TimestampMixin):
    """A creator's profile across all protocols."""

    __tablename__ = "creator_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wallet_address: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    handle: Mapped[str] = mapped_column(String(128), default="", index=True)
    bio: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(String(2048), default="")
    email: Mapped[str] = mapped_column(String(256), default="")

    # Linked protocol handles: {"nostr": "npub...", "farcaster": "0x...", "bluesky": "...", "mastodon": "..."}
    protocol_handles: Mapped[dict[str, Any] | None] = mapped_column(
        "protocol_handles", type_=String(4096), nullable=True
    )
    # Creator interests/topics
    interests: Mapped[dict[str, Any] | None] = mapped_column("interests", type_=String(2048), nullable=True)
    total_followers: Mapped[int] = mapped_column(Integer, default=0)
    total_posts: Mapped[int] = mapped_column(Integer, default=0)
    total_views: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    posts: Mapped[list[ContentPost]] = relationship("ContentPost", back_populates="creator", lazy="selectin")

    __table_args__ = (
        Index("ix_creator_wallet", "wallet_address"),
        Index("ix_creator_handle", "handle"),
    )


class ContentPost(Base, TimestampMixin):
    """A piece of content published across one or more protocols."""

    __tablename__ = "content_posts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("creator_profiles.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), default="", comment="ID on the source platform")

    # Content metadata
    title: Mapped[str] = mapped_column(String(512), default="")
    content_type: Mapped[str] = mapped_column(
        String(32),
        default="micro",
        comment="micro, article, video, image, thread, audio, newsletter",
    )
    content_length: Mapped[int] = mapped_column(
        Integer, default=0, comment="Character count or video duration in seconds"
    )
    content_hash: Mapped[str] = mapped_column(String(64), default="", comment="SHA-256 of content for dedup")
    tags: Mapped[str] = mapped_column(String(2048), default="", comment="Comma-separated tags")

    # Cross-published protocols
    published_protocols: Mapped[str] = mapped_column(
        String(256),
        default="",
        comment="Comma-separated: nostr,farcaster,bluesky,mastodon",
    )

    # Media
    has_images: Mapped[bool] = mapped_column(Boolean, default=False)
    has_video: Mapped[bool] = mapped_column(Boolean, default=False)
    video_duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    has_link: Mapped[bool] = mapped_column(Boolean, default=False)

    # Flags
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Protocol-specific IDs
    nostr_id: Mapped[str] = mapped_column(String(256), default="")
    farcaster_hash: Mapped[str] = mapped_column(String(256), default="")
    bluesky_rkey: Mapped[str] = mapped_column(String(256), default="")
    mastodon_id: Mapped[str] = mapped_column(String(256), default="")

    # Relationships
    creator: Mapped[CreatorProfile] = relationship("CreatorProfile", back_populates="posts")
    views: Mapped[list[ViewEvent]] = relationship("ViewEvent", back_populates="post", lazy="selectin")
    engagements: Mapped[list[EngagementEvent]] = relationship("EngagementEvent", back_populates="post", lazy="selectin")

    __table_args__ = (
        Index("ix_content_creator", "creator_id"),
        Index("ix_content_type", "content_type"),
        Index("ix_content_published", "published_at"),
        Index("ix_content_creator_published", "creator_id", "published_at"),
    )


class ViewEvent(Base, TimestampMixin):
    """A single view event — the foundation of TRUE view counts.

    Each row represents one unique viewing session.  Deduplication happens
    at ingestion via viewer_fingerprint + post_id + session_id.
    """

    __tablename__ = "view_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("content_posts.id"), nullable=False)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("creator_profiles.id"), nullable=False)

    # Viewer identity (hashed for privacy)
    viewer_fingerprint: Mapped[str] = mapped_column(
        String(128),
        default="",
        index=True,
        comment="Hashed unique viewer identifier (wallet/IP/fingerprint)",
    )
    viewer_wallet: Mapped[str] = mapped_column(String(256), default="", comment="Wallet address if authenticated")
    viewer_ip_hash: Mapped[str] = mapped_column(String(64), default="", comment="Hashed IP for unique counting")

    # Session tracking
    session_id: Mapped[str] = mapped_column(String(128), default="", index=True, comment="Client-generated session ID")

    # Watch time (seconds)
    watch_time_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    total_content_duration: Mapped[float] = mapped_column(
        Float, default=0.0, comment="Total duration of content in seconds"
    )

    # Drop-off tracking
    dropped_at_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="Where viewer stopped (seconds)"
    )
    drop_off_point_pct: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Percentage through content where viewer dropped",
    )
    viewed_to_end: Mapped[bool] = mapped_column(Boolean, default=False)

    # Scroll-by tracking
    scrolled_past: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Scrolled past without meaningful watch"
    )
    scroll_depth_pct: Mapped[float] = mapped_column(Float, default=0.0, comment="How far they scrolled (0-100)")

    # Protocol
    protocol: Mapped[str] = mapped_column(
        String(32),
        default="nostr",
        index=True,
        comment="nostr, farcaster, bluesky, mastodon, direct",
    )

    # Live tracking
    is_live_view: Mapped[bool] = mapped_column(Boolean, default=False)
    is_replay: Mapped[bool] = mapped_column(Boolean, default=False, comment="Replay of a recorded/live stream")
    peak_concurrent: Mapped[int] = mapped_column(
        Integer, default=0, comment="Peak concurrent during this session (live)"
    )

    # Referrer
    referrer: Mapped[str] = mapped_column(String(1024), default="")

    # Relationships
    post: Mapped[ContentPost] = relationship("ContentPost", back_populates="views")

    __table_args__ = (
        Index("ix_view_post", "post_id"),
        Index("ix_view_creator", "creator_id"),
        Index("ix_view_creator_protocol", "creator_id", "protocol"),
        Index("ix_view_fingerprint_post", "viewer_fingerprint", "post_id", unique=False),
        Index("ix_view_created_at", "created_at"),
        UniqueConstraint("session_id", "viewer_fingerprint", "post_id", name="uq_view_session"),
    )


class EngagementEvent(Base, TimestampMixin):
    """A single engagement action on content.

    Each row is one action.  Duplicate detection at ingestion.
    """

    __tablename__ = "engagement_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("content_posts.id"), nullable=False)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("creator_profiles.id"), nullable=False)
    viewer_fingerprint: Mapped[str] = mapped_column(String(128), default="", index=True)
    viewer_wallet: Mapped[str] = mapped_column(String(256), default="")

    # Engagement type
    engagement_type: Mapped[str] = mapped_column(
        String(32),
        default="like",
        index=True,
        comment="like, comment, repost, tip, save, bookmark, click, follow",
    )

    # Protocol
    protocol: Mapped[str] = mapped_column(
        String(32),
        default="nostr",
        index=True,
        comment="nostr, farcaster, bluesky, mastodon, direct",
    )

    # Tip-specific fields
    tip_amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tip_token: Mapped[str] = mapped_column(String(32), default="")
    tip_amount_token: Mapped[float] = mapped_column(Float, default=0.0)

    # Comment text (with sentiment)
    comment_text: Mapped[str] = mapped_column(Text, default="")
    comment_sentiment: Mapped[str] = mapped_column(
        String(16),
        default="neutral",
        comment="positive, negative, neutral, mixed",
    )
    comment_sentiment_score: Mapped[float] = mapped_column(Float, default=0.0, comment="-1.0 to 1.0")

    # Click tracking
    clicked_url: Mapped[str] = mapped_column(String(2048), default="")
    click_position: Mapped[str] = mapped_column(String(32), default="")

    # Follow tracking
    followed_after: Mapped[bool] = mapped_column(Boolean, default=False)

    # Dedup key
    external_engagement_id: Mapped[str] = mapped_column(
        String(256), default="", comment="ID on source protocol for dedup"
    )

    # Relationships
    post: Mapped[ContentPost] = relationship("ContentPost", back_populates="engagements")

    __table_args__ = (
        Index("ix_engagement_post", "post_id"),
        Index("ix_engagement_creator", "creator_id"),
        Index("ix_engagement_type", "engagement_type"),
        Index("ix_engagement_creator_type", "creator_id", "engagement_type"),
        Index("ix_engagement_protocol", "protocol"),
    )


class AudienceFollower(Base, TimestampMixin):
    """Tracks followers and their metadata for audience analytics."""

    __tablename__ = "audience_followers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("creator_profiles.id"), nullable=False)

    # Follower identity
    follower_fingerprint: Mapped[str] = mapped_column(String(128), default="", index=True)
    follower_wallet: Mapped[str] = mapped_column(String(256), default="")
    follower_handle: Mapped[str] = mapped_column(String(128), default="")

    # Protocol they follow from
    protocol: Mapped[str] = mapped_column(
        String(32),
        default="nostr",
        comment="nostr, farcaster, bluesky, mastodon",
    )

    # Source attribution
    source_post_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=True, comment="Post that drove the follow")
    source_protocol: Mapped[str] = mapped_column(String(32), default="")

    # Churn tracking
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    unfollowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Demographics (populated over time)
    interests: Mapped[str] = mapped_column(String(2048), default="", comment="Comma-separated inferred interests")
    chains_active: Mapped[str] = mapped_column(String(512), default="", comment="Comma-separated chains they use")

    # Engagement score (0-100)
    engagement_score: Mapped[float] = mapped_column(Float, default=0.0)
    total_engagement_actions: Mapped[int] = mapped_column(Integer, default=0)
    total_tips_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_views: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_follower_creator", "creator_id"),
        Index("ix_follower_active", "creator_id", "is_active"),
        Index("ix_follower_protocol", "creator_id", "protocol"),
        UniqueConstraint("creator_id", "follower_fingerprint", name="uq_follower_creator"),
    )
