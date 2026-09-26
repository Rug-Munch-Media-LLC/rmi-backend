"""Put one rendered alert on the wire, or into a test's outbox.

Split out so the publisher holds only the decision. Passing ``outbox`` is a
DI seam: production omits it and the alert goes to the Redis queue, a test
passes a list and the real send path is never touched.
"""

from __future__ import annotations

from typing import Any

from .queues import SYSTEM, push


async def emit(alert: dict[str, Any], outbox: list[dict] | None) -> None:
    """Deliver the alert to the outbox when given, else to the system queue."""
    if outbox is not None:
        outbox.append(alert)
        return
    await push(SYSTEM, alert)
