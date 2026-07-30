"""Webhook subscription API — /api/v1/scanner/webhooks/*.

Endpoints:
  POST   /api/v1/scanner/webhooks          — Create a subscription
  GET    /api/v1/scanner/webhooks          — List subscriptions
  GET    /api/v1/scanner/webhooks/events   — Available event types
  DELETE /api/v1/scanner/webhooks/{id}     — Unsubscribe
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger
from app.domains.scanner.webhooks.models import (
    CreateWebhookRequest,
    WebhookEventType,
    WebhookEventTypeInfo,
    WebhookSubscription,
)
from app.domains.scanner.webhooks.repository import WebhookRepository

log = get_logger(__name__)

router = APIRouter(prefix="/scanner/webhooks", tags=["scanner-webhooks"])
_repo = WebhookRepository()

# ── Event type catalog ──────────────────────────────────────────────

EVENT_TYPE_CATALOG: list[WebhookEventTypeInfo] = [
    WebhookEventTypeInfo(
        event_type=WebhookEventType.TOKEN_DEPLOYED,
        description="New token contract detected by our deployer tracker",
        data_source="ClickHouse (wallet_memory.deployer_history)",
    ),
    WebhookEventTypeInfo(
        event_type=WebhookEventType.TOKEN_FIRST_SWAP,
        description="First DEX swap detected for a token",
        data_source="ClickHouse (wallet_memory.token_transfers × DEX router list)",
    ),
    WebhookEventTypeInfo(
        event_type=WebhookEventType.TOKEN_PRICE_CHANGE,
        description="Token price moves beyond configured % threshold",
        data_source="Internal price oracle (own_price_consensus)",
    ),
    WebhookEventTypeInfo(
        event_type=WebhookEventType.TOKEN_WHALE_MOVE,
        description="Large transfer (>$10K) from a known whale address",
        data_source="ClickHouse (wallet_memory.token_transfers)",
    ),
    WebhookEventTypeInfo(
        event_type=WebhookEventType.TOKEN_LIQUIDITY_CHANGE,
        description="Significant LP add or remove (>$5K)",
        data_source="ClickHouse (wallet_memory.token_transfers + LiquidityWatchdog)",
    ),
    WebhookEventTypeInfo(
        event_type=WebhookEventType.WALLET_ACTIVITY,
        description="Any transaction from a watched wallet address",
        data_source="ClickHouse (wallet_memory.token_transfers)",
    ),
    WebhookEventTypeInfo(
        event_type=WebhookEventType.CHAIN_BLOCK,
        description="New block detected on monitored chains",
        data_source="Free public RPCs (cloudflare-eth.com, api.mainnet-beta.solana.com, etc.)",
    ),
]

# ── Endpoints ───────────────────────────────────────────────────────


@router.get("", response_model=dict[str, Any])
async def list_webhooks() -> dict[str, Any]:
    """List all webhook subscriptions (with optional owner_id filter)."""
    subs = await _repo.list_all()
    return {
        "count": len(subs),
        "subscriptions": [s.model_dump(mode="json") for s in subs],
    }


@router.post("", response_model=dict[str, Any], status_code=201)
async def create_webhook(req: CreateWebhookRequest) -> dict[str, Any]:
    """Create a new webhook subscription.

    Validates the URL format and event types before persisting.
    The webhook engine will start delivering events within one poll cycle.
    """
    sub = WebhookSubscription(
        id=f"whk:{int(datetime.now(UTC).timestamp())}",
        url=req.url,
        event_types=req.event_types,
        filters=req.filters or None,
    )
    await _repo.create(sub)
    log.info(
        "webhook_created",
        sub_id=sub.id,
        types=[t.value for t in sub.event_types],
    )
    return {"status": "created", "subscription": sub.model_dump(mode="json")}


@router.delete("/{sub_id}", response_model=dict[str, Any])
async def delete_webhook(sub_id: str) -> dict[str, Any]:
    """Delete (unsubscribe) a webhook by ID."""
    found = await _repo.delete(sub_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"Webhook not found: {sub_id}")
    return {"status": "deleted", "id": sub_id}


@router.get("/events", response_model=list[dict[str, Any]])
async def list_webhook_events() -> list[dict[str, Any]]:
    """Return all available webhook event types with descriptions.

    This endpoint does not require authentication — it's informational.
    """
    return [info.model_dump(mode="json") for info in EVENT_TYPE_CATALOG]
