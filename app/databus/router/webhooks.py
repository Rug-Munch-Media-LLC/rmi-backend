"""DataBus Router sub-module. Webhooks endpoints."""

from fastapi import APIRouter, HTTPException, Query, Request
from typing import Any
from app.core.logging import get_logger
from app.databus.core import databus

logger = get_logger("databus.router.webhooks")
router = APIRouter(tags=["databus"])

@router.post("/webhooks/{service}")
async def receive_webhook(service: str, request: Request) -> Any:
    """Universal webhook receiver. Routes to correct handler based on service.

    Supported: arkham, helius, moralis, alchemy, custom

    Auto-validates signatures, deduplicates, caches in DataBus,
    indexes in RAG, triggers premium scanner, pushes alerts.
    """
    try:
        from app.databus.webhooks import handle_webhook
    except ImportError:
        raise HTTPException(501, "Webhook system not available") from None

    raw_body = await request.body()
    headers = dict(request.headers)

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}

    result = await handle_webhook(service, payload, headers, raw_body)

    if "error" in result and result.get("status") != "duplicate":
        raise HTTPException(400, result["error"])

    return result

@router.get("/webhooks")
async def list_webhooks_endpoint() -> Any:
    """List all recent webhook events."""
    try:
        from app.databus.webhooks import list_webhooks

        return await list_webhooks()
    except ImportError:
        raise HTTPException(501, "Webhook system not available") from None

@router.post("/webhooks/setup/{service}")
async def setup_webhook_endpoint(service: str, request: Request) -> Any:
    """Programmatically register a webhook with a service.

    Body: {
        "webhook_url": "https://rugmunch.io/api/v1/databus/webhooks/helius",
        "events": ["transaction", "token_transfer"],
        "addresses": ["0x..."],
        "api_key": "optional-override"
    }
    """
    try:
        from app.databus.webhooks import setup_webhook
    except ImportError:
        raise HTTPException(501, "Webhook system not available") from None

    body = await request.json()
    result = await setup_webhook(
        service=service,
        webhook_url=body.get("webhook_url", ""),
        events=body.get("events"),
        api_key=body.get("api_key", ""),
        addresses=body.get("addresses", []),
        chains=body.get("chains", []),
    )
    return result

