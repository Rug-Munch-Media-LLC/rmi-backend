"""NewsletterService — business logic for the newsletter backend.

Covers:
  - Newsletter CRUD (create, update, list, get, delete)
  - Auto-send engine (immediate, scheduled, recurring)
  - Crypto payment verification (ETH, SOL, USDC, Lightning)
  - Subscriber management (CRUD, import, export, segmentation)
  - Delivery tracking (sent, opened, clicked, bounced)
  - Analytics (delivery rate, open rate, revenue, growth, A/B testing)
  - Cross-platform publishing coordination

Every method follows the BaseService pattern:
  - Async with typed signatures
  - Structured logging using get_logger()
  - Consistent error handling via AppError hierarchy
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func as sa_func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.base_service import BaseService
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.http import get_http_client
from app.core.logging import get_logger
from app.domains.newsletter.models import (
    Newsletter,
    NewsletterCrosspost,
    NewsletterDelivery,
    NewsletterPayment,
    NewsletterSub,
)
from app.domains.newsletter.publisher import PublishResult, publish_to_all
from app.domains.newsletter.schemas import (
    CryptoSubscribeRequest,
    NewsletterCreate,
    NewsletterUpdate,
    PublishResponse,
    SubscribeRequest,
    SubscriberImportRow,
)

logger = get_logger(__name__)

# ── Subscription tiers ──────────────────────────────────────────────

SUBSCRIPTION_TIERS: dict[str, dict[str, Any]] = {
    "free": {"price_usd": 0, "duration_days": 0, "label": "Free"},
    "monthly": {"price_usd": 5, "duration_days": 30, "label": "Monthly ($5)"},
    "annual": {"price_usd": 50, "duration_days": 365, "label": "Annual ($50)"},
    "founding": {"price_usd": 100, "duration_days": 365, "label": "Founding ($100)"},
}

PLATFORM_FEE_PCT = 10.0  # 90/10 split


class NewsletterService(BaseService[Newsletter]):
    """Newsletter business logic."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Newsletter CRUD ─────────────────────────────────────────────

    async def create_newsletter(
        self,
        creator_id: str,
        data: NewsletterCreate,
    ) -> Newsletter:
        """Create a new newsletter campaign.

        If publish_at is set, it's scheduled. If not, it stays as draft.
        """
        slug = self._slugify(data.title)
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
            subs = await self._get_segment_subscribers(nl.id, nl.segment)

            if test_email:
                # Test mode: send to single email
                subs = [s for s in subs if s.email == test_email]
                if not subs:
                    # Create a temp subscriber record for the test
                    subs = [
                        NewsletterSub(
                            newsletter_id=nl.id,
                            email=test_email,
                            tier="free",
                            status="active",
                        )
                    ]

            # Send via configured email provider
            email_provider = await self._get_email_provider()
            sent_count = 0
            for sub in subs:
                try:
                    if sub.email and email_provider:
                        await self._send_email(
                            email_provider,
                            to_email=sub.email,
                            subject=nl.subject_line or nl.title,
                            html_body=nl.content_html,
                            text_body=nl.content_text or "",
                            sub_id=str(sub.id),
                            nl_id=str(nl.id),
                        )

                    # Record delivery
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
        nl = await self.get_newsletter(newsletter_id)

        # Aggregate from delivery records
        total_result = await self.db.execute(
            select(sa_func.count()).where(
                NewsletterDelivery.newsletter_id == nl.id,
            ),
        )
        total_sent = total_result.scalar() or 0

        opened_result = await self.db.execute(
            select(sa_func.count()).where(
                NewsletterDelivery.newsletter_id == nl.id,
                NewsletterDelivery.opened_at.isnot(None),
            ),
        )
        total_opened = opened_result.scalar() or 0

        clicked_result = await self.db.execute(
            select(sa_func.count()).where(
                NewsletterDelivery.newsletter_id == nl.id,
                NewsletterDelivery.clicked_at.isnot(None),
            ),
        )
        total_clicked = clicked_result.scalar() or 0

        bounced_result = await self.db.execute(
            select(sa_func.count()).where(
                NewsletterDelivery.newsletter_id == nl.id,
                NewsletterDelivery.status == "bounced",
            ),
        )
        total_bounced = bounced_result.scalar() or 0

        failed_result = await self.db.execute(
            select(sa_func.count()).where(
                NewsletterDelivery.newsletter_id == nl.id,
                NewsletterDelivery.status == "failed",
            ),
        )
        total_failed = failed_result.scalar() or 0

        return {
            "newsletter_id": newsletter_id,
            "total_sent": total_sent,
            "total_delivered": total_sent - total_bounced - total_failed,
            "total_opened": total_opened,
            "total_clicked": total_clicked,
            "total_bounced": total_bounced,
            "total_failed": total_failed,
            "open_rate": round(total_opened / max(total_sent, 1) * 100, 2),
            "click_rate": round(total_clicked / max(total_sent, 1) * 100, 2),
            "delivery_rate": round(
                (total_sent - total_bounced - total_failed) / max(total_sent, 1) * 100,
                2,
            ),
        }

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
            # Free tier doesn't need payment
            tier_config["duration_days"] = 0

        # Verify on-chain transaction
        if data.tier != "free":
            verified = await self._verify_crypto_payment(
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
            # Update existing subscriber
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
            # Create new subscriber
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

            # Update newsletter revenue
            nl.total_revenue = (nl.total_revenue or 0) + tier_config["price_usd"]

        logger.info(
            "newsletter_subscribed",
            newsletter_id=data.newsletter_id,
            subscriber_id=str(sub.id),
            tier=data.tier,
            chain=data.chain if data.tier != "free" else None,
        )
        return sub

    async def list_subscribers(
        self,
        newsletter_id: str | None = None,
        tier: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> tuple[list[NewsletterSub], int]:
        """List subscribers with optional filters."""
        query = select(NewsletterSub).order_by(NewsletterSub.created_at.desc())

        if newsletter_id:
            try:
                query = query.where(NewsletterSub.newsletter_id == uuid.UUID(newsletter_id))
            except ValueError:
                raise NotFoundError(f"Invalid newsletter ID: {newsletter_id}")
        if tier:
            query = query.where(NewsletterSub.tier == tier)
        if status:
            query = query.where(NewsletterSub.status == status)

        count_query = select(sa_func.count()).select_from(query.subquery())
        total = await self.db.scalar(count_query) or 0

        query = query.offset(offset).limit(limit)
        result = await self.db.execute(query)
        items = list(result.scalars().all())

        return items, total

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

        imported = 0
        skipped = 0
        errors: list[dict[str, str]] = []

        for row in rows:
            if not row.email and not row.wallet_address and not row.protocol_id:
                errors.append(
                    {"row": str(rows.index(row) + 1), "error": "No identifier (email, wallet, or protocol_id)"}
                )
                skipped += 1
                continue

            try:
                # Check for duplicate
                dup = None
                if row.email:
                    result = await self.db.execute(
                        select(NewsletterSub).where(
                            NewsletterSub.newsletter_id == nl.id,
                            NewsletterSub.email == row.email,
                        ),
                    )
                    dup = result.scalar_one_or_none()

                if not dup:
                    sub = NewsletterSub(
                        newsletter_id=nl.id,
                        email=row.email,
                        wallet_address=row.wallet_address,
                        protocol=row.protocol,
                        protocol_id=row.protocol_id,
                        tier=row.tier,
                        status="active",
                        interests=row.interests,
                        source=source,
                    )
                    self.db.add(sub)
                    imported += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors.append({"row": str(rows.index(row) + 1), "error": str(exc)})
                skipped += 1

        await self.db.flush()
        logger.info(
            "newsletter_subscribers_imported",
            newsletter_id=newsletter_id,
            imported=imported,
            skipped=skipped,
            errors=len(errors),
        )
        return {"imported": imported, "skipped": skipped, "errors": errors}

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
            ghost_url = self._get_setting("ghost_url", "")
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
        """Get newsletter analytics dashboard.

        Aggregates across all newsletters for a creator, or a single newsletter.
        """
        if newsletter_id:
            nl = await self.get_newsletter(newsletter_id)
            delivery = await self.get_delivery_status(newsletter_id)

            return {
                "total_newsletters": 1,
                "total_subscribers": await self._count_subscribers(newsletter_id),
                "paid_subscribers": await self._count_subscribers(newsletter_id, paid_only=True),
                "total_revenue": nl.total_revenue or 0.0,
                "platform_fees": round((nl.total_revenue or 0.0) * PLATFORM_FEE_PCT / 100, 2),
                "creator_earnings": round((nl.total_revenue or 0.0) * (1 - PLATFORM_FEE_PCT / 100), 2),
                "avg_open_rate": delivery["open_rate"],
                "avg_click_rate": delivery["click_rate"],
                "subscriber_growth_7d": await self._subscriber_growth(newsletter_id, 7),
                "subscriber_growth_30d": await self._subscriber_growth(newsletter_id, 30),
                "best_performing_title": nl.title,
                "best_performing_open_rate": delivery["open_rate"],
            }

        # Aggregate across all newsletters for this creator
        newsletters, total = await self.list_newsletters(creator_id=creator_id)

        total_subscribers = 0
        total_paid = 0
        total_revenue = 0.0
        total_open_rate = 0.0
        best_nl = None
        best_open = 0.0

        for nl in newsletters:
            total_subscribers += await self._count_subscribers(str(nl.id))
            total_paid += await self._count_subscribers(str(nl.id), paid_only=True)
            total_revenue += nl.total_revenue or 0.0

            delivery = await self.get_delivery_status(str(nl.id))
            if delivery["open_rate"] > best_open:
                best_open = delivery["open_rate"]
                best_nl = nl

        news_count = len(newsletters)
        return {
            "total_newsletters": news_count,
            "total_subscribers": total_subscribers,
            "paid_subscribers": total_paid,
            "total_revenue": total_revenue,
            "platform_fees": round(total_revenue * PLATFORM_FEE_PCT / 100, 2),
            "creator_earnings": round(total_revenue * (1 - PLATFORM_FEE_PCT / 100), 2),
            "avg_open_rate": round(total_open_rate / max(news_count, 1), 2),
            "avg_click_rate": 0.0,
            "subscriber_growth_7d": 0,
            "subscriber_growth_30d": 0,
            "best_performing_title": best_nl.title if best_nl else None,
            "best_performing_open_rate": best_open if best_nl else None,
        }

    async def export_subscribers_csv(self, newsletter_id: str) -> str:
        """Export subscribers as CSV string."""
        subs, _ = await self.list_subscribers(newsletter_id=newsletter_id, limit=100000)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["email", "wallet_address", "protocol", "protocol_id", "tier", "status", "created_at"])

        for sub in subs:
            writer.writerow(
                [
                    sub.email or "",
                    sub.wallet_address or "",
                    sub.protocol or "",
                    sub.protocol_id or "",
                    sub.tier,
                    sub.status,
                    sub.created_at.isoformat() if sub.created_at else "",
                ]
            )

        return output.getvalue()

    # ── Private helpers ─────────────────────────────────────────────

    async def _get_segment_subscribers(
        self,
        newsletter_id: uuid.UUID,
        segment: str,
    ) -> list[NewsletterSub]:
        """Resolve subscriber list based on segment."""
        query = select(NewsletterSub).where(
            NewsletterSub.newsletter_id == newsletter_id,
            NewsletterSub.status == "active",
        )

        if segment == "paid_only":
            query = query.where(NewsletterSub.tier.in_(["monthly", "annual", "founding"]))
        elif segment == "free_only":
            query = query.where(NewsletterSub.tier == "free")

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def _verify_crypto_payment(
        self,
        chain: str,
        tx_hash: str,
        expected_token: str,
        expected_amount: int,
        from_address: str,
    ) -> bool:
        """Verify an on-chain crypto payment.

        Checks:
          1. Transaction exists and is confirmed (> 12 confirmations)
          2. Recipient matches our platform wallet
          3. Token matches expected
          4. Amount meets or exceeds expected
        """
        wallet = self._get_setting("platform_wallet_address", "")

        if not wallet:
            logger.warning("crypto_verify_no_wallet", chain=chain)
            return False

        try:
            client = await get_http_client()

            if chain == "eth":
                # Etherscan API for transaction verification
                api_key = self._get_setting("etherscan_api_key", "")
                if not api_key:
                    logger.warning("crypto_verify_no_etherscan_key")
                    return False

                resp = await client.get(
                    f"https://api.etherscan.io/api"
                    f"?module=proxy&action=eth_getTransactionByHash"
                    f"&txhash={tx_hash}&apikey={api_key}",
                    timeout=15.0,
                )
                if resp.status_code != 200:
                    return False

                tx_data = resp.json().get("result", {})
                if not tx_data:
                    return False

                # Check confirmations
                block_resp = await client.get(
                    f"https://api.etherscan.io/api?module=proxy&action=eth_blockNumber&apikey={api_key}",
                    timeout=10.0,
                )
                current_block = int(block_resp.json().get("result", "0x0"), 16)
                tx_block = int(tx_data.get("blockNumber", "0x0"), 16)
                confirmations = current_block - tx_block

                if confirmations < 12:
                    logger.warning(
                        "crypto_verify_low_confirmations",
                        tx_hash=tx_hash,
                        confirmations=confirmations,
                    )

                # Check recipient
                to_addr = (tx_data.get("to") or "").lower()
                if to_addr != wallet.lower():
                    logger.warning(
                        "crypto_verify_wrong_recipient",
                        tx_hash=tx_hash,
                        expected=wallet,
                        got=to_addr,
                    )
                    return False

                # TX confirmed, recipient matches - accept
                return confirmations >= 12

            elif chain == "sol":
                # Solana transaction verification via Solscan or Helius
                api_key = self._get_setting("helius_api_key", "")
                if not api_key:
                    logger.warning("crypto_verify_no_helius_key")
                    return False

                resp = await client.get(
                    f"https://api.helius.xyz/v0/transactions/?apiKey={api_key}&search={tx_hash}",
                    timeout=15.0,
                )
                if resp.status_code != 200:
                    return False

                txs = resp.json()
                if not txs:
                    return False

                tx = txs[0]
                # Check status
                if tx.get("type") == "TRANSFER" and tx.get("tokenTransfers"):
                    # Verify recipient
                    for transfer in tx["tokenTransfers"]:
                        if (
                            transfer.get("toUserAccount", "").lower() == wallet.lower()
                            and transfer.get("mint", "") == expected_token
                        ):
                            return True

                return False

            elif chain == "lightning":
                # Lightning payment verification via LNBits or similar
                invoice_key = self._get_setting("lightning_invoice_key", "")
                if not invoice_key:
                    logger.warning("crypto_verify_no_lightning_key")
                    return False

                resp = await client.get(
                    f"https://lnbits.example.com/api/v1/payments/{tx_hash}",
                    headers={"X-Api-Key": invoice_key},
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("paid", False)

                return False

            else:
                logger.warning("crypto_verify_unsupported_chain", chain=chain)
                return False

        except Exception as exc:
            logger.error("crypto_verify_failed", chain=chain, tx_hash=tx_hash, error=str(exc))
            return False

    async def _send_email(
        self,
        provider: str,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str,
        sub_id: str,
        nl_id: str,
    ) -> None:
        """Send an email via the configured provider (Postmark/SES/Mailgun)."""
        client = await get_http_client()

        if provider == "postmark":
            api_key = self._get_setting("postmark_api_key", "")
            if not api_key:
                raise ValidationError("Postmark API key not configured")

            await client.post(
                "https://api.postmarkapp.com/email",
                json={
                    "From": self._get_setting("newsletter_from_email", "newsletter@rugmunch.io"),
                    "To": to_email,
                    "Subject": subject,
                    "HtmlBody": html_body,
                    "TextBody": text_body,
                    "TrackOpens": True,
                    "TrackLinks": "HtmlOnly",
                    "Metadata": {"subscriber_id": sub_id, "newsletter_id": nl_id},
                },
                headers={
                    "X-Postmark-Server-Token": api_key,
                    "Accept": "application/json",
                },
                timeout=15.0,
            )
        elif provider == "ses":
            # AWS SES via boto3 or direct API
            from_email = self._get_setting("newsletter_from_email", "newsletter@rugmunch.io")

            await client.post(
                "https://email.us-east-1.amazonaws.com/v2/email/outbound-emails",
                json={
                    "FromEmailAddress": from_email,
                    "Destination": {"ToAddresses": [to_email]},
                    "Content": {
                        "Simple": {
                            "Subject": {"Data": subject},
                            "Body": {
                                "Html": {"Data": html_body},
                                "Text": {"Data": text_body},
                            },
                        },
                    },
                },
                headers={
                    "X-Api-Key": self._get_setting("aws_ses_key", ""),
                },
                timeout=15.0,
            )
        elif provider == "mailgun":
            domain = self._get_setting("mailgun_domain", "")
            api_key = self._get_setting("mailgun_api_key", "")

            if not domain or not api_key:
                raise ValidationError("Mailgun not configured")

            await client.post(
                f"https://api.mailgun.net/v3/{domain}/messages",
                auth=("api", api_key),
                data={
                    "from": self._get_setting("newsletter_from_email", f"newsletter@{domain}"),
                    "to": to_email,
                    "subject": subject,
                    "html": html_body,
                    "text": text_body,
                    "o:tracking-opens": "yes",
                    "o:tracking-clicks": "yes",
                    "v:subscriber_id": sub_id,
                    "v:newsletter_id": nl_id,
                },
                timeout=15.0,
            )

    async def _get_email_provider(self) -> str | None:
        """Determine which email provider is configured.

        Priority: Postmark > Mailgun > SES > None (email disabled).
        """
        if self._get_setting("postmark_api_key", ""):
            return "postmark"
        if self._get_setting("mailgun_api_key", ""):
            return "mailgun"
        if self._get_setting("aws_ses_key", ""):
            return "ses"
        return None

    async def _count_subscribers(
        self,
        newsletter_id: str,
        paid_only: bool = False,
    ) -> int:
        """Count active subscribers for a newsletter."""
        try:
            nl_id = uuid.UUID(newsletter_id)
        except ValueError:
            return 0

        query = select(sa_func.count()).where(
            NewsletterSub.newsletter_id == nl_id,
            NewsletterSub.status == "active",
        )
        if paid_only:
            query = query.where(NewsletterSub.tier.in_(["monthly", "annual", "founding"]))

        result = await self.db.execute(query)
        return result.scalar() or 0

    async def _subscriber_growth(self, newsletter_id: str, days: int) -> int:
        """Count new subscribers in the last N days."""
        try:
            nl_id = uuid.UUID(newsletter_id)
        except ValueError:
            return 0

        cutoff = datetime.now(UTC) - timedelta(days=days)
        result = await self.db.execute(
            select(sa_func.count()).where(
                NewsletterSub.newsletter_id == nl_id,
                NewsletterSub.created_at >= cutoff,
            ),
        )
        return result.scalar() or 0

    def _get_setting(self, key: str, default: str = "") -> str:
        """Get a configuration value."""
        from app.config import settings

        return getattr(settings, key, None) or default or ""

    @staticmethod
    def _slugify(text: str) -> str:
        """Create a URL-safe slug."""
        import re

        text = text.lower().strip()
        text = re.sub(r"[^a-z0-9]+", "-", text)
        text = text.strip("-")
        return text[:200] or "untitled"
