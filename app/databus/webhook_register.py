"""Outbound webhook subscriptions — API registration and listing."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.databus.vault import resolve_key
from app.databus.webhook_config import _REDIS_ERRORS, _WEBHOOK_ERRORS, _redis, logger


async def setup_webhook(service: str, webhook_url: str, events: list[str] | None = None, **kw: Any) -> dict[str, Any]:
    """Programmatically set up a webhook subscription with a service.

    For services that support API-based webhook registration (Helius, Moralis),
    this auto-configures the webhook URL and event filters.
    """
    api_key = kw.get("api_key", "")

    if service == "helius":
        if not api_key:
            api_key = resolve_key("HELIUS_API_KEY")
        if not api_key:
            return {"error": "HELIUS_API_KEY required"}
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                resp = await c.post(
                    f"https://mainnet.helius-rpc.com/v0/webhooks?api-key={api_key}",
                    json={
                        "webhookURL": webhook_url,
                        "transactionTypes": events or ["ANY"],
                        "accountAddresses": kw.get("addresses", []),
                        "webhookType": "enhanced",
                    },
                )
                return resp.json() if resp.status_code == 200 else {"error": resp.text[:200]}
        except _WEBHOOK_ERRORS as exc:
            logger.warning("webhook_register_failed", service=service, error=str(exc))
            return {"error": str(exc)}

    elif service == "moralis":
        if not api_key:
            api_key = resolve_key("MORALIS_API_KEY")
        if not api_key:
            return {"error": "MORALIS_API_KEY required"}
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                resp = await c.post(
                    "https://authapi.moralis.io/streams/evm",
                    headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                    json={
                        "webhookUrl": webhook_url,
                        "description": f"RMI DataBus webhook for {service}",
                        "tag": "rmi-databus",
                        "chains": kw.get("chains", ["eth", "bsc", "polygon"]),
                        "includeNativeTxs": True,
                    },
                )
                return resp.json() if resp.status_code in (200, 201) else {"error": resp.text[:200]}
        except _WEBHOOK_ERRORS as exc:
            logger.warning("webhook_register_failed", service=service, error=str(exc))
            return {"error": str(exc)}

    return {
        "status": "manual_setup_required",
        "service": service,
        "webhook_url": webhook_url,
        "instructions": f"Configure webhook at {service} dashboard to point to {webhook_url}",
    }


async def list_webhooks() -> dict[str, Any]:
    """List all configured webhook subscriptions."""
    try:
        r = _redis()
        keys = r.keys("webhook:*:*")
        webhooks = []
        for key in keys[:50]:
            data = r.get(key)
            if data:
                webhooks.append(json.loads(data))
        r.close()
        return {"webhooks": webhooks, "total": len(webhooks)}
    except _REDIS_ERRORS as exc:
        logger.warning("webhook_list_failed", error=str(exc))
        return {"webhooks": [], "total": 0}
