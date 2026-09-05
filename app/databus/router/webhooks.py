"""DataBus Router sub-module. Webhooks endpoints."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import settings
from app.core.logging import get_logger

logger = get_logger("databus.router.webhooks")
router = APIRouter(tags=["databus"])

# ── Setup-route auth (RMI shared secret, enforced in-router) ─────────────
# The /webhooks prefix is public at the middleware layer (external webhook
# receivers authenticate per-service), so the admin setup route gates itself.
_SETUP_AUTH_TOKENS = {
    token
    for token in (
        getattr(settings, "rmi_auth_token", None),
        getattr(settings, "rmi_internal_key", None),
    )
    if token
}


def _setup_authorized(request: Request) -> bool:
    """Accept X-API-Key or Authorization: Bearer against the RMI tokens."""
    token = request.headers.get("X-API-Key", "") or request.headers.get("Authorization", "").replace("Bearer ", "")
    return bool(token) and token in _SETUP_AUTH_TOKENS  # nosemgrep: nan-injection bool() cannot produce NaN (FP)


def _require_setup_auth(request: Request) -> None:
    """FastAPI dependency: setup endpoints require RMI_AUTH_TOKEN auth."""
    if not _setup_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized — RMI_AUTH_TOKEN required")


# ── SSRF guard for user-supplied webhook_url ─────────────────────────────
async def _reject_non_public_url(webhook_url: str) -> None:
    """Reject URLs that point at non-public (internal) addresses.

    Blocks loopback, private, link-local, multicast, unspecified and other
    reserved IPs — including addresses reached via DNS resolution of the
    hostname — so an attacker cannot register callbacks into our network.
    """
    if not webhook_url:
        raise HTTPException(status_code=422, detail="webhook_url is required")
    try:
        parsed = urlsplit(webhook_url)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid webhook_url") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=422, detail="webhook_url must be an absolute http(s) URL")
    hostname = parsed.hostname
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror:
        raise HTTPException(status_code=422, detail="webhook_url hostname does not resolve") from None
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_unspecified
            or addr.is_reserved
        ):
            raise HTTPException(
                status_code=422,
                detail="webhook_url must point to a public IP address (internal/private targets blocked)",
            )


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
    except (AttributeError, ImportError):
        payload = {}

    result = await handle_webhook(service, payload, headers, raw_body)

    if "error" in result and result.get("status") != "duplicate":
        # Signature/auth failures are client-side auth errors (401); the rest 400.
        raise HTTPException(401 if result.get("status") == "rejected" else 400, result["error"])

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
async def setup_webhook_endpoint(service: str, request: Request, _: None = Depends(_require_setup_auth)) -> Any:
    """Programmatically register a webhook with a service.

    Auth: X-API-Key / Bearer must match RMI_AUTH_TOKEN (or RMI_INTERNAL_KEY).
    SSRF guard: webhook_url must resolve to a public IP.

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
    await _reject_non_public_url(body.get("webhook_url", ""))
    result = await setup_webhook(
        service=service,
        webhook_url=body.get("webhook_url", ""),
        events=body.get("events"),
        api_key=body.get("api_key", ""),
        addresses=body.get("addresses", []),
        chains=body.get("chains", []),
    )
    return result
