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

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.webhooks.models import (
    CreateWebhookRequest,
    TelegramAlertRequest,
    UpdateWebhookRequest,
    WebhookEventType,
    WebhookEventTypeInfo,
    WebhookFilters,
    WebhookSubscription,
)
from app.domains.scanner.webhooks.repository import WebhookRepository

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/scanner/webhooks", tags=["scanner-webhooks"])
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
        data_source="ClickHouse (wallet_memory.token_transfers x DEX router list)",
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
    WebhookEventTypeInfo(
        event_type=WebhookEventType.TRANSACTION_ENHANCED,
        description="Full decoded transaction on watched wallets/tokens — token transfers, "
        "native SOL/ETH transfers, swap classification, account balance deltas "
        "(Helius-style enhanced transaction)",
        data_source="Helius RPC (HELIUS_API_KEY) / free Solana RPC + Alchemy (ALCHEMY_API_KEY) / EVM receipts",
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
    # Internal ``internal://telegram/`` targets are reserved for the bot's
    # alert bridge (see /scanner/webhooks/telegram) — public callers must
    # supply an external http(s) URL.
    if req.url.startswith("internal://"):
        raise HTTPException(status_code=400, detail="internal:// webhook URLs are reserved for bot alert subscriptions")
    sub = WebhookSubscription(
        id=f"whk:{int(datetime.now(UTC).timestamp())}",
        url=req.url,
        event_types=req.event_types,
        filters=req.filters or WebhookFilters(),
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


@router.get("/{sub_id}", response_model=dict[str, Any])
async def get_webhook(sub_id: str) -> dict[str, Any]:
    """Get a single webhook subscription by ID."""
    sub = await _repo.get(sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"Webhook not found: {sub_id}")
    return {"subscription": sub.model_dump(mode="json")}


@router.patch("/{sub_id}", response_model=dict[str, Any])
async def update_webhook(sub_id: str, req: UpdateWebhookRequest) -> dict[str, Any]:
    """Update an existing webhook subscription.

    Supports partial updates: only provided fields are patched. Set
    ``active: false`` to pause delivery without deleting the subscription
    (mirrors Helius's toggle-webhook behaviour).
    """
    sub = await _repo.get(sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"Webhook not found: {sub_id}")

    if req.url is not None:
        # Same guard as create: internal:// targets are reserved for the bot.
        if req.url.startswith("internal://"):
            raise HTTPException(
                status_code=400,
                detail="internal:// webhook URLs are reserved for bot alert subscriptions",
            )
        sub.url = req.url
    if req.event_types is not None:
        sub.event_types = req.event_types
    if req.filters is not None:
        sub.filters = req.filters
    if req.active is not None:
        sub.active = req.active

    await _repo.update(sub)
    log.info("webhook_updated", sub_id=sub.id, active=sub.active)
    return {"status": "updated", "subscription": sub.model_dump(mode="json")}


@router.post("/{sub_id}/test", response_model=dict[str, Any])
async def test_webhook(sub_id: str) -> dict[str, Any]:
    """Send a synthetic test event to the subscriber URL.

    Useful to verify an endpoint accepts our payload shape without waiting
    for a real on-chain event. Uses the first event type on the subscription.
    """
    sub = await _repo.get(sub_id)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"Webhook not found: {sub_id}")

    from app.domains.scanner.webhooks.delivery import WebhookDeliveryService
    from app.domains.scanner.webhooks.models import WebhookEvent

    first_type = sub.event_types[0]
    event = WebhookEvent(
        id=f"evt:test:{sub_id}",
        subscription_id=sub.id,
        event_type=first_type,
        payload={
            "test": True,
            "message": "Synthetic test event — verify endpoint accepts RMI webhook payloads",
            "token_address": "0xtest000000000000000000000000000000000000",
            "chain": "ethereum",
        },
        chain="ethereum",
    )
    delivery = await WebhookDeliveryService().deliver(event, sub)
    return {"status": delivery.status, "delivery": delivery.model_dump(mode="json")}


@router.get("/events", response_model=list[dict[str, Any]])
async def list_webhook_events() -> list[dict[str, Any]]:
    """Return all available webhook event types with descriptions.

    This endpoint does not require authentication — it's informational.
    """
    return [info.model_dump(mode="json") for info in EVENT_TYPE_CATALOG]


# ── Bot alert subscriptions (internal delivery to the Telegram bot) ────


@router.post("/telegram", response_model=dict[str, Any], status_code=201)
async def subscribe_telegram_alerts(req: TelegramAlertRequest) -> dict[str, Any]:
    """Subscribe a bot user's chat to real events (no external URL needed).

    Used by the RugMunchBot's /alert and /watch commands. Creates internal
    ``internal://telegram/{user_id}`` subscriptions; the webhook engine
    routes matching events to the bot's Redis alert queue instead of an
    HTTP POST, and the bot's ``deliver_pending_alerts`` job pushes them as
    formatted Telegram alerts.
    """
    from app.domains.scanner.webhooks.telegram_bridge import subscribe_telegram_user

    sub = await subscribe_telegram_user(
        user_id=req.user_id,
        event_types=req.event_types,
        token_addresses=req.token_addresses,
        chains=req.chains,
        price_change_pct=req.price_change_pct,
        min_value_usd=req.min_value_usd,
    )
    if sub is None:
        raise HTTPException(status_code=503, detail="Telegram alert subscription failed (Redis unavailable)")
    return {"status": "subscribed", "user_id": req.user_id, "subscription": sub.model_dump(mode="json")}


@router.delete("/telegram", response_model=dict[str, Any])
async def unsubscribe_telegram_alerts(req: TelegramAlertRequest) -> dict[str, Any]:
    """Unsubscribe a bot user's chat from events for the given scope."""
    from app.domains.scanner.webhooks.telegram_bridge import unsubscribe_telegram_user

    removed = await unsubscribe_telegram_user(
        user_id=req.user_id,
        event_types=req.event_types,
        token_addresses=req.token_addresses,
    )
    return {"status": "unsubscribed", "user_id": req.user_id, "removed": removed}
