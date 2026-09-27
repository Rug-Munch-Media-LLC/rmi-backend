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
import hashlib
import json
import os
import time
from typing import Any

from app.core.logging import get_logger
from app.domains.telegram.adminbot.tg_markdown import code, esc, unesc

try:  # redis-py raises its own TimeoutError (not the builtin) on idle BLPOP
    from redis.exceptions import TimeoutError as RedisTimeoutError
except Exception:  # pragma: no cover - redis unavailable at import time

    class RedisTimeoutError(Exception):  # type: ignore[no-redef]
        """Fallback when redis is not importable."""


logger = get_logger("admin_bot.alerts_feed")

# sendMessage with parse_mode="Markdown" (legacy) treats these as entities and
# returns HTTP 400 "Bad Request" on an unescaped one. Alert text is free-form
# (metric names carry "(" ")" ">" "/" and ":"), so every interpolated value has
# to be escaped or the whole alert is silently dropped. Observed 4x/24h on
# production, e.g. "ValkeyHeadroom: rmi-valkey".

SYSTEM_QUEUE = "rmi:alerts:system"
SECURITY_QUEUE = "rmi:alerts:security"
QUEUE_KEYS: list[str] = [SYSTEM_QUEUE, SECURITY_QUEUE]

POLL_TIMEOUT_SECONDS = int(os.getenv("ALERTS_FEED_POLL", "10"))
DEDUPE_WINDOW_SECONDS = int(os.getenv("ALERTS_FEED_DEDUPE", "1800"))
DEDUPE_MAX_ENTRIES = 500
DEDUPE_REDIS_PREFIX = "rmi:alerts:dedupe:"

_SEVERITY_EMOJI = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}  # noqa: RUF001
_RESOLVED_EMOJI = "✅"
_QUEUE_LABEL = {
    SYSTEM_QUEUE: "system",
    SECURITY_QUEUE: "security",
}


def fingerprint(alert: dict[str, Any]) -> str:
    """Stable, status-independent identity: alertname + instance.

    Status is deliberately excluded so a ``resolved`` notice clears the
    dedupe entry of its firing counterpart (and a re-fire pages again).
    """
    labels = alert.get("labels") or {}
    return "|".join(
        str(part)
        for part in (
            labels.get("alertname"),
            labels.get("instance"),
        )
    )


def format_alert(alert: dict[str, Any], queue: str) -> str:
    """Format one Alertmanager alert dict into a Telegram Markdown message.

    Every free-form value is escaped: Telegram's legacy Markdown parse mode
    returns HTTP 400 for an unbalanced "_", "*" or "[", which silently
    dropped whole alerts in production.
    """
    status = str(alert.get("status") or "firing").lower()
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    name = esc(labels.get("alertname") or "Unknown alert")
    severity = str(labels.get("severity") or "none").lower()

    lines: list[str] = []
    if status == "resolved":
        lines.append(f"{_RESOLVED_EMOJI} *RESOLVED — {name}*")
    else:
        emoji = _SEVERITY_EMOJI.get(severity, "🔔")
        lines.append(f"{emoji} *{name}* `[{severity.upper()}]`")

    summary = str(annotations.get("summary") or "")
    description = str(annotations.get("description") or "")
    if summary:
        lines.append(f"\n{esc(summary[:400])}")
    if description and description != summary:
        lines.append(f"\n{esc(description[:600])}")

    for key in ("instance", "job", "service", "container", "device"):
        value = labels.get(key)
        if value:
            lines.append(f"• `{key}`: {esc(str(value)[:120])}")

    started = alert.get("startsAt") or alert.get("starts_at")
    if started:
        lines.append(f"\n🕐 {code(str(started)[:32])}")

    route = _QUEUE_LABEL.get(queue, "system")
    lines.append(f"\n_alerts:{route}_")
    return "\n".join(lines)


class AlertDedupe:
    """In-memory dedupe window keyed by alert fingerprint."""

    def __init__(self, window_seconds: int = DEDUPE_WINDOW_SECONDS) -> None:
        self._window = window_seconds
        self._seen: dict[str, float] = {}

    def should_send(self, alert: dict[str, Any]) -> bool:
        """True when this alert should be forwarded right now."""
        fp = fingerprint(alert)
        now = time.monotonic()
        if str(alert.get("status") or "firing").lower() == "resolved":
            # Recovery notice always passes once; clear so a re-fire pages again.
            self._seen.pop(fp, None)
            return True
        last = self._seen.get(fp)
        if last is not None and (now - last) < self._window:
            return False
        self._seen[fp] = now
        if len(self._seen) > DEDUPE_MAX_ENTRIES:
            cutoff = now - self._window
            self._seen = {k: ts for k, ts in self._seen.items() if ts >= cutoff}
        return True


async def redis_should_send(client: Any, alert: dict[str, Any]) -> bool:
    """Persist the alert fingerprint window across bot restarts.

    Redis is the source of truth when available; the caller falls back to the
    in-memory deduper if the connection is temporarily unavailable.
    """
    fp = fingerprint(alert)
    digest = hashlib.sha256(fp.encode("utf-8")).hexdigest()
    key = f"{DEDUPE_REDIS_PREFIX}{digest}"
    status = str(alert.get("status") or "firing").lower()
    labels = alert.get("labels") or {}
    severity = str(labels.get("severity") or "none").lower()

    if status == "resolved":
        await client.delete(key)
        return True

    state = f"{status}:{severity}"
    previous = await client.get(key)
    if previous == state:
        return False
    await client.set(key, state, ex=DEDUPE_WINDOW_SECONDS)
    return True


async def _deliver(bot_app: Any, chat_id: int, text: str) -> None:
    """Send one alert, retrying as plain text if the parse mode is rejected.

    A dropped alert is worse than a noisy one: Telegram answers HTTP 400
    "Bad Request" for any message its Markdown parser cannot read, and that
    used to discard the alert entirely. Falling back to no parse_mode always
    renders, so the operator gets the text even if it loses its formatting.
    """
    try:
        await bot_app.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    except Exception as exc:
        # Retry the SAME alert without the escapes: the fallback is plain text,
        # so keeping the backslashes would render "\(15m\)" on screen.
        logger.warning("alert_send_markdown_failed error=%s — retrying plain text", type(exc).__name__)
        await bot_app.send_message(chat_id=chat_id, text=unesc(text))


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
