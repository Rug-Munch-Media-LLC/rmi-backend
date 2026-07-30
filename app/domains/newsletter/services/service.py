"""NewsletterService — business logic for the newsletter backend.

Every method follows the BaseService pattern:
  - Async with typed signatures
  - Structured logging using get_logger()
  - Consistent error handling via AppError hierarchy
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_service import BaseService
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domains.newsletter.models import (
    Newsletter,
    NewsletterCrosspost,
    NewsletterDelivery,
    NewsletterPayment,
    NewsletterSub,
)
from app.domains.newsletter.publisher import publish_to_all
from app.domains.newsletter.schemas import (
    CryptoSubscribeRequest,
    NewsletterCreate,
    NewsletterUpdate,
    PublishResponse,
    SubscriberImportRow,
)
from app.domains.newsletter.services.constants import PLATFORM_FEE_PCT, SUBSCRIPTION_TIERS
from app.domains.newsletter.services.helpers import get_setting, slugify
from app.domains.newsletter.services.scheduling import (
    compute_creator_analytics,
    compute_delivery_status,
    compute_newsletter_info_analytics,
)
from app.domains.newsletter.services.sending import (
    get_email_provider,
    send_email,
    verify_crypto_payment,
)
from app.domains.newsletter.services.subscribers import (
    export_subscribers_csv,
    get_segment_subscribers,
    import_subscribers_batch,
    list_subscribers,
)

logger = get_logger(__name__)


class NewsletterService(BaseService[Newsletter]):
    """Newsletter business logic."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @classmethod
    def _slugify(cls, text: str) -> str:
        """Slugify a string for URLs and filenames."""
        from app.domains.newsletter.services.helpers import slugify as _slugify_fn
        return _slugify_fn(text)

    # ── Newsletter CRUD ─────────────────────────────────────────────

    async def create_newsletter(
        self,
        creator_id: str,
        data: NewsletterCreate,
    ) -> Newsletter:
        """Create a new newsletter campaign.

        If publish_at is set, it's scheduled. If not, it stays as draft.
        """
        slug = slugify(data.title)
        # Check slug uniqueness
        existing = await self.db.execute(
            select(Newsletter).where(Newsletter.slug == slug),
        )
        if existing.scalar_one_or_none():
            slug = f"{slug}-{uuid.uuid4().hex[:8]}"

        nl = Newsletter(
            creator_id=creator_id,
            title=data.title,
            slug=slug,
            content_html=data.content_html,
            content_text=data.content_text,
            excerpt=data.excerpt,
            cover_image=data.cover_image,
            tags=data.tags,
            subject_line=data.subject_line,
            preheader_text=data.preheader_text,
            segment=data.segment,
            status="scheduled" if data.publish_at else "draft",
            publish_at=data.publish_at,
            recurrence=data.recurrence,
        )
        self.db.add(nl)
        await self.db.flush()

        logger.info(
            "newsletter_created",
            newsletter_id=str(nl.id),
            creator_id=creator_id,
            status=nl.status,
        )
        return nl

    async def get_newsletter(self, newsletter_id: str) -> Newsletter:
        """Get a newsletter by ID."""
        try:
            uid = uuid.UUID(newsletter_id)
        except ValueError:
            raise NotFoundError(f"Invalid newsletter ID: {newsletter_id}")

        nl = await self.db.get(Newsletter, uid)
        if not nl:
            raise NotFoundError(f"Newsletter not found: {newsletter_id}")
        return nl

    async def update_newsletter(
        self,
        newsletter_id: str,
        data: NewsletterUpdate,
    ) -> Newsletter:
        """Update a newsletter (draft or scheduled)."""
        nl = await self.get_newsletter(newsletter_id)

        if nl.status not in ("draft", "scheduled"):
            raise ValidationError("Only draft or scheduled newsletters can be updated")

        update_data = data.model_dump(exclude_unset=True, exclude_none=True)
        for field, value in update_data.items():
            setattr(nl, field, value)

        await self.db.flush()
        logger.info("newsletter_updated", newsletter_id=newsletter_id)
        return nl

    async def list_newsletters(
        self,
        creator_id: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> tuple[list[Newsletter], int]:
        """List newsletters with optional filters."""
        query = select(Newsletter).order_by(Newsletter.created_at.desc())

        if creator_id:
            query = query.where(Newsletter.creator_id == creator_id)
        if status:
            query = query.where(Newsletter.status == status)

        count_query = select(sa_func.count()).select_from(query.subquery())
        total = await self.db.scalar(count_query) or 0

        query = query.offset(offset).limit(limit)
        result = await self.db.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def delete_newsletter(self, newsletter_id: str) -> bool:
        """Delete a newsletter campaign."""
        nl = await self.get_newsletter(newsletter_id)
        await self.db.delete(nl)
        await self.db.flush()
        logger.info("newsletter_deleted", newsletter_id=newsletter_id)
        return True

    # ── Auto-Send Engine ────────────────────────────────────────────

    async def send_newsletter(
        self,
        newsletter_id: str,
        test_email: str | None = None,
    ) -> dict[str, Any]:
        """Send a newsletter to its target segment.

        1. Load the newsletter
        2. Resolve subscriber list (segmented)
        3. For email: send via SMTP (Postmark/SES/Mailgun)
        4. Record deliveries
        5. Update newsletter stats
        """
        nl = await self.get_newsletter(newsletter_id)

        if nl.status not in ("draft", "scheduled", "sending"):
            raise ValidationError(f"Cannot send newsletter in status: {nl.status}")

        # Mark as sending
        nl.status = "sending"
        await self.db.flush()

        try:
            # Resolve subscribers
            subs = await get_segment_subscribers(self.db, nl.id, nl.segment)

            if test_email:
                subs = [s for s in subs if s.email == test_email]
                if not subs:
                    subs = [
                        NewsletterSub(
                            newsletter_id=nl.id,
                            email=test_email,
                            tier="free",
                            status="active",
                        )
                    ]

            # Send via configured email provider
            provider = await get_email_provider()
            sent_count = 0
            for sub in subs:
                try:
                    if sub.email and provider:
                        await send_email(
                            provider,
                            to_email=sub.email,
                            subject=nl.subject_line or nl.title,
                            html_body=nl.content_html,
                            text_body=nl.content_text or "",
                            sub_id=str(sub.id),
                            nl_id=str(nl.id),
                        )

                    delivery = NewsletterDelivery(
                        newsletter_id=nl.id,
                        subscriber_id=sub.id,
                        channel="email",
                        status="sent",
                        sent_at=datetime.now(UTC),
                    )
                    self.db.add(delivery)
                    sent_count += 1
                except Exception as exc:
                    logger.error(
                        "newsletter_send_failed_sub",
                        subscriber_id=str(sub.id),
                        error=str(exc),
                    )
                    delivery = NewsletterDelivery(
                        newsletter_id=nl.id,
                        subscriber_id=sub.id,
                        channel="email",
                        status="failed",
                        error_message=str(exc),
                    )
                    self.db.add(delivery)

            # Update newsletter
            nl.status = "sent"
            nl.sent_at = datetime.now(UTC)
            nl.total_recipients = sent_count
            await self.db.flush()

            logger.info(
                "newsletter_sent",
                newsletter_id=newsletter_id,
                recipients=sent_count,
                test=bool(test_email),
            )

            return {
                "newsletter_id": newsletter_id,
                "status": "sent",
                "recipients": sent_count,
                "test_email": test_email,
            }
        except Exception as exc:
            nl.status = "failed"
            await self.db.flush()
            logger.error("newsletter_send_failed", newsletter_id=newsletter_id, error=str(exc))
            raise

    async def schedule_newsletter(
        self,
        newsletter_id: str,
        publish_at: datetime,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        """Schedule a newsletter for future delivery."""
        nl = await self.get_newsletter(newsletter_id)

        if publish_at <= datetime.now(UTC):
            raise ValidationError("publish_at must be in the future")

        nl.status = "scheduled"
        nl.publish_at = publish_at
        nl.recurrence = recurrence
        await self.db.flush()

        logger.info(
            "newsletter_scheduled",
            newsletter_id=newsletter_id,
            publish_at=publish_at.isoformat(),
            recurrence=recurrence,
        )

        return {
            "newsletter_id": newsletter_id,
            "status": "scheduled",
            "publish_at": publish_at.isoformat(),
            "recurrence": recurrence,
        }

    async def get_delivery_status(self, newsletter_id: str) -> dict[str, Any]:
        """Get delivery statistics for a newsletter."""
        return await compute_delivery_status(self.db, newsletter_id)

    # ── Crypto Payment Integration ──────────────────────────────────

    async def subscribe_with_crypto(
        self,
        data: CryptoSubscribeRequest,
    ) -> NewsletterSub:
        """Subscribe to a newsletter with a crypto payment.

        Verifies the on-chain transaction, creates subscriber + payment records.
        Supports: ETH, SOL, USDC, Lightning.
        """
        # Load newsletter
        try:
            nl_id = uuid.UUID(data.newsletter_id)
        except ValueError:
            raise NotFoundError(f"Invalid newsletter ID: {data.newsletter_id}")

        nl = await self.db.get(Newsletter, nl_id)
        if not nl:
            raise NotFoundError(f"Newsletter not found: {data.newsletter_id}")

        # Validate tier
        tier_config = SUBSCRIPTION_TIERS.get(data.tier)
        if not tier_config:
            raise ValidationError(f"Invalid tier: {data.tier}. Must be one of: {list(SUBSCRIPTION_TIERS.keys())}")

        if data.tier == "free" and data.tx_hash:
            tier_config["duration_days"] = 0

        # Verify on-chain transaction
        if data.tier != "free":
            verified = await verify_crypto_payment(
                chain=data.chain,
                tx_hash=data.tx_hash,
                expected_token=data.token,
                expected_amount=tier_config["price_usd"],
                from_address=data.from_address,
            )
            if not verified:
                raise ValidationError("Payment verification failed. Transaction not confirmed or amount mismatch")

        # Check for existing subscriber
        existing_sub = None
        if data.email:
            result = await self.db.execute(
                select(NewsletterSub).where(
                    NewsletterSub.newsletter_id == nl.id,
                    NewsletterSub.email == data.email,
                ),
            )
            existing_sub = result.scalar_one_or_none()

        if not existing_sub and data.wallet_address:
            result = await self.db.execute(
                select(NewsletterSub).where(
                    NewsletterSub.newsletter_id == nl.id,
                    NewsletterSub.wallet_address == data.wallet_address,
                ),
            )
            existing_sub = result.scalar_one_or_none()

        if existing_sub:
            existing_sub.tier = data.tier
            existing_sub.status = "active"
            existing_sub.subscription_tx_hash = data.tx_hash if data.tier != "free" else None
            if data.tier != "free":
                existing_sub.subscription_expires_at = datetime.now(UTC) + timedelta(
                    days=tier_config["duration_days"],
                )
            await self.db.flush()
            sub = existing_sub
        else:
            sub = NewsletterSub(
                newsletter_id=nl.id,
                email=data.email,
                wallet_address=data.wallet_address,
                protocol=data.protocol,
                protocol_id=data.protocol_id,
                tier=data.tier,
                status="active",
                subscription_tx_hash=data.tx_hash if data.tier != "free" else None,
                subscription_expires_at=(
                    datetime.now(UTC) + timedelta(days=tier_config["duration_days"]) if data.tier != "free" else None
                ),
                interests=data.interests,
                referrer=data.referrer,
                source="signup",
            )
            self.db.add(sub)
            await self.db.flush()

        # Record payment if paid tier
        if data.tier != "free":
            payment = NewsletterPayment(
                subscriber_id=sub.id,
                chain=data.chain,
                token=data.token,
                amount=str(tier_config["price_usd"]),
                usd_value=float(tier_config["price_usd"]),
                tx_hash=data.tx_hash,
                from_address=data.from_address,
                tier=data.tier,
                duration_days=tier_config["duration_days"],
                expires_at=sub.subscription_expires_at
                or datetime.now(UTC) + timedelta(days=tier_config["duration_days"]),
                status="confirmed",
                verified_at=datetime.now(UTC),
                platform_fee_pct=PLATFORM_FEE_PCT,
                creator_amount=str(round(tier_config["price_usd"] * (1 - PLATFORM_FEE_PCT / 100), 2)),
                platform_amount=str(round(tier_config["price_usd"] * (PLATFORM_FEE_PCT / 100), 2)),
            )
            self.db.add(payment)
            await self.db.flush()

            nl.total_revenue = (nl.total_revenue or 0) + tier_config["price_usd"]

        logger.info(
            "newsletter_subscribed",
            newsletter_id=data.newsletter_id,
            subscriber_id=str(sub.id),
            tier=data.tier,
            chain=data.chain if data.tier != "free" else None,
        )
        return sub

    # ── Subscriber Management ────────────────────────────────────────

    async def list_subscribers(
        self,
        newsletter_id: str | None = None,
        tier: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> tuple[list[NewsletterSub], int]:
        """List subscribers with optional filters."""
        return await list_subscribers(self.db, newsletter_id, tier, status, offset, limit)

    async def import_subscribers(
        self,
        newsletter_id: str,
        rows: list[SubscriberImportRow],
        source: str = "import",
    ) -> dict[str, Any]:
        """Bulk import subscribers from CSV/JSON data."""
        try:
            nl_id = uuid.UUID(newsletter_id)
        except ValueError:
            raise NotFoundError(f"Invalid newsletter ID: {newsletter_id}")

        nl = await self.db.get(Newsletter, nl_id)
        if not nl:
            raise NotFoundError(f"Newsletter not found: {newsletter_id}")

        result = await import_subscribers_batch(self.db, nl.id, rows, source)

        logger.info(
            "newsletter_subscribers_imported",
            newsletter_id=newsletter_id,
            imported=result["imported"],
            skipped=result["skipped"],
            errors=len(result["errors"]),
        )
        return result

    async def export_subscribers_csv(self, newsletter_id: str) -> str:
        """Export subscribers as CSV string."""
        return await export_subscribers_csv(self.db, newsletter_id)

    # ── Cross-Platform Publishing ───────────────────────────────────

    async def publish_crossplatform(
        self,
        newsletter_id: str,
        platforms: list[str] | None = None,
        platforms_extra: dict[str, Any] | None = None,
    ) -> PublishResponse:
        """Publish a newsletter to all configured platforms."""
        nl = await self.get_newsletter(newsletter_id)

        # Build canonical URL (from Ghost or our platform)
        canonical_url = ""
        if nl.ghost_post_id:
            ghost_url = get_setting("ghost_url", "")
            if ghost_url:
                canonical_url = f"{ghost_url.rstrip('/')}/{nl.slug}"

        # Run all platform publishers
        results = await publish_to_all(
            title=nl.title,
            content_html=nl.content_html,
            excerpt=nl.excerpt,
            tags=nl.tags,
            cover_image=nl.cover_image,
            slug=nl.slug,
            platforms=platforms,
            platform_extras=platforms_extra,
            canonical_url=canonical_url,
        )

        # Record crossposts in DB
        crossposts: list[NewsletterCrosspost] = []
        succeeded = 0
        failed = 0

        for result in results:
            xp = NewsletterCrosspost(
                newsletter_id=nl.id,
                platform=result.platform,
                platform_url=result.platform_url,
                platform_id=result.platform_id,
                status="published" if result.success else "failed",
                error_message=result.error_message,
                published_at=datetime.now(UTC) if result.success else None,
            )
            self.db.add(xp)
            crossposts.append(xp)

            if result.success:
                succeeded += 1
            else:
                failed += 1

        await self.db.flush()

        logger.info(
            "newsletter_published_crossplatform",
            newsletter_id=newsletter_id,
            succeeded=succeeded,
            failed=failed,
        )

        return PublishResponse(
            results=[
                {
                    "id": str(xp.id),
                    "newsletter_id": str(xp.newsletter_id),
                    "platform": xp.platform,
                    "platform_url": xp.platform_url,
                    "platform_id": xp.platform_id,
                    "status": xp.status,
                    "error_message": xp.error_message,
                }
                for xp in crossposts
            ],
            total=len(results),
            succeeded=succeeded,
            failed=failed,
        )

    # ── Analytics ────────────────────────────────────────────────────

    async def get_analytics(
        self,
        creator_id: str | None = None,
        newsletter_id: str | None = None,
    ) -> dict[str, Any]:
        """Get newsletter analytics dashboard."""
        if newsletter_id:
            nl = await self.get_newsletter(newsletter_id)
            info = await compute_newsletter_info_analytics(self.db, newsletter_id, nl.total_revenue or 0.0)
            delivery = await self.get_delivery_status(newsletter_id)
            info["best_performing_title"] = nl.title
            info["best_performing_open_rate"] = delivery["open_rate"]
            return info

        # Aggregate across all newsletters for this creator
        newsletters, total = await self.list_newsletters(creator_id=creator_id)
        return await compute_creator_analytics(self.db, newsletters)
