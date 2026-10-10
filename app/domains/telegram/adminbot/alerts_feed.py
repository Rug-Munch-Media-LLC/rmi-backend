"""Alertmanager alerts feed — closes the alert last mile for @rmiadminbot.

Background: the Alertmanager webhook (``app/api/v1/admin/alerts_webhook.py``)
rpush()es single-alert payloads onto two Redis list queues
(``rmi:alerts:system`` / ``rmi:alerts:security``). Until 2026-09-16 **no
consumer existed** — infrastructure alerts died inside Redis. This module
BLPOPs both queues from the admin-bot process and forwards formatted alerts
to ``ADMIN_CHAT_ID`` with:

* severity-aware emoji + routing footer
* fingerprint dedupe — Alertmanager re-sends firing alerts every group
  interval; a given fingerprint+status is forwarded once per window
* resolved notices pass once (and clear the dedupe entry so a re-fire pages)
* fail-open behaviour: Redis down -> log + retry, never crash the bot

Wiring: ``bot.py`` ``_post_init`` starts :func:`start_alerts_feed` as an
asyncio task (kept referenced alongside the RAG listener task).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from app.core.logging import get_logger
from app.domains.telegram.adminbot.alerts_render import (
    QUEUE_KEYS,
    SECURITY_QUEUE as SECURITY_QUEUE,  # explicit re-export (tests import from this module)
    SYSTEM_QUEUE as SYSTEM_QUEUE,  # explicit re-export
    AlertDedupe,
    _deliver,
    fingerprint as fingerprint,  # explicit re-export
    format_alert,
    redis_should_send,
)

try:  # redis-py raises its own TimeoutError (not the builtin) on idle BLPOP
    from redis.exceptions import TimeoutError as RedisTimeoutError
except Exception:  # pragma: no cover - redis unavailable at import time

    class RedisTimeoutError(Exception):  # type: ignore[no-redef]
        """Fallback when redis is not importable."""


logger = get_logger("admin_bot.alerts_feed")

POLL_TIMEOUT_SECONDS = int(os.getenv("ALERTS_FEED_POLL", "10"))
DEDUPE_WINDOW_SECONDS = int(os.getenv("ALERTS_FEED_DEDUPE", "1800"))
DEDUPE_MAX_ENTRIES = 500
DEDUPE_REDIS_PREFIX = "rmi:alerts:dedupe:"


async def start_alerts_feed(bot_app: Any) -> None:
    """Async BLPOP consumer for the Alertmanager queues. Runs forever."""
    logger.info("Alerts feed starting on queues=%s", QUEUE_KEYS)

    from app.domains.telegram.adminbot.redis_listener import (
        _build_redis_url,
        _resolve_redis_password,
    )

    redis_url = _build_redis_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        _resolve_redis_password(),
    )
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_url, decode_responses=True, protocol=2)
        await client.ping()
    except ImportError:
        logger.warning("redis.asyncio not installed — alerts feed disabled")
        return
    except Exception as exc:  # fail-open: bot keeps working without the feed
        logger.error("Alerts feed Redis connection failed: %s", exc)
        return

    dedupe = AlertDedupe()
    chat_id = int(os.environ.get("ADMIN_CHAT_ID", "0") or 0)
    if chat_id <= 0:
        logger.error("ADMIN_CHAT_ID unset — alerts feed started but cannot deliver")

    try:
        while True:
            try:
                result = await client.blpop(QUEUE_KEYS, timeout=POLL_TIMEOUT_SECONDS)
                if result is None:
                    continue
                key, raw = result
                alert = json.loads(raw)
                if not isinstance(alert, dict):
                    logger.warning("Alerts feed: non-dict payload dropped")
                    continue
                try:
                    should_send = await redis_should_send(client, alert)
                except Exception as exc:
                    logger.warning("Alerts feed Redis dedupe failed; using memory: %s", exc)
                    should_send = dedupe.should_send(alert)
                if not should_send:
                    continue
                text = format_alert(alert, str(key))
                if chat_id > 0:
                    await _deliver(bot_app, chat_id, text)
                    labels = alert.get("labels") or {}
                    logger.info(
                        "alert_forwarded queue=%s name=%s status=%s",
                        key,
                        labels.get("alertname"),
                        alert.get("status"),
                    )
            except json.JSONDecodeError:
                logger.warning("Alerts feed: non-JSON payload skipped")
            except asyncio.CancelledError:
                raise
            except RedisTimeoutError:
                # redis-py 8.x raises on an idle BLPOP cycle instead of
                # returning None — normal state, just poll again.
                continue
            except (ConnectionError, OSError, TimeoutError) as exc:
                logger.error("Alerts feed BLPOP/forward error: %s", exc)
                await asyncio.sleep(5)
            except Exception as exc:  # keep the loop alive on unknown errors
                logger.warning("Alerts feed unexpected error: %s", exc)
                await asyncio.sleep(2)
    except asyncio.CancelledError:
        logger.info("Alerts feed cancelled")
    finally:
        await client.aclose()
