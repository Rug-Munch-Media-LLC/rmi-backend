"""Newsletter Pydantic v2 request/response schemas.

Every schema uses strict=False where fields may be absent in legacy data,
consistent with app.domains.news.router patterns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# NOTE: ``datetime`` is imported at runtime (not TYPE_CHECKING-only) because
# Pydantic v2 must resolve the annotation when building JSON/OpenAPI schemas
# for models with ``publish_at``/``created_at`` fields (model_json_schema()
# raises PydanticUserError otherwise).

# ── Newsletter (Campaign) ───────────────────────────────────────────


class NewsletterCreate(BaseModel):
    """Request body for creating/sending a newsletter."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=512, description="Newsletter title")
    content_html: str = Field(..., min_length=1, description="HTML body")
    content_text: str | None = Field(None, description="Plain text fallback")
    excerpt: str | None = Field(None, max_length=500, description="Short preview")
    cover_image: str | None = Field(None, max_length=1024, description="Cover image URL")
    tags: list[str] | None = Field(None, description="Content tags")
    subject_line: str | None = Field(None, max_length=256, description="Email subject")
    preheader_text: str | None = Field(None, max_length=256, description="Email preheader")
    segment: str = Field("all", description="Audience segment: all | paid_only | free_only | by_interest")
    publish_at: datetime | None = Field(None, description="Scheduled publish time; null = immediate")
    recurrence: str | None = Field(None, description="daily | weekly | monthly | null = one-off")


class NewsletterUpdate(BaseModel):
    """Request body for updating a newsletter draft."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    content_html: str | None = None
    content_text: str | None = None
    excerpt: str | None = None
    cover_image: str | None = None
    tags: list[str] | None = None
    subject_line: str | None = None
    preheader_text: str | None = None
    segment: str | None = None
    publish_at: datetime | None = None
    recurrence: str | None = None


class NewsletterOut(BaseModel):
    """Response body for a newsletter."""

    model_config = ConfigDict(strict=False)

    id: str
    creator_id: str
    title: str
    slug: str | None = None
    content_html: str
    content_text: str | None = None
    excerpt: str | None = None
    cover_image: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: str = "draft"
    publish_at: datetime | None = None
    sent_at: datetime | None = None
    segment: str = "all"
    subject_line: str | None = None
    preheader_text: str | None = None
    recurrence: str | None = None
    total_recipients: int = 0
    total_opened: int = 0
    total_clicked: int = 0
    total_revenue: float = 0.0
    ghost_post_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NewsletterListResponse(BaseModel):
    """Paginated newsletter list."""

    items: list[NewsletterOut]
    total: int
    offset: int
    limit: int


# ── Subscription ─────────────────────────────────────────────────────


class SubscribeRequest(BaseModel):
    """Request body for subscribing to a newsletter."""

    model_config = ConfigDict(extra="forbid")

    newsletter_id: str = Field(..., description="Newsletter ID to subscribe to")
    email: str | None = Field(None, max_length=320, description="Email address")
    wallet_address: str | None = Field(None, max_length=128, description="Crypto wallet address")
    protocol: str | None = Field(None, description="nostr | farcaster | bluesky | mastodon | lens")
    protocol_id: str | None = Field(None, description="Decentralized identity (npub, FID, DID, handle)")
    tier: str = Field("free", description="free | monthly | annual | founding")
    interests: list[str] | None = None
    referrer: str | None = None


class CryptoSubscribeRequest(SubscribeRequest):
    """Request body for subscribing with a crypto payment."""

    model_config = ConfigDict(extra="forbid")

    chain: str = Field(..., description="eth | sol | base | polygon | lightning")
    token: str = Field(..., description="ETH | USDC | SOL | USDT | BTC")
    tx_hash: str = Field(..., min_length=10, description="On-chain transaction hash")
    from_address: str = Field(..., min_length=10, description="Sender wallet address")


class SubscriberOut(BaseModel):
    """Response body for a subscriber."""

    model_config = ConfigDict(strict=False)

    id: str
    newsletter_id: str
    email: str | None = None
    wallet_address: str | None = None
    protocol: str | None = None
    protocol_id: str | None = None
    tier: str = "free"
    status: str = "active"
    subscription_tx_hash: str | None = None
    subscription_expires_at: datetime | None = None
    token_gate_holdings: dict[str, Any] | None = None
    interests: list[str] = Field(default_factory=list)
    source: str | None = None
    created_at: datetime | None = None


class SubscriberListResponse(BaseModel):
    """Paginated subscriber list."""

    items: list[SubscriberOut]
    total: int
    offset: int
    limit: int


# ── Payment ──────────────────────────────────────────────────────────


class PaymentOut(BaseModel):
    """Response body for a crypto payment record."""

    model_config = ConfigDict(strict=False)

    id: str
    subscriber_id: str
    chain: str
    token: str
    amount: str
    usd_value: float | None = None
    tx_hash: str
    from_address: str
    tier: str
    duration_days: int
    expires_at: datetime
    status: str = "pending"
    created_at: datetime | None = None


# ── Send / Schedule ─────────────────────────────────────────────────


class SendRequest(BaseModel):
    """Request body for sending a newsletter."""

    model_config = ConfigDict(extra="forbid")

    newsletter_id: str = Field(..., description="ID of the newsletter to send")
    test_email: str | None = Field(None, description="Send test to a single email first")


class ScheduleRequest(BaseModel):
    """Request body for scheduling a newsletter."""

    model_config = ConfigDict(extra="forbid")

    newsletter_id: str
    publish_at: datetime = Field(..., description="ISO 8601 scheduled time")
    recurrence: str | None = None


class DeliveryStatusOut(BaseModel):
    """Delivery stats for a newsletter."""

    model_config = ConfigDict(strict=False)

    newsletter_id: str
    total_sent: int = 0
    total_delivered: int = 0
    total_opened: int = 0
    total_clicked: int = 0
    total_bounced: int = 0
    total_failed: int = 0
    open_rate: float = 0.0
    click_rate: float = 0.0
    delivery_rate: float = 0.0


# ── Cross-platform Publish ──────────────────────────────────────────


class PublishRequest(BaseModel):
    """Request body for cross-platform publishing."""

    model_config = ConfigDict(extra="forbid")

    newsletter_id: str
    platforms: list[str] | None = Field(
        None,
        description="Platform list; null = all enabled platforms",
    )
    platforms_extra: dict[str, Any] | None = Field(
        None,
        description="Platform-specific overrides (e.g. ghost_tags, medium_canonical)",
    )


class CrosspostOut(BaseModel):
    """Response body for a cross-platform publication record."""

    model_config = ConfigDict(strict=False)

    id: str
    newsletter_id: str
    platform: str
    platform_url: str | None = None
    platform_id: str | None = None
    status: str = "pending"
    error_message: str | None = None
    published_at: datetime | None = None


class PublishResponse(BaseModel):
    """Response from cross-platform publish."""

    results: list[CrosspostOut]
    total: int
    succeeded: int
    failed: int


# ── Import / Export ─────────────────────────────────────────────────


class SubscriberImportRow(BaseModel):
    """A single row in a subscriber import."""

    model_config = ConfigDict(extra="forbid")

    email: str | None = None
    wallet_address: str | None = None
    protocol: str | None = None
    protocol_id: str | None = None
    tier: str = "free"
    interests: list[str] | None = None


class SubscriberImportRequest(BaseModel):
    """Request body for importing subscribers."""

    model_config = ConfigDict(extra="forbid")

    newsletter_id: str
    subscribers: list[SubscriberImportRow] = Field(..., min_length=1, max_length=10000)
    source: str = "import"


class SubscriberImportResponse(BaseModel):
    """Response from subscriber import."""

    imported: int
    skipped: int
    errors: list[dict[str, str]] = Field(default_factory=list)


# ── Analytics ───────────────────────────────────────────────────────


class NewsletterAnalytics(BaseModel):
    """Aggregate analytics for a newsletter or creator."""

    model_config = ConfigDict(strict=False)

    total_newsletters: int = 0
    total_subscribers: int = 0
    paid_subscribers: int = 0
    total_revenue: float = 0.0
    platform_fees: float = 0.0
    creator_earnings: float = 0.0
    avg_open_rate: float = 0.0
    avg_click_rate: float = 0.0
    subscriber_growth_7d: int = 0
    subscriber_growth_30d: int = 0
    best_performing_title: str | None = None
    best_performing_open_rate: float | None = None


# ── Generic ─────────────────────────────────────────────────────────


class ActionResponse(BaseModel):
    """Generic action response."""

    success: bool = True
    message: str = ""
    data: dict[str, Any] | None = None
