"""Service — business logic for alert subscriptions and event firing.

This is the only thing the api/ layer should call. It composes
the repository (storage) and the broadcaster (WebSocket push).
No FastAPI. No HTTP. Pure Python.
"""
from __future__ import annotations

from datetime import datetime

from app.core.logging import get_logger
from app.domain.alerts.broadcaster import AlertBroadcaster
from app.domain.alerts.models import (
    AlertEvent,
    AlertSubscription,
    AlertType,
    CreateAlertRequest,
)
from app.domain.alerts.repository import AlertRepository

log = get_logger(__name__)


class AlertService:
    """Orchestrates subscription CRUD and event firing."""

    def __init__(
        self,
        repo: AlertRepository | None = None,
        broadcaster: AlertBroadcaster | None = None,
    ) -> None:
        self._repo = repo or AlertRepository()
        self._broadcaster = broadcaster or AlertBroadcaster()

    async def create_subscription(
        self,
        req: CreateAlertRequest,
        owner_id: str | None = None,
    ) -> AlertSubscription:
        """Create a new subscription, persist it, return the saved record."""
        # Use provided types, or the model default if not given.
        types = req.alert_types or [
            AlertType.LIQUIDITY_REMOVE,
            AlertType.MINT,
            AlertType.BLACKLIST,
        ]
        sub = AlertSubscription(
            id=f"alert:{int(datetime.utcnow().timestamp())}",
            token_address=req.token_address,
            alert_types=types,
            webhook_url=req.webhook_url,
            owner_id=owner_id,
        )
        await self._repo.create(sub)
        log.info(
            "alert_subscription_created",
            alert_id=sub.id,
            token=sub.token_address,
            types=[t.value for t in sub.alert_types],
            owner=owner_id,
        )
        return sub

    async def list_subscriptions(self, owner_id: str | None = None) -> list[AlertSubscription]:
        """List subscriptions. If owner_id given, filter to that user."""
        all_subs = await self._repo.list_all()
        if owner_id is None:
            return all_subs
        return [s for s in all_subs if s.owner_id == owner_id]

    async def cancel_subscription(self, alert_id: str, owner_id: str | None = None) -> bool:
        """Cancel (delete) a subscription. Returns True if removed."""
        sub = await self._repo.get(alert_id)
        if sub is None:
            return False
        if owner_id is not None and sub.owner_id != owner_id:
            log.warning(
                "alert_cancel_unauthorized",
                alert_id=alert_id,
                owner=owner_id,
                actual_owner=sub.owner_id,
            )
            return False
        return await self._repo.delete(alert_id)

    async def fire_event(
        self,
        token_address: str,
        event_type: AlertType,
        payload: dict | None = None,
    ) -> list[AlertEvent]:
        """Fire an event for a token. Returns the events that were broadcast.

        Looks up all active subscriptions matching this token + event_type,
        creates an AlertEvent for each, and broadcasts via the broadcaster.
        """
        subs = await self._repo.list_by_token(token_address)
        matching = [
            s for s in subs
            if s.active and (event_type.value in [t.value for t in s.alert_types])
        ]
        if not matching:
            return []

        events: list[AlertEvent] = []
        for sub in matching:
            ev = AlertEvent(
                subscription_id=sub.id,
                token_address=token_address,
                event_type=event_type,
                payload=payload or {},
                severity=_severity_for(event_type),
            )
            events.append(ev)
            await self._broadcaster.broadcast_event(ev)
        log.info(
            "alert_events_fired",
            token=token_address,
            event_type=event_type.value,
            count=len(events),
        )
        return events


def _severity_for(event_type: AlertType) -> str:
    """Map event type → severity (used by clients to choose alert UI)."""
    critical = {AlertType.RUG_PULL, AlertType.HONEYPOT, AlertType.BLACKLIST}
    warning = {AlertType.LIQUIDITY_REMOVE, AlertType.WHALE_MOVEMENT, AlertType.DEPLOYER_SELL}
    if event_type in critical:
        return "critical"
    if event_type in warning:
        return "warning"
    return "info"
