"""Publish a health alert when a host threshold is crossed.

pages and a recovery must hold before it is announced, so load oscillating
across the warn/crit line is one incident. Ported from alert-webhook.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

from .health_emit import emit
from .health_judge import judge
from .health_read import sample
from .health_render import render
from .health_severity import rate
from .health_store import open_store
from .health_timing import DEFAULT as TIMING
from .health_verdict import LEVEL_CODE

if TYPE_CHECKING:
    from collections.abc import Callable

    from .health_state import HealthStore

logger = get_logger("adminbot.notify.health_publish")


async def publish(
    store: HealthStore | None = None,
    rating: Callable[[dict[str, float]], tuple[str, str]] = rate,
    outbox: list[dict] | None = None,
) -> tuple[str, str]:
    """Publish a settled transition only; store/rating/outbox are DI seams."""
    severity, reason = rating(sample())
    status = "resolved" if severity == "ok" else "firing"
    active: Any = store if store is not None else open_store()
    if not await active.claim():
        return severity, reason
    now = await active.now()
    state = await active.load(now)
    if judge(state, severity, status, now, TIMING):
        await emit(render(severity, status, reason), outbox)
        state.last_sent = now
        state.last_escalation = LEVEL_CODE.get(severity, 0.0) if status == "firing" else 0.0
    await active.save(state, status)
    logger.info("health_alert severity=%s status=%s", severity, status)
    return severity, reason
