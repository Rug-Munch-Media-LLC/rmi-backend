"""Newsletter API router.

Mounts at /api/v1/newsletter/* with the following endpoints:

  POST   /api/v1/newsletter                   — Create newsletter
  GET    /api/v1/newsletter                    — List newsletters
  GET    /api/v1/newsletter/{id}              — Get newsletter
  PUT    /api/v1/newsletter/{id}              — Update newsletter
  DELETE /api/v1/newsletter/{id}              — Delete newsletter
  POST   /api/v1/newsletter/send              — Send newsletter
  POST   /api/v1/newsletter/schedule          — Schedule newsletter
  GET    /api/v1/newsletter/delivery-status   — Delivery analytics
  POST   /api/v1/newsletter/publish           — Publish cross-platform
  POST   /api/v1/newsletter/subscribe         — Subscribe with crypto
  GET    /api/v1/newsletter/subscribers       — Subscriber list
  POST   /api/v1/newsletter/subscribers/import — Import subscribers
  GET    /api/v1/newsletter/subscribers/export — Export subscribers CSV
  GET    /api/v1/newsletter/analytics         — Analytics dashboard

Every endpoint follows RFC 7807 error handling and Stripe-style envelopes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_auth
from app.core.database import get_db
from app.core.logging import get_logger
from app.domains.newsletter.schemas import (
    ActionResponse,
    CryptoSubscribeRequest,
    DeliveryStatusOut,
    NewsletterAnalytics,
    NewsletterCreate,
    NewsletterListResponse,
    NewsletterOut,
    NewsletterUpdate,
    PublishRequest,
    PublishResponse,
    ScheduleRequest,
    SendRequest,
    SubscribeRequest,
    SubscriberImportRequest,
    SubscriberImportResponse,
    SubscriberListResponse,
    SubscriberOut,
)
from app.domains.newsletter.service import NewsletterService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/newsletter", tags=["newsletter"])


# ── Dependency ──────────────────────────────────────────────────────


async def get_newsletter_service(db: AsyncSession = Depends(get_db)) -> NewsletterService:
    """FastAPI dependency for NewsletterService."""
    return NewsletterService(db)


async def get_creator_id(
    current_user: dict[str, Any] = Depends(require_auth),
) -> str:
    """Extract creator identity from the authenticated JWT token.

    Requires a valid user JWT (``Authorization: Bearer <jwt>`` or the
    ``rmi_token`` HttpOnly cookie). The creator id is the authenticated
    user's id — never a client-supplied header.
    """
    uid = current_user.get("id") or current_user.get("sub")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid auth token")
    return str(uid)


def _newsletter_to_out(nl: Any) -> dict[str, Any]:
    """Convert a Newsletter ORM model to a response dict."""
    return {
        "id": str(nl.id),
        "creator_id": nl.creator_id,
        "title": nl.title,
        "slug": nl.slug,
        "content_html": nl.content_html,
        "content_text": nl.content_text,
        "excerpt": nl.excerpt,
        "cover_image": nl.cover_image,
        "tags": nl.tags or [],
        "status": nl.status,
        "publish_at": nl.publish_at.isoformat() if nl.publish_at else None,
        "sent_at": nl.sent_at.isoformat() if nl.sent_at else None,
        "segment": nl.segment,
        "subject_line": nl.subject_line,
        "preheader_text": nl.preheader_text,
        "recurrence": nl.recurrence,
        "total_recipients": nl.total_recipients,
        "total_opened": nl.total_opened,
        "total_clicked": nl.total_clicked,
        "total_revenue": nl.total_revenue,
        "ghost_post_id": nl.ghost_post_id,
        "created_at": nl.created_at.isoformat() if nl.created_at else None,
        "updated_at": nl.updated_at.isoformat() if nl.updated_at else None,
    }


def _subscriber_to_out(sub: Any) -> dict[str, Any]:
    """Convert a NewsletterSub ORM model to a response dict."""
    return {
        "id": str(sub.id),
        "newsletter_id": str(sub.newsletter_id),
        "email": sub.email,
        "wallet_address": sub.wallet_address,
        "protocol": sub.protocol,
        "protocol_id": sub.protocol_id,
        "tier": sub.tier,
        "status": sub.status,
        "subscription_tx_hash": sub.subscription_tx_hash,
        "subscription_expires_at": sub.subscription_expires_at.isoformat() if sub.subscription_expires_at else None,
        "token_gate_holdings": sub.token_gate_holdings,
        "interests": sub.interests or [],
        "source": sub.source,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


# ── Newsletter CRUD ─────────────────────────────────────────────────


@router.post("", response_model=NewsletterOut, status_code=201)
async def create_newsletter(
    data: NewsletterCreate,
    svc: NewsletterService = Depends(get_newsletter_service),
    creator_id: str = Depends(get_creator_id),
) -> dict[str, Any]:
    """Create a new newsletter campaign."""
    nl = await svc.create_newsletter(creator_id, data)
    return _newsletter_to_out(nl)


@router.get("", response_model=NewsletterListResponse)
async def list_newsletters(
    status: str | None = Query(None, description="Filter by status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    svc: NewsletterService = Depends(get_newsletter_service),
    creator_id: str = Depends(get_creator_id),
) -> dict[str, Any]:
    """List newsletters for the authenticated creator."""
    items, total = await svc.list_newsletters(
        creator_id=creator_id,
        status=status,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [_newsletter_to_out(nl) for nl in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/{newsletter_id}", response_model=NewsletterOut)
async def get_newsletter(
    newsletter_id: str,
    svc: NewsletterService = Depends(get_newsletter_service),
    _user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get a single newsletter by ID."""
    nl = await svc.get_newsletter(newsletter_id)
    return _newsletter_to_out(nl)


@router.put("/{newsletter_id}", response_model=NewsletterOut)
async def update_newsletter(
    newsletter_id: str,
    data: NewsletterUpdate,
    svc: NewsletterService = Depends(get_newsletter_service),
    _user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Update a draft or scheduled newsletter."""
    nl = await svc.update_newsletter(newsletter_id, data)
    return _newsletter_to_out(nl)


@router.delete("/{newsletter_id}", response_model=ActionResponse)
async def delete_newsletter(
    newsletter_id: str,
    svc: NewsletterService = Depends(get_newsletter_service),
    _user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a newsletter campaign."""
    await svc.delete_newsletter(newsletter_id)
    return {"success": True, "message": "Newsletter deleted"}


# ── Auto-Send Engine ────────────────────────────────────────────────


@router.post("/send", response_model=ActionResponse)
async def send_newsletter(
    data: SendRequest,
    svc: NewsletterService = Depends(get_newsletter_service),
    _user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Send a newsletter immediately.

    Optionally send a test to a single email first.
    """
    result = await svc.send_newsletter(
        newsletter_id=data.newsletter_id,
        test_email=data.test_email,
    )
    return {
        "success": result["status"] == "sent",
        "message": f"Newsletter sent to {result['recipients']} recipients",
        "data": result,
    }


@router.post("/schedule", response_model=ActionResponse)
async def schedule_newsletter(
    data: ScheduleRequest,
    svc: NewsletterService = Depends(get_newsletter_service),
    _user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Schedule a newsletter for future delivery."""
    result = await svc.schedule_newsletter(
        newsletter_id=data.newsletter_id,
        publish_at=data.publish_at,
        recurrence=data.recurrence,
    )
    return {
        "success": True,
        "message": f"Newsletter scheduled for {data.publish_at.isoformat()}",
        "data": result,
    }


@router.get("/delivery-status", response_model=DeliveryStatusOut)
async def delivery_status(
    newsletter_id: str = Query(..., description="Newsletter ID"),
    svc: NewsletterService = Depends(get_newsletter_service),
    _user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get delivery analytics for a newsletter."""
    return await svc.get_delivery_status(newsletter_id)


# ── Cross-Platform Publishing ───────────────────────────────────────


@router.post("/publish", response_model=PublishResponse)
async def publish_newsletter(
    data: PublishRequest,
    svc: NewsletterService = Depends(get_newsletter_service),
    _user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Publish a newsletter to all configured platforms simultaneously."""
    result = await svc.publish_crossplatform(
        newsletter_id=data.newsletter_id,
        platforms=data.platforms,
        platforms_extra=data.platforms_extra,
    )
    return result


# ── Subscription Management ─────────────────────────────────────────


@router.post("/subscribe", response_model=SubscriberOut, status_code=201)
async def subscribe(
    data: SubscribeRequest,
    svc: NewsletterService = Depends(get_newsletter_service),
) -> dict[str, Any]:
    """Subscribe to a newsletter (free or with crypto payment).

    For paid tiers, include chain, tx_hash, token, and from_address.
    """
    if data.tier != "free":
        # Re-interpret as CryptoSubscribeRequest
        CryptoSubscribeRequest(**data.model_dump())
        # We need chain/tx_hash/from_address/token for paid subs
        raise HTTPException(
            status_code=400,
            detail="Paid subscriptions require chain, tx_hash, token, and from_address. "
            "Use the subscribe/crypto endpoint instead.",
        )

    sub = await svc.subscribe_with_crypto(
        CryptoSubscribeRequest(
            newsletter_id=data.newsletter_id if hasattr(data, "newsletter_id") else data.newsletter_id,
            email=data.email,
            wallet_address=data.wallet_address,
            protocol=data.protocol,
            protocol_id=data.protocol_id,
            tier=data.tier,
            interests=data.interests,
            referrer=data.referrer,
            chain="",
            token="",
            tx_hash="",
            from_address="",
        )
    )
    return _subscriber_to_out(sub)


@router.post("/subscribe/crypto", response_model=SubscriberOut, status_code=201)
async def subscribe_crypto(
    data: CryptoSubscribeRequest,
    svc: NewsletterService = Depends(get_newsletter_service),
) -> dict[str, Any]:
    """Subscribe with a crypto payment. Verifies on-chain before activating."""
    sub = await svc.subscribe_with_crypto(data)
    return _subscriber_to_out(sub)


@router.get("/subscribers", response_model=SubscriberListResponse)
async def list_subscribers(
    newsletter_id: str | None = Query(None, description="Filter by newsletter"),
    tier: str | None = Query(None, description="Filter by tier"),
    status: str | None = Query(None, description="Filter by status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    svc: NewsletterService = Depends(get_newsletter_service),
    _user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """List subscribers with optional filters."""
    items, total = await svc.list_subscribers(
        newsletter_id=newsletter_id,
        tier=tier,
        status=status,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [_subscriber_to_out(sub) for sub in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("/subscribers/import", response_model=SubscriberImportResponse)
async def import_subscribers(
    data: SubscriberImportRequest,
    svc: NewsletterService = Depends(get_newsletter_service),
    _user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Bulk import subscribers."""
    result = await svc.import_subscribers(
        newsletter_id=data.newsletter_id,
        rows=data.subscribers,
        source=data.source,
    )
    return result


@router.get("/subscribers/export")
async def export_subscribers_csv(
    newsletter_id: str = Query(..., description="Newsletter ID"),
    svc: NewsletterService = Depends(get_newsletter_service),
    _user: dict[str, Any] = Depends(require_auth),
) -> str:
    """Export subscribers as CSV."""
    csv_data = await svc.export_subscribers_csv(newsletter_id)
    return csv_data


# ── Analytics ───────────────────────────────────────────────────────


@router.get("/analytics", response_model=NewsletterAnalytics)
async def get_analytics(
    newsletter_id: str | None = Query(None, description="Per-newsletter analytics"),
    svc: NewsletterService = Depends(get_newsletter_service),
    creator_id: str = Depends(get_creator_id),
) -> dict[str, Any]:
    """Get analytics dashboard — per-creator or per-newsletter."""
    return await svc.get_analytics(
        creator_id=creator_id if not newsletter_id else None,
        newsletter_id=newsletter_id,
    )
