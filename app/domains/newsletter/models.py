"""Newsletter ORM models.

Tables:
  newsletters          - Newsletter campaigns (content, metadata, scheduling)
  newsletter_subs      - Subscribers (email, wallet, tier, protocol, status)
  newsletter_payments  - Crypto subscription payments (tx hash, chain, amount)
  newsletter_deliveries - Per-subscriber delivery tracking (sent, opened, clicked)
  newsletter_crossposts - Cross-platform publication records (platform, URL, status)
"""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003 — SQLAlchemy Mapped[] needs runtime resolution
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base
from app.models.base import TimestampMixin


class Newsletter(Base, TimestampMixin):
    """A newsletter campaign - content created by a creator."""

    __tablename__ = "newsletters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    creator_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="Creator wallet address or user ID",
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    slug: Mapped[str] = mapped_column(String(256), nullable=True, unique=True)
    content_html: Mapped[str] = mapped_column(Text, nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_image: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)

    # Publishing & scheduling
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="draft",
        index=True,
        comment="draft | scheduled | sending | sent | failed",
    )
    publish_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Scheduled publish time; null = send immediately",
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Delivery options
    segment: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="all",
        comment="all | paid_only | free_only | by_interest",
    )
    subject_line: Mapped[str | None] = mapped_column(String(256), nullable=True)
    preheader_text: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Recurrence
    recurrence: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="null (one-off) | daily | weekly | monthly",
    )
    recurrence_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Stats
    total_recipients: Mapped[int] = mapped_column(Integer, default=0)
    total_opened: Mapped[int] = mapped_column(Integer, default=0)
    total_clicked: Mapped[int] = mapped_column(Integer, default=0)
    total_revenue: Mapped[float] = mapped_column(Float, default=0.0)

    # Ghost integration
    ghost_post_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_newsletter_creator_status", "creator_id", "status"),
        Index("ix_newsletter_scheduled", "publish_at"),
    )


class NewsletterSub(Base, TimestampMixin):
    """A newsletter subscriber."""

    __tablename__ = "newsletter_subs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    newsletter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("newsletters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    wallet_address: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Protocol identity (Nostr npub, Farcaster FID, Bluesky DID, Mastodon handle)
    protocol: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="email | nostr | farcaster | bluesky | mastodon | lens | other",
    )
    protocol_id: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        comment="npub, FID, DID, handle, etc.",
    )

    # Tier & status
    tier: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="free",
        comment="free | monthly | annual | founding",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        index=True,
        comment="active | inactive | cancelled | expired",
    )
    subscription_tx_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    subscription_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    token_gate_holdings: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Token-gated holdings snapshot {'token': '...', 'amount': '...'}",
    )

    # Metadata
    interests: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)
    referrer: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="signup | import | widget | api",
    )

    newsletter: Mapped[Newsletter] = relationship("Newsletter", backref="subscribers")

    __table_args__ = (
        Index("ix_newsletter_sub_email_newsletter", "email", "newsletter_id"),
        Index("ix_newsletter_sub_wallet", "wallet_address", "newsletter_id"),
    )


class NewsletterPayment(Base, TimestampMixin):
    """Crypto payment record for a newsletter subscription."""

    __tablename__ = "newsletter_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    subscriber_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("newsletter_subs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Payment details
    chain: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="eth | sol | base | polygon | lightning",
    )
    token: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="ETH | USDC | SOL | USDT | BTC (lightning)",
    )
    amount: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Amount as string (preserve precision)",
    )
    usd_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_hash: Mapped[str] = mapped_column(String(256), nullable=False, index=True, unique=True)
    from_address: Mapped[str] = mapped_column(String(128), nullable=False)
    block_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Block number of the transaction",
    )
    confirmations: Mapped[int] = mapped_column(Integer, default=0)

    # Subscription
    tier: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="free | monthly | annual | founding",
    )
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Revenue share
    platform_fee_pct: Mapped[float] = mapped_column(Float, default=10.0)
    creator_amount: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform_amount: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="pending | confirmed | failed | refunded",
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relations
    subscriber: Mapped[NewsletterSub] = relationship("NewsletterSub", backref="payments")

    __table_args__ = (
        Index("ix_newsletter_payment_status", "status"),
        Index("ix_newsletter_payment_sub_tier", "subscriber_id", "tier"),
    )


class NewsletterDelivery(Base, TimestampMixin):
    """Per-subscriber delivery record for a newsletter issue."""

    __tablename__ = "newsletter_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    newsletter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("newsletters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subscriber_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("newsletter_subs.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Delivery tracking
    channel: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="email | nostr_dm | farcaster | bluesky | mastodon",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="pending | sent | delivered | bounced | failed",
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    open_count: Mapped[int] = mapped_column(Integer, default=0)
    click_count: Mapped[int] = mapped_column(Integer, default=0)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Error tracking
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    newsletter: Mapped[Newsletter] = relationship("Newsletter", backref="deliveries")
    subscriber: Mapped[NewsletterSub] = relationship("NewsletterSub", backref="deliveries")

    __table_args__ = (Index("ix_newsletter_delivery_nl_subbed", "newsletter_id", "subscriber_id"),)


class NewsletterCrosspost(Base, TimestampMixin):
    """Record of a cross-platform publication."""

    __tablename__ = "newsletter_crossposts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    newsletter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("newsletters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Platform
    platform: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="ghost | mirror | paragraph | medium | substack | nostr | farcaster | bluesky | mastodon | email",
    )
    platform_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    platform_id: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        comment="Platform-specific ID (post ID, event ID, etc.)",
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="pending | publishing | published | failed",
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Analytics
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    engagement_count: Mapped[int] = mapped_column(Integer, default=0)

    newsletter: Mapped[Newsletter] = relationship("Newsletter", backref="crossposts")

    __table_args__ = (Index("ix_newsletter_crosspost_nl_platform", "newsletter_id", "platform"),)
