"""Service - business logic for alert subscriptions and event firing.

This is the only thing the api/ layer should call. It composes
the repository (storage) and the broadcaster (WebSocket push).
No FastAPI. No HTTP. Pure Python.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import builtins

from app.core.base_service import BaseService
from app.core.logging import get_logger
from app.domains.alerts.broadcaster import AlertBroadcaster
from app.domains.alerts.models import AlertEvent, AlertSubscription, AlertType, CreateAlertRequest
from app.domains.alerts.repository import AlertRepository

log = get_logger(__name__)


class AlertService(BaseService[AlertSubscription]):
    """Orchestrates subscription CRUD and event firing."""

    def __init__(
        self,
        repo: AlertRepository | None = None,
        broadcaster: AlertBroadcaster | None = None,
    ) -> None:
        self._repo = repo or AlertRepository()
        self._broadcaster = broadcaster or AlertBroadcaster()

    # ── CRUD ──────────────────────────────────────────────────────────

    async def list(self, **filters: Any) -> list[AlertSubscription]:
        """List subscriptions. If owner_id given, filter to that user."""
        owner_id = filters.get("owner_id")
        all_subs = await self._repo.list_all()
        if owner_id is None:
            return all_subs
        return [s for s in all_subs if s.owner_id == owner_id]

    async def get(self, id_: str, **kwargs: Any) -> AlertSubscription | None:
        """Get a subscription by ID."""
        return await self._repo.get(id_)

    async def create(self, data: Any, **kwargs: Any) -> AlertSubscription:
        """Create a new subscription, persist it, return the saved record."""
        req = data if isinstance(data, CreateAlertRequest) else CreateAlertRequest.model_validate(data)
        owner_id = kwargs.get("owner_id")
        types = req.alert_types or [
            AlertType.LIQUIDITY_REMOVE,
            AlertType.MINT,
            AlertType.BLACKLIST,
        ]
        sub = AlertSubscription(
            id=f"alert:{int(datetime.now(UTC).timestamp())}",
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

    async def delete(self, id_: str, **kwargs: Any) -> bool:
        """Delete a subscription by ID. Optionally verify ownership."""
        owner_id = kwargs.get("owner_id")
        sub = await self._repo.get(id_)
        if sub is None:
            return False
        if owner_id is not None and sub.owner_id != owner_id:
            log.warning(
                "alert_cancel_unauthorized",
                alert_id=id_,
                owner=owner_id,
                actual_owner=sub.owner_id,
            )
            return False
        return await self._repo.delete(id_)

    # ── Domain-specific ───────────────────────────────────────────────

    async def fire_event(
        self,
        token_address: str,
        event_type: AlertType,
        payload: dict[str, Any] | None = None,
    ) -> builtins.list[AlertEvent]:
        """Fire an event for a token. Returns the events that were broadcast."""
        subs = await self._repo.list_by_token(token_address)
        matching = [s for s in subs if s.active and (event_type.value in [t.value for t in s.alert_types])]
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
