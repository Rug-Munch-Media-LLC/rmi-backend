"""Wallet Monitor — Alert Channel."""
from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime

import httpx

from app.domains.wallet.monitor.config import CONFIG, get_redis

class AlertChannel:
    """Distributes alerts to Redis pub/sub and optional webhook callbacks."""

    INTEL_CHANNEL = "wallet:intel"
    ALERT_CHANNEL = "wallet:alerts"

    @staticmethod
    async def publish_intel(payload: dict) -> None:
        r = await get_redis()
        if r:
            await r.publish(AlertChannel.INTEL_CHANNEL, json.dumps(payload))

    @staticmethod
    async def publish_alert(alert: dict) -> None:
        r = await get_redis()
        if r:
            await r.publish(AlertChannel.ALERT_CHANNEL, json.dumps(alert))
            alert_id = f"wallet:alert:{alert.get('chain')}:{alert.get('tx_hash')}:{datetime.now(UTC).timestamp()}"
            await r.setex(alert_id, CONFIG["ALERT_TTL_SECONDS"], json.dumps(alert))

    @staticmethod
    async def dispatch_webhooks(alert: dict, webhooks: list[str]) -> None:
        if not webhooks: return
        async with httpx.AsyncClient(timeout=CONFIG["WEBHOOK_TIMEOUT"]) as client:
            for url in webhooks:
                with contextlib.suppress(Exception):
                    await client.post(url, json=alert)
