"""Subscriber management — CRUD, import, export, segmentation utilities."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func as sa_func, select

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.domains.newsletter.models import NewsletterSub

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


async def list_subscribers(
    db: AsyncSession,
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
    total = await db.scalar(count_query) or 0

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return items, total


async def count_subscribers(
    db: AsyncSession,
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

    result = await db.execute(query)
    return result.scalar() or 0


async def subscriber_growth(
    db: AsyncSession,
    newsletter_id: str,
    days: int,
) -> int:
    """Count new subscribers in the last N days."""
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


async def get_segment_subscribers(
    db: AsyncSession,
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

    result = await db.execute(query)
    return list(result.scalars().all())


async def export_subscribers_csv(
    db: AsyncSession,
    newsletter_id: str,
    max_limit: int = 100000,
) -> str:
    """Export subscribers as CSV string."""
    subs, _ = await list_subscribers(db, newsletter_id=newsletter_id, limit=max_limit)

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


async def import_subscribers_batch(
    db: AsyncSession,
    newsletter_id: uuid.UUID,
    rows: list[Any],
    source: str = "import",
) -> dict[str, Any]:
    """Bulk import subscribers from parsed row data.

    Each row must have 'email', 'wallet_address', 'protocol', 'protocol_id',
    'tier', and 'interests' attributes (like SubscriberImportRow).
    """
    imported = 0
    skipped = 0
    errors: list[dict[str, str]] = []

    for i, row in enumerate(rows):
        email = getattr(row, "email", None) or ""
        wallet_address = getattr(row, "wallet_address", None) or ""
        protocol_id = getattr(row, "protocol_id", None) or ""

        if not email and not wallet_address and not protocol_id:
            errors.append({"row": str(i + 1), "error": "No identifier (email, wallet, or protocol_id)"})
            skipped += 1
            continue

        try:
            dup = None
            if email:
                result = await db.execute(
                    select(NewsletterSub).where(
                        NewsletterSub.newsletter_id == newsletter_id,
                        NewsletterSub.email == email,
                    ),
                )
                dup = result.scalar_one_or_none()

            if not dup:
                sub = NewsletterSub(
                    newsletter_id=newsletter_id,
                    email=email or None,
                    wallet_address=wallet_address or None,
                    protocol=getattr(row, "protocol", None) or None,
                    protocol_id=protocol_id or None,
                    tier=getattr(row, "tier", "free") or "free",
                    status="active",
                    interests=getattr(row, "interests", None) or None,
                    source=source,
                )
                db.add(sub)
                imported += 1
            else:
                skipped += 1
        except Exception as exc:
            errors.append({"row": str(i + 1), "error": str(exc)})
            skipped += 1

    await db.flush()
    return {"imported": imported, "skipped": skipped, "errors": errors}
