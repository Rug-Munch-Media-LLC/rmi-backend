"""Event Bus — typed async event system for service-to-service communication.

Rather than calling services directly, publish events. Services subscribe to the
events they care about. This decouples producers from consumers and makes the
system more resilient.

Architecture
────────────
Events use Redis pub/sub (async) as the transport layer. Each event is:
  1. A typed dataclass/Pydantic model in ``app.core.events``
  2. Published to a Redis channel matching the event type name
  3. Received by zero or more subscribers registered at startup

Standard event types:
  - ScanCompleted(token_address, chain, scan_id, safety_score)
  - WalletUpdated(wallet_address, change_type, diff)
  - NewsIngested(article_id, source, entities)
  - AlertTriggered(alert_id, token_address, event_type, severity)
  - PaymentReceived(payment_id, chain, amount_usd, payer)

Usage
─────
  Produce::
      from app.core.events import get_event_bus, ScanCompleted

      bus = get_event_bus()
      await bus.publish(ScanCompleted(token_address="...", chain="solana", ...))

  Consume::
      from app.core.events import get_event_bus, ScanCompleted

      bus = get_event_bus()
      async def on_scan(event: ScanCompleted):
          logger.info("scan done", token=event.token_address)

      bus.subscribe(ScanCompleted, on_scan)

DO NOT:
  - Publish events synchronously (use await)
  - Subscribe to events in router modules (use lifespan hooks)
  - Send sensitive data in event payloads (events may persist in Redis)
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Type helpers ──────────────────────────────────────────────────────────────

T = TypeVar("T", bound="BaseEvent")

EventHandler = Callable[[T], Coroutine[Any, Any, None]]


# ── Base event ────────────────────────────────────────────────────────────────


@dataclass
class BaseEvent:
    """Every event carries a timestamp and event_id.

    Subclasses add domain-specific fields.
    """

    event_id: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()
        if not self.event_id:
            import uuid

            self.event_id = str(uuid.uuid4())[:16]

    @property
    def channel(self) -> str:
        """Redis pub/sub channel name: ``rmi:event:{ClassName}``."""
        return f"rmi:event:{type(self).__name__}"


# ── Standard event types ─────────────────────────────────────────────────────


@dataclass
class ScanCompleted(BaseEvent):
    """Emitted when a token scan finishes."""

    token_address: str = ""
    chain: str = ""
    scan_id: str = ""
    safety_score: float = 0.0
    risk_level: str = ""
    risk_flags: list[str] = field(default_factory=list)
    modules_run: list[str] = field(default_factory=list)


@dataclass
class WalletUpdated(BaseEvent):
    """Emitted when wallet data changes significantly."""

    wallet_address: str = ""
    change_type: str = ""  # "balance", "label", "cluster", "portfolio"
    diff: dict[str, Any] = field(default_factory=dict)


@dataclass
class NewsIngested(BaseEvent):
    """Emitted when a news article is ingested and clustered."""

    article_id: str = ""
    source: str = ""
    title: str = ""
    entities: list[str] = field(default_factory=list)
    cluster_id: str = ""


@dataclass
class AlertTriggered(BaseEvent):
    """Emitted when an alert condition is met."""

    alert_id: str = ""
    token_address: str = ""
    event_type: str = ""
    severity: str = "info"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaymentReceived(BaseEvent):
    """Emitted when an x402 payment is confirmed."""

    payment_id: str = ""
    chain: str = ""
    amount_usd: float = 0.0
    payer_address: str = ""
    tool_id: str = ""


# ── Event bus ────────────────────────────────────────────────────────────────


class EventBus:
    """Async pub/sub event bus backed by Redis.

    Subscribers are registered per event type and called when an event
    of that type is published. If Redis is unavailable, subscribers are
    called in-process (synchronous fallback).

    Thread-safe for registration. Publish is async-safe.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, list[EventHandler[Any]]] = {}
        self._running_listeners: set[str] = set()

    # ── Registration ─────────────────────────────────────────────────────

    def subscribe(self, event_type: type[T], handler: EventHandler[T]) -> None:
        """Register *handler* to be called when *event_type* is published.

        Handlers are async functions that accept a single event argument.
        Multiple handlers per event type are supported.
        """
        key = event_type.__name__
        if key not in self._subscriptions:
            self._subscriptions[key] = []
        self._subscriptions[key].append(handler)
        logger.debug("event_subscriber_registered", event_type=key)

    def unsubscribe(self, event_type: type[T], handler: EventHandler[T]) -> None:
        """Remove a previously registered handler."""
        key = event_type.__name__
        if key in self._subscriptions:
            self._subscriptions[key] = [h for h in self._subscriptions[key] if h is not handler]
            if not self._subscriptions[key]:
                del self._subscriptions[key]
        logger.debug("event_subscriber_unregistered", event_type=key)

    # ── Publishing ───────────────────────────────────────────────────────

    async def publish(self, event: BaseEvent) -> None:
        """Publish an event to all subscribers + Redis pub/sub.

        1. Calls in-process subscribers immediately.
        2. Publishes to Redis so other processes can react.
        3. Failures in one subscriber do NOT block others.

        Returns immediately after dispatching (fire-and-forget for Redis).
        """
        key = type(event).__name__
        logger.debug("event_publishing", event_type=key, event_id=event.event_id)

        # 1. In-process subscribers
        handlers = self._subscriptions.get(key, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "event_handler_failed",
                    event_type=key,
                    handler=handler.__name__,
                )

        # 2. Redis broadcast
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r is not None:
                payload = _serialize(event)
                channel = event.channel
                await r.publish(channel, payload)
        except Exception:
            logger.warning("event_redis_publish_failed", event_type=key)

    # ── Redis listener (for cross-process consumption) ───────────────────

    async def listen_forever(self, event_type: type[T], handler: EventHandler[T]) -> None:
        """Listen on a Redis pub/sub channel for *event_type* and call *handler*.

        Runs forever (until the event loop is cancelled).  Each call starts a
        long-lived Redis connection.  Only one listener per event type is
        allowed to avoid duplicate processing.

        Typically started in ``app.lifespan`` on worker processes.
        """
        key = event_type.__name__
        if key in self._running_listeners:
            logger.warning("event_listener_already_running", event_type=key)
            return

        self._running_listeners.add(key)
        channel = event_type.__name__  # no prefix for listening side

        try:
            import asyncio

            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r is None:
                logger.error("event_listener_redis_unavailable", event_type=key)
                self._running_listeners.discard(key)
                return

            pubsub = r.pubsub()
            await pubsub.subscribe(channel)
            logger.info("event_listener_started", event_type=key, channel=channel)

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    event = _deserialize(event_type, data)
                    await handler(event)
                except Exception:
                    logger.exception(
                        "event_listener_handler_failed",
                        event_type=key,
                    )
        except asyncio.CancelledError:
            logger.info("event_listener_cancelled", event_type=key)
        except Exception:
            logger.exception("event_listener_error", event_type=key)
        finally:
            self._running_listeners.discard(key)


# ── Serialization ────────────────────────────────────────────────────────────


def _serialize(event: BaseEvent) -> str:
    """Serialize a dataclass event to JSON."""
    data = asdict(event)
    data["_type"] = type(event).__name__
    return json.dumps(data)


def _deserialize(event_type: type[T], data: dict[str, Any]) -> T:
    """Deserialize a dict to an event instance."""
    clean = {k: v for k, v in data.items() if k != "_type"}
    return event_type(**clean)


# ── Singleton ────────────────────────────────────────────────────────────────

_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the application-wide EventBus singleton.

    Created once, reused for the lifetime of the process.
    """
    global _bus
    if _bus is None:
        _bus = EventBus()
        logger.info("event_bus_initialized")
    return _bus


def reset_event_bus() -> None:
    """Reset the EventBus singleton — used in test teardown."""
    global _bus
    _bus = None


# ── Startup helper ──────────────────────────────────────────────────────────


def register_core_subscribers() -> None:
    """Register built-in event subscribers.

    Called during app startup (``app.lifespan``).  Connects event producers
    to event consumers that live in the same process.

    Example registration::

        bus = get_event_bus()
        bus.subscribe(ScanCompleted, on_scan_completed)
    """
    # Import subscribers lazily to avoid circular imports at module level
    # Core subscribers are lightweight — they log events or trigger local side effects.

    bus = get_event_bus()

    # Log all events for observability
    async def _log_event(event: BaseEvent) -> None:
        logger.debug(
            "event_observed",
            event_type=type(event).__name__,
            event_id=event.event_id,
        )

    # Subscribe to all standard events for logging
    for evt_type in [ScanCompleted, WalletUpdated, NewsIngested, AlertTriggered, PaymentReceived]:
        bus.subscribe(evt_type, _log_event)
