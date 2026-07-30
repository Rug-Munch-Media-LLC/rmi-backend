"""Webhook engine — poll-based event detection and dispatch orchestrator.

Architecture::
  WebhookEngine.start()
    ├── For each provider in PROVIDER_REGISTRY:
    │     └── asyncio.create_task(_poll_loop(provider))
    │           ├── Every N seconds:
    │           │     ├── Load cursor from Redis
    │           │     ├── provider.poll(cursor)
    │           │     ├── For each event:
    │           │     │     ├── Find matching subscriptions
    │           │     │     └── _dispatch(event, sub)
    │           │     │           └── delivery_service.deliver(event, sub)
    │           │     └── Save updated cursor to Redis
    │           └── asyncio.sleep(poll_interval)

All state is in Redis (subscriptions + cursors).  Engine can be stopped
and restarted without losing position.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.logging import get_logger
from app.core.redis import get_redis_async
from app.domains.scanner.webhooks.delivery import WebhookDeliveryService
from app.domains.scanner.webhooks.models import (
    WebhookEvent,
    WebhookEventType,
    WebhookSubscription,
)
from app.domains.scanner.webhooks.providers import PROVIDER_REGISTRY
from app.domains.scanner.webhooks.providers.utils import load_cursor, save_cursor
from app.domains.scanner.webhooks.repository import WebhookRepository

log = get_logger(__name__)


class WebhookEngine:
    """Poll-based webhook engine — zero external dependencies.

    Usage::

        engine = WebhookEngine()
        await engine.start()  # starts background tasks
        # ...
        await engine.stop()   # cancels background tasks
    """

    def __init__(
        self,
        repo: WebhookRepository | None = None,
        delivery_service: WebhookDeliveryService | None = None,
    ) -> None:
        self._repo = repo or WebhookRepository()
        self._delivery = delivery_service or WebhookDeliveryService()
        self._tasks: list[asyncio.Task[Any]] = []
        self._running = False

    async def start(self) -> None:
        """Start background poll loops for every registered provider."""
        if self._running:
            log.warning("webhook_engine_already_running")
            return
        self._running = True

        for provider_cls in PROVIDER_REGISTRY:
            provider = provider_cls()
            task = asyncio.create_task(
                self._poll_loop(provider),
                name=f"wh-poll-{provider.event_type.value}",
            )
            self._tasks.append(task)
            log.info(
                "webhook_poll_started",
                event_type=provider.event_type.value,
                interval=provider.poll_interval,
            )

        log.info("webhook_engine_started", provider_count=len(self._tasks))

    async def stop(self) -> None:
        """Cancel all background poll loops."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("webhook_engine_stopped")

    async def _poll_loop(self, provider: Any) -> None:
        """Run an infinite polling loop for a single provider.

        Loads cursor → polls → dispatches → saves cursor → sleeps.
        """
        while self._running:
            try:
                cursor = await load_cursor(await provider.cursor_key())
                raw_events = await provider.poll(cursor)

                if raw_events:
                    sub_map = await self._build_subscription_map(provider.event_type)
                    dispatched = 0

                    for raw_event in raw_events:
                        event_id = f"evt:{int(time.time())}:{uuid4().hex[:8]}"
                        event = WebhookEvent(
                            id=event_id,
                            event_type=provider.event_type,
                            payload=raw_event,
                            chain=raw_event.get("chain", "unknown"),
                        )

                        # Dispatch to matching subscriptions
                        for sub in sub_map.get(raw_event.get("token_address", "").lower(), []):
                            if self._matches_filters(raw_event, sub):
                                await self._dispatch(event, sub)
                                dispatched += 1

                        # Also dispatch to subscriptions without token filter (catch-all)
                        for sub in sub_map.get("*", []):
                            if self._matches_filters(raw_event, sub):
                                await self._dispatch(event, sub)
                                dispatched += 1

                    log.info(
                        "webhook_poll_dispatched",
                        event_type=provider.event_type.value,
                        events_found=len(raw_events),
                        dispatched=dispatched,
                    )

                # Save cursor — use last event timestamp if available
                if raw_events and len(raw_events) > 0:
                    last_event = raw_events[-1]
                    new_cursor = self._build_cursor(cursor, last_event)
                    await save_cursor(await provider.cursor_key(), new_cursor)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(
                    "webhook_poll_error",
                    event_type=provider.event_type.value,
                    error=str(exc),
                )

            await asyncio.sleep(provider.poll_interval)

    async def _build_subscription_map(self, event_type: WebhookEventType) -> dict[str, list[WebhookSubscription]]:
        """Build a map: token_address_lower → [subscriptions].

        The catch-all key "*" holds subscriptions that watch all tokens.
        """
        subs = await self._repo.list_by_event_type(event_type.value)
        sub_map: dict[str, list[WebhookSubscription]] = {}
        for sub in subs:
            token_filters = sub.filters.token_addresses if sub.filters else []
            if token_filters:
                for addr in token_filters:
                    key = addr.lower()
                    sub_map.setdefault(key, []).append(sub)
            else:
                sub_map.setdefault("*", []).append(sub)
        return sub_map

    async def _dispatch(self, event: WebhookEvent, subscription: WebhookSubscription) -> None:
        """Dispatch an event to a single subscriber."""
        event.subscription_id = subscription.id
        await self._delivery.deliver(event, subscription)

        # Update subscription's last_triggered_at
        subscription.last_triggered_at = datetime.now(UTC)
        try:
            await self._repo.update(subscription)
        except Exception as exc:
            log.warning("webhook_update_last_triggered_failed", error=str(exc))

    def _matches_filters(self, raw_event: dict[str, Any], subscription: WebhookSubscription) -> bool:
        """Check if an event payload matches subscription filters."""
        filters = subscription.filters
        if not filters:
            return True

        # Chain filter
        if filters.chains:
            event_chain = raw_event.get("chain", "").lower()
            if event_chain and event_chain not in [c.lower() for c in filters.chains]:
                return False

        # Token address filter (already handled in _build_subscription_map,
        # but double-check for catch-all subscriptions)
        if filters.token_addresses:
            event_token = raw_event.get("token_address", "").lower()
            if event_token and event_token not in [a.lower() for a in filters.token_addresses]:
                return False

        # USD value filter
        if filters.min_value_usd is not None:
            value = raw_event.get("value_decimal", 0)
            if isinstance(value, (int, float)) and value < filters.min_value_usd:
                return False

        return True

    @staticmethod
    def _build_cursor(
        old_cursor: dict[str, Any] | None,
        last_event: dict[str, Any],
    ) -> dict[str, Any]:
        """Build an updated cursor from the last processed event."""
        cursor: dict[str, Any] = {}
        if old_cursor:
            cursor.update(old_cursor)

        # Use block_timestamp or deployed_at as the poll checkpoint
        ts = last_event.get("block_timestamp") or last_event.get("deployed_at")
        if ts:
            cursor["since"] = str(ts)

        return cursor
