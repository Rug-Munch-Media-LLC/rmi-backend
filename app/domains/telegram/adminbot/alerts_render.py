"""
Alert formatting & dedupe — extracted from alerts_feed.py for structure gate.

Pure functions/classes only; no Redis or I/O dependencies.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from app.domains.telegram.adminbot.tg_markdown import code, esc, unesc

# ── Constants ─────────────────────────────────────────────────────────

_SYSTEM_QUEUE = "rmi:alerts:system"
_SECURITY_QUEUE = "rmi:alerts:security"
_QUEUE_KEYS: list[str] = [_SYSTEM_QUEUE, _SECURITY_QUEUE]

_SEVERITY_EMOJI = {"critical": "🚨", "warning": "⚠️", "info": "\u2139\ufe0f"}
_RESOLVED_EMOJI = "✅"
_QUEUE_LABEL = {
    _SYSTEM_QUEUE: "system",
    _SECURITY_QUEUE: "security",
}

DEDUPE_WINDOW_SECONDS = 1800
DEDUPE_MAX_ENTRIES = 500
DEDUPE_REDIS_PREFIX = "rmi:alerts:dedupe:"


# ── Public exports ───────────────────────────────────────────────────

SYSTEM_QUEUE = _SYSTEM_QUEUE
SECURITY_QUEUE = _SECURITY_QUEUE
QUEUE_KEYS = _QUEUE_KEYS


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
    import logging

    logger = logging.getLogger("admin_bot.alerts_feed")

    try:
        await bot_app.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    except Exception as exc:
        # Retry the SAME alert without the escapes: the fallback is plain text,
        # so keeping the backslashes would render "\(15m\)" on screen.
        logger.warning("alert_send_markdown_failed error=%s — retrying plain text", type(exc).__name__)
        await bot_app.send_message(chat_id=chat_id, text=unesc(text))
