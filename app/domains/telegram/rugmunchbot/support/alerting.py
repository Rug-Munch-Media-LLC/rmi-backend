"""RugMunchBot failure-alert shim (fire-and-forget n8n webhook).

Second migration step toward rugmunchbot's own repository (next to
``support/logging.py``): provides a zero-dependency-on-``app.core`` way to
surface live degradations that would otherwise only appear as log lines.

The scheduler and the news lanes fail open by contract — a channel tick
error, a headless-client skip, a Postgres outage during news persistence,
or a dead/unreachable news-channel username all log and the JobQueue keeps
running. Those log-only errors were silent in production: nobody pages an
operator when ClickHouse/Neo4j/Postgres auth fails or a news channel goes
dead. ``notify_failure`` POSTs one JSON event to an n8n webhook
(``RMI_ALERT_WEBHOOK_URL``) so the fleet can route it to Telegram / email /
whatever n8n is wired to.

Guarantees:
* **No-op when unconfigured** — ``RMI_ALERT_WEBHOOK_URL`` unset/empty means
  the helper returns immediately (safe default; no network, no task).
* **Fire-and-forget** — the POST runs in an ``asyncio`` task created from
  the running loop; callers never await it and the tick never blocks on it.
* **Never raises** — every failure path (no loop, schedule failure, HTTP
  error, timeout) logs at warning and returns. An alerting outage can never
  crash a channel job.
* **Bounded** — the HTTP call has a 5s timeout.

Usage::

    from app.domains.telegram.rugmunchbot.support.alerting import notify_failure

    notify_failure("channel_job_failed", f"job={job_name} error={exc}")

Env var is documented in ``app/domains/telegram/rugmunchbot/config.py``
(``RMI_ALERT_WEBHOOK_URL``) and ``.env.example``.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import httpx

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.alerting")

#: Strong references to in-flight alert POST tasks so they are never GC'd
#: mid-flight (fire-and-forget, discarded on completion).
_BG_TASKS: set[asyncio.Task[object]] = set()

#: Env var holding the n8n webhook URL. Read at call time (not import) so the
#: no-op default is trivially testable and a misconfigured deployment simply
#: disables alerting. Mirrored as a lazy config name in config.py.
_ALERT_WEBHOOK_ENV = "RMI_ALERT_WEBHOOK_URL"

#: Bounded HTTP call: 5s total timeout, same ballpark as the scheduler's
#: post retry backoff cap. A slow/failing webhook must never stall the tick.
_REQUEST_TIMEOUT = 5.0


async def _post_failure(url: str, event: str, detail: str) -> None:
    """POST one JSON event to the webhook. Never raises (bounded, logged)."""
    payload = {
        "event": event,
        "detail": detail,
        "ts": datetime.now(UTC).isoformat(),
        "source": "rugmunchbot",
    }
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            await client.post(url, json=payload)
        logger.info("alerting:notified event=%s", event)
    except Exception as exc:  # alerting must never raise out of the task
        logger.warning("alerting:notify_failed event=%s err=%s", event, str(exc)[:200])


def notify_failure(event: str, detail: str) -> None:
    """Schedule a fire-and-forget failure alert to the n8n webhook.

    No-op when ``RMI_ALERT_WEBHOOK_URL`` is unset/empty (safe default).
    When configured, spawns one ``asyncio`` task that POSTs
    ``{"event", "detail", "ts", "source"}`` with a 5s timeout. Never raises:
    without a running event loop, or when the task cannot be scheduled, the
    alert is dropped with a warning log.
    """
    url = os.getenv(_ALERT_WEBHOOK_ENV, "").strip()
    if not url:
        return  # unconfigured → silent no-op (safe default)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("alerting:no_running_loop dropping event=%s", event)
        return
    try:
        task = loop.create_task(_post_failure(url, event, detail))
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
    except Exception as exc:  # never raise out of the caller
        logger.warning("alerting:schedule_failed event=%s err=%s", event, str(exc)[:200])


__all__ = ["notify_failure"]
