"""Webhook delivery — async HTTP POST with exponential backoff retry.

Delivery flow::
  1. POST payload to subscriber URL (3s timeout)
  2. If 2xx → mark delivered, done
  3. If timeout / 5xx → retry up to 3 times (10s, 30s, 60s backoff)
  4. If all attempts fail → mark failed, log warning

All delivery outcomes are recorded to Redis for observability.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from app.core.logging import get_logger
from app.core.redis import get_redis_async
from app.domains.scanner.webhooks.models import (
    WebhookDelivery,
    WebhookEvent,
    WebhookSubscription,
)

log = get_logger(__name__)

DELIVERY_KEY = "rmi:webhook_deliveries"
RETRY_DELAYS = [10, 30, 60]  # seconds between retries
MAX_ATTEMPTS = 3
HTTP_TIMEOUT = 10.0  # seconds


class WebhookDeliveryService:
    """Delivers webhook events to subscriber URLs with retry."""

    async def deliver(
        self,
        event: WebhookEvent,
        subscription: WebhookSubscription,
    ) -> WebhookDelivery:
        """Deliver an event to a subscriber. Returns the delivery record.

        Retries on network errors and 5xx responses with exponential backoff.
        """
        delivery = WebhookDelivery(
            event_id=event.id,
            subscription_id=subscription.id,
            url=subscription.url,
        )

        payload = self._build_payload(event)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            delivery.attempt = attempt
            delivery.last_attempt_at = datetime.now(UTC)

            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                    resp = await client.post(
                        subscription.url,
                        json=payload,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "RMI-Webhook/1.0",
                            "X-Webhook-Event": event.event_type.value,
                            "X-Webhook-ID": event.id,
                            "X-Delivery-Attempt": str(attempt),
                        },
                    )

                delivery.response_code = resp.status_code

                if 200 <= resp.status_code < 300:
                    delivery.status = "delivered"
                    await self._save_delivery(delivery)
                    log.info(
                        "webhook_delivered",
                        sub_id=subscription.id,
                        event_id=event.id,
                        status_code=resp.status_code,
                        attempt=attempt,
                    )
                    return delivery

                # 4xx — client error, no point retrying
                if 400 <= resp.status_code < 500:
                    delivery.status = "failed"
                    delivery.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    await self._save_delivery(delivery)
                    log.warning(
                        "webhook_client_error",
                        sub_id=subscription.id,
                        event_id=event.id,
                        status_code=resp.status_code,
                        error=delivery.error,
                    )
                    return delivery

                # 5xx — retry
                delivery.error = f"HTTP {resp.status_code}: {resp.text[:200]}"

            except httpx.TimeoutException:
                delivery.error = "timeout"
            except httpx.RequestError as exc:
                delivery.error = f"request_error: {exc}"
            except (ValueError, TypeError, KeyError, OSError) as exc:
                delivery.error = f"delivery_error: {exc}"
                delivery.status = "failed"
                await self._save_delivery(delivery)
                log.error(
                    "webhook_delivery_fatal",
                    sub_id=subscription.id,
                    error=str(exc),
                )
                return delivery

            if attempt < MAX_ATTEMPTS:
                delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
                log.info(
                    "webhook_retry",
                    sub_id=subscription.id,
                    attempt=attempt,
                    next_delay=delay,
                    error=delivery.error,
                )
                await asyncio.sleep(delay)

        # All attempts exhausted
        delivery.status = "failed"
        await self._save_delivery(delivery)
        log.error(
            "webhook_all_attempts_failed",
            sub_id=subscription.id,
            event_id=event.id,
            error=delivery.error,
            attempts=MAX_ATTEMPTS,
        )
        return delivery

    def _build_payload(self, event: WebhookEvent) -> dict[str, Any]:
        """Build the JSON payload sent to the subscriber."""
        return {
            "event_id": event.id,
            "event_type": event.event_type.value,
            "chain": event.chain,
            "triggered_at": event.triggered_at.isoformat(),
            "data": event.payload,
        }

    async def _save_delivery(self, delivery: WebhookDelivery) -> None:
        """Persist delivery record to Redis for observability."""
        r = get_redis_async()
        if r is None:
            return
        try:
            key = f"{DELIVERY_KEY}:{delivery.event_id}"
            await r.hset(key, mapping=delivery.model_dump(mode="json"))
            # Expire after 7 days
            await r.expire(key, 604800)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            log.warning("webhook_delivery_save_failed", error=str(exc))
