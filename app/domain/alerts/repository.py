"""Repository — async Redis storage for alert subscriptions.

Storage layout (matches legacy for cutover):
    Hash key: "rmi:alerts"
    Field:     alert id (e.g. "alert:1700000000")
    Value:     JSON-encoded AlertSubscription

This is a thin async wrapper. No business logic. Pure data access.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.redis import get_redis_async
from app.domain.alerts.models import AlertSubscription

log = get_logger(__name__)

HASH_KEY = "rmi:alerts"


class AlertRepository:
    """Async Redis-backed subscription storage."""

    def __init__(self) -> None:
        self._key = HASH_KEY

    async def list_all(self) -> list[AlertSubscription]:
        r = get_redis_async()
        raw: dict[str, str] = await r.hgetall(self._key) or {}
        out: list[AlertSubscription] = []
        for value in raw.values():
            try:
                out.append(AlertSubscription.model_validate_json(value))
            except Exception as e:
                log.warning("alert_repo_corrupt_record", error=str(e))
        return out

    async def get(self, alert_id: str) -> AlertSubscription | None:
        r = get_redis_async()
        raw: str | None = await r.hget(self._key, alert_id)
        if raw is None:
            return None
        try:
            return AlertSubscription.model_validate_json(raw)
        except Exception as e:
            log.warning("alert_repo_corrupt_record", alert_id=alert_id, error=str(e))
            return None

    async def create(self, sub: AlertSubscription) -> None:
        r = get_redis_async()
        await r.hset(self._key, sub.id, sub.model_dump_json())

    async def delete(self, alert_id: str) -> bool:
        r = get_redis_async()
        removed: int = await r.hdel(self._key, alert_id)
        return removed > 0

    async def list_by_token(self, token_address: str) -> list[AlertSubscription]:
        all_subs = await self.list_all()
        return [s for s in all_subs if s.token_address == token_address]
