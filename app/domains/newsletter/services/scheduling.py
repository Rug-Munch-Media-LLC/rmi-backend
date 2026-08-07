"""Scheduling — delivery status, analytics, publication coordination."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func as sa_func, select

from app.core.logging import get_logger
from app.domains.newsletter.models import NewsletterDelivery, NewsletterSub
from app.domains.newsletter.services.constants import PLATFORM_FEE_PCT
from app.domains.newsletter.services.subscribers import count_subscribers

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


async def compute_delivery_status(
    db: AsyncSession,
    newsletter_id: str,
) -> dict[str, Any]:
    """Get delivery statistics for a newsletter."""
    try:
        nl_id = uuid.UUID(newsletter_id)
    except ValueError:
        return {
            "newsletter_id": newsletter_id,
            "total_sent": 0,
            "total_delivered": 0,
            "total_opened": 0,
            "total_clicked": 0,
            "total_bounced": 0,
            "total_failed": 0,
            "open_rate": 0.0,
            "click_rate": 0.0,
            "delivery_rate": 0.0,
        }

    total_sent = (
        await db.scalar(
            select(sa_func.count()).where(NewsletterDelivery.newsletter_id == nl_id),
        )
        or 0
    )

    total_opened = (
        await db.scalar(
            select(sa_func.count()).where(
                NewsletterDelivery.newsletter_id == nl_id,
                NewsletterDelivery.opened_at.isnot(None),
            ),
        )
        or 0
    )

    total_clicked = (
        await db.scalar(
            select(sa_func.count()).where(
                NewsletterDelivery.newsletter_id == nl_id,
                NewsletterDelivery.clicked_at.isnot(None),
            ),
        )
        or 0
    )

    total_bounced = (
        await db.scalar(
            select(sa_func.count()).where(
                NewsletterDelivery.newsletter_id == nl_id,
                NewsletterDelivery.status == "bounced",
            ),
        )
        or 0
    )

    total_failed = (
        await db.scalar(
            select(sa_func.count()).where(
                NewsletterDelivery.newsletter_id == nl_id,
                NewsletterDelivery.status == "failed",
            ),
        )
        or 0
    )

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


async def compute_newsletter_info_analytics(
    db: AsyncSession,
    newsletter_id: str,
    total_revenue: float,
) -> dict[str, Any]:
    """Compute per-newsletter analytics summary."""
    delivery = await compute_delivery_status(db, newsletter_id)
    sub_count = await count_subscribers(db, newsletter_id)
    paid_count = await count_subscribers(db, newsletter_id, paid_only=True)
    growth_7d = await _subscriber_growth_raw(db, newsletter_id, 7)
    growth_30d = await _subscriber_growth_raw(db, newsletter_id, 30)

    return {
        "total_newsletters": 1,
        "total_subscribers": sub_count,
        "paid_subscribers": paid_count,
        "total_revenue": total_revenue,
        "platform_fees": round(total_revenue * PLATFORM_FEE_PCT / 100, 2),
        "creator_earnings": round(total_revenue * (1 - PLATFORM_FEE_PCT / 100), 2),
        "avg_open_rate": delivery["open_rate"],
        "avg_click_rate": delivery["click_rate"],
        "subscriber_growth_7d": growth_7d,
        "subscriber_growth_30d": growth_30d,
    }


async def compute_creator_analytics(
    db: AsyncSession,
    newsletters: list[Any],
) -> dict[str, Any]:
    """Aggregate analytics across all newsletters for a creator."""
    total_subscribers = 0
    total_paid = 0
    total_revenue = 0.0
    total_open_rate_sum = 0.0
    best_nl = None
    best_open = 0.0

    for nl in newsletters:
        nl_id = str(nl.id)
        total_subscribers += await count_subscribers(db, nl_id)
        total_paid += await count_subscribers(db, nl_id, paid_only=True)
        total_revenue += nl.total_revenue or 0.0

        delivery = await compute_delivery_status(db, nl_id)
        total_open_rate_sum += delivery["open_rate"]
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
        "avg_open_rate": round(total_open_rate_sum / max(news_count, 1), 2),
        "avg_click_rate": 0.0,
        "subscriber_growth_7d": 0,
        "subscriber_growth_30d": 0,
        "best_performing_title": best_nl.title if best_nl else None,
        "best_performing_open_rate": best_open if best_nl else None,
    }


async def _subscriber_growth_raw(
    db: AsyncSession,
    newsletter_id: str,
    days: int,
) -> int:
    """Count new subscribers in the last N days (internal)."""
    try:
        nl_id = uuid.UUID(newsletter_id)
    except ValueError:
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await db.execute(
        select(sa_func.count()).where(
            NewsletterSub.newsletter_id == nl_id,
            NewsletterSub.created_at >= cutoff,
        ),
    )
    return result.scalar() or 0
