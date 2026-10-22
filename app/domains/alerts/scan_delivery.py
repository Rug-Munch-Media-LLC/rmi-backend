"""Deliver ONE persisted scan alert to the public alerts channel.

The ingest route persists; this module posts. Routing is deliberately narrow:
a scan finding has exactly one surface (``SCAN_ALERT_CHANNELS`` →
``alerts_channel`` → @rmialerts), so it can never fan out to alpha, news or the
main channel by accident. The per-channel gate (``RMI_CHANNEL_DRY_RUN_ALERTS``)
decides whether that surface is live — unset means dry, and this module has no
override that could bypass it.

The send goes through the Bot API transport (``alert_hooks.send_alert``), the
same path the bot uses for channel posts. The Telegram message id is returned,
so a delivery can be pointed at rather than assumed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.channel_gate import is_live
from app.core.logging import get_logger
from app.domains.alerts.models import PlatformAlert
from app.domains.alerts.publisher import CHANNEL_GATE_ROLE, SCAN_ALERT_CHANNELS
from app.domains.alerts.telegram_transport import channel_ids_from_env, format_alert_message
from app.domains.telegram.alert_hooks import send_alert

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger("alerts.scan_delivery")


async def deliver_scan_alert(row: Mapping[str, Any]) -> dict[str, Any]:
    """Post one ``alerts`` row to the single scan-alert target.

    ``row`` is a persisted alert (the shape ``postgres_store`` writes). Returns
    the resolved channel, whether the gate let it through, the Telegram
    ``message_id`` and the ``t.me`` link; nothing is sent while the role is dry.
    """
    channel = next(iter(SCAN_ALERT_CHANNELS))
    role = CHANNEL_GATE_ROLE[channel]
    chat_id = channel_ids_from_env().get(channel)
    alert = PlatformAlert(
        id=row.get("alert_id"),
        severity=row.get("severity") or "high",
        category="scanner",
        title=row.get("title") or "",
        body=row.get("description") or "",
        token_address=row.get("token_id"),
        chain=row.get("chain"),
        source="scanner",
    )
    text = format_alert_message(alert)
    result: dict[str, Any] = {
        "channel": channel,
        "role": role,
        "gate": f"RMI_CHANNEL_DRY_RUN_{role.upper()}",
        "chat_id": chat_id,
        "live": is_live(role),
        "sent": False,
        "message_id": None,
        "link": None,
        "text": text,
    }
    if chat_id is None or not result["live"]:
        logger.info("scan_delivery_skipped", chan=channel, live=result["live"])
        return result

    response = await send_alert(text, chat_id=str(chat_id), alert_type="scanner")
    delivered = next((r for r in response.get("results") or [] if r.get("ok")), None)
    if delivered is None:
        result["error"] = response.get("error") or (response.get("results") or [{}])[0].get("error")
        logger.warning("scan_delivery_failed", chan=channel, err=str(result["error"])[:120])
        return result

    message_id = delivered.get("message_id")
    handle = str(chat_id).lstrip("@")
    result.update(
        sent=True,
        message_id=message_id,
        link=f"https://t.me/{handle}/{message_id}" if message_id else None,
    )
    logger.info("scan_delivery_sent", chan=channel, message_id=message_id)
    return result
