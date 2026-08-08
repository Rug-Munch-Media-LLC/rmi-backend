#!/usr/bin/env python3
"""#4 - Whale Wallet Alert System. Monitors large transfers across all chains.

Users subscribe to addresses, get instant Telegram alerts on >$X movements."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel

from app.config import settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/whale-alerts", tags=["whale-alerts"])

BACKEND = settings.backend_url
TG_BOT = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# In-memory subscription store (Redis in prod)
_subscriptions: dict[str, list[dict[str, Any]]] = {}  # user_id -> list of alert rules


class WhaleAlert(BaseModel):
    user_id: str
    address: str
    chain: str
    threshold_usd: float = 100000  # trigger on >$100K
    notify_telegram: bool = True
    label: str = ""


class WhaleAlertResponse(BaseModel):
    id: str
    user_id: str
    address: str
    chain: str
    threshold_usd: float
    active: bool


@router.post("/subscribe")
async def subscribe(alert: WhaleAlert) -> dict[str, Any]:
    """Subscribe to whale alerts for an address."""
    sub_id = f"{alert.user_id}:{alert.address}:{alert.chain}"
    entry = {
        "id": sub_id,
        "user_id": alert.user_id,
        "address": alert.address,
        "chain": alert.chain,
        "threshold_usd": alert.threshold_usd,
        "notify_telegram": alert.notify_telegram,
        "label": alert.label,
        "active": True,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _subscriptions.setdefault(alert.user_id, []).append(entry)
    return {"id": sub_id, "status": "subscribed"}


@router.delete("/subscribe/{alert_id}")
async def unsubscribe(alert_id: str) -> dict[str, Any]:
    """Remove a whale alert subscription."""
    for _uid, subs in _subscriptions.items():
        for s in subs[:]:
            if s["id"] == alert_id:
                subs.remove(s)
                return {"id": alert_id, "status": "unsubscribed"}
    raise NotFoundError("Alert not found")


@router.get("/subscriptions/{user_id}")
async def list_subscriptions(user_id: str) -> dict[str, Any | None]:
    """List all whale alert subscriptions for a user."""
    return {"subscriptions": _subscriptions.get(user_id, [])}


@router.get("/check/{address}")
async def check_transfer(address: str, chain: str = Query("ethereum"), min_usd: float = Query(50000)) -> dict[str, Any]:
    """Check recent large transfers for an address (on-demand scan)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{BACKEND}/api/v1/databus/{chain}/transactions/{address}")
            if resp.status_code != 200:
                return {
                    "address": address,
                    "chain": chain,
                    "transfers": [],
                    "error": "chain unavailable",
                }
            txs = resp.json().get("data", {}).get("transactions", [])
            large = [t for t in txs if float(t.get("value_usd", 0) or 0) > min_usd]
            return {"address": address, "chain": chain, "transfers": large, "count": len(large)}
    except Exception as e:
        return {"address": address, "chain": chain, "transfers": [], "error": str(e)}


async def _send_telegram_alert(chat_id: str, text: str) -> None:
    """Send whale alert to Telegram."""
    if not TG_BOT:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{TG_BOT}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
    except (httpx.HTTPError, TimeoutError) as e:
        logger.warning(f"Telegram alert send failed: {e}")


@router.post("/scan")
async def scan_whale_activity(background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Trigger a scan of all subscribed addresses. Cron-friendly."""
    alerts_fired = 0
    for uid, subs in _subscriptions.items():
        for sub in subs:
            if not sub["active"]:
                continue
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{BACKEND}/api/v1/databus/{sub['chain']}/transactions/{sub['address']}")
                    if resp.status_code != 200:
                        continue
                    txs = resp.json().get("data", {}).get("transactions", [])
                    for tx in txs:
                        value = float(tx.get("value_usd", 0) or 0)
                        if value > sub["threshold_usd"]:
                            alerts_fired += 1
                            text = (
                                f"🐋 <b>WHALE ALERT</b>\n\n"
                                f"<b>Address:</b> {sub['address'][:10]}...\n"
                                f"<b>Chain:</b> {sub['chain'].upper()}\n"
                                f"<b>Amount:</b> ${value:,.0f}\n"
                                f"<b>Label:</b> {sub['label'] or 'Unlabeled'}\n"
                                f"<b>Tx:</b> {tx.get('hash', '?')[:20]}..."
                            )
                            background_tasks.add_task(_send_telegram_alert, uid, text)
            except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
                logger.debug(f"Whale scan failed for {sub['address']}: {e}")
                continue
    return {
        "alerts_fired": alerts_fired,
        "subscriptions_checked": sum(len(s) for s in _subscriptions.values()),
    }
