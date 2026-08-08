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
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.logging import get_logger
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
            # A single failing provider must not prevent the engine (or the
            # other providers) from starting — log and continue.
            try:
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
            except Exception as exc:
                log.warning(
                    "webhook_provider_start_failed",
                    provider=provider_cls.__name__,
                    error=str(exc),
                )
                continue

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

                # Providers that watch addresses (wallet_activity,
                # enhanced_transaction) read their watch list from the cursor.
                # The engine injects it from the active subscriptions for that
                # event type so a subscription controls what is watched.
                cursor = await self._inject_watch_addresses(provider.event_type, cursor)

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

                        # First-class whale indexing — every polled whale
                        # move is upserted into the whale_transfers Qdrant
                        # collection (powers /whales + bot chat history),
                        # independent of whether any subscription matches.
                        # Runs alongside the telegram_bridge routing below;
                        # fail-open: Qdrant down never breaks the poll loop.
                        if provider.event_type is WebhookEventType.TOKEN_WHALE_MOVE:
                            try:
                                from app.domains.scanner.whale_indexer import index_whale_event

                                await index_whale_event(raw_event)
                            except Exception as exc:
                                log.warning("whale_index_failed error=%s", exc)

                        # Dispatch to matching subscriptions
                        for sub in sub_map.get(raw_event.get("token_address", "").lower(), []):
                            if self._matches_filters(raw_event, sub):
                                await self._dispatch(event, sub)
                                dispatched += 1

                        # Wallet-scoped subscriptions (wallet.activity,
                        # transaction.enhanced) route by wallet_address.
                        for sub in sub_map.get(raw_event.get("wallet_address", "").lower(), []):
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
        """Build a map: address → [subscriptions].

        Keys are lowercased token addresses (from ``filters.token_addresses``),
        wallet addresses (from ``filters.wallet_addresses``) and the catch-all
        ``"*"`` for subscriptions that watch everything.
        """
        subs = await self._repo.list_by_event_type(event_type.value)
        sub_map: dict[str, list[WebhookSubscription]] = {}
        for sub in subs:
            if not sub.filters:
                sub_map.setdefault("*", []).append(sub)
                continue
            token_filters = sub.filters.token_addresses or []
            wallet_filters = sub.filters.wallet_addresses or []
            if token_filters or wallet_filters:
                for addr in [*token_filters, *wallet_filters]:
                    key = addr.lower()
                    sub_map.setdefault(key, []).append(sub)
            else:
                sub_map.setdefault("*", []).append(sub)
        return sub_map

    async def _dispatch(self, event: WebhookEvent, subscription: WebhookSubscription) -> None:
        """Dispatch an event to a single subscriber.

        Internal ``internal://telegram/{user_id}`` subscriptions (created by
        the bot's alert wiring) are routed to the bot's Redis alert queue —
        the bot's ``deliver_pending_alerts`` job turns them into Telegram
        push alerts. Everything else goes through the HTTP delivery service.
        """
        event.subscription_id = subscription.id
        handled_internal = False
        try:
            from app.domains.scanner.webhooks.telegram_bridge import route_internal_telegram

            handled_internal = await route_internal_telegram(event, subscription)
        except Exception as exc:
            log.warning(
                "webhook_internal_routing_failed",
                sub_id=subscription.id,
                error=str(exc),
            )
        if not handled_internal:
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

        # Wallet address filter (wallet.activity, transaction.enhanced)
        if filters.wallet_addresses:
            event_wallet = raw_event.get("wallet_address", "").lower()
            event_from = raw_event.get("from_address", "").lower()
            event_to = raw_event.get("to_address", "").lower()
            watched = [a.lower() for a in filters.wallet_addresses]
            involved = {w for w in (event_wallet, event_from, event_to) if w}
            if involved and not (involved & set(watched)):
                return False

        # USD value filter
        if filters.min_value_usd is not None:
            value = raw_event.get("value_decimal", 0)
            if isinstance(value, (int, float)) and value < filters.min_value_usd:
                return False

        # Transaction type filter (transaction.enhanced)
        if filters.transaction_types:
            tx_type = raw_event.get("type", "")
            if tx_type and tx_type not in filters.transaction_types:
                return False

        # Program ID filter (transaction.enhanced — Solana programs / EVM routers)
        if filters.program_ids:
            program_ids = [str(i.get("programId", "")).lower() for i in raw_event.get("instructions", [])]
            watched_programs = [p.lower() for p in filters.program_ids]
            if program_ids and not (set(program_ids) & set(watched_programs)):
                return False

        return True

    async def _inject_watch_addresses(
        self,
        event_type: WebhookEventType,
        cursor: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Collect wallet + token watch lists from active subscriptions.

        Providers that watch specific addresses (wallet_activity,
        enhanced_transaction) read ``cursor["wallet_addresses"]`` /
        ``cursor["token_addresses"]``; without this injection they would never
        know which addresses to poll. Catch-all subscriptions are ignored —
        an address-less provider poll makes no sense and would blow up RPC
        budgets.
        """
        cursor = cursor or {}
        subs = await self._repo.list_by_event_type(event_type.value)
        wallets: set[str] = set(cursor.get("wallet_addresses") or [])
        tokens: set[str] = set(cursor.get("token_addresses") or [])
        for sub in subs:
            filters = sub.filters
            if not filters:
                continue
            wallets.update(a.lower() for a in (filters.wallet_addresses or []))
            tokens.update(a.lower() for a in (filters.token_addresses or []))
        if wallets:
            cursor["wallet_addresses"] = sorted(wallets)
        if tokens:
            cursor["token_addresses"] = sorted(tokens)
        return cursor

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
