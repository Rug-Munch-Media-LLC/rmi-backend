"""Deliver a scan card to Telegram — with a dry-run that never posts.

The scanner produces a dict; :func:`send_scan_card` turns it into the card from
:mod:`scan_card` and either returns the exact API payload (``dry_run=True``,
the default) or sends it.

Two behaviours worth knowing:

* **Dry-run is the default.** The payload returned in dry-run mode is the same
  object that would be posted, character count included, so a preview can be
  reviewed without a chat_id and without risking a live post.
* **The plain-text fallback sends different bytes.** A Telegram 400 on the HTML
  body drops the whole alert, so on failure the text rendering is re-sent with
  no parse mode. It is a separate rendering, not the HTML with tags stripped at
  send time — see :func:`scan_card.scan_card`.
"""

from __future__ import annotations

import os
from typing import Any

from app.domains.telegram.scan_card import MAX_CHARS, scan_card

CHANNEL_ENV = "SCAN_CHANNEL"


async def send_scan_card(
    result: object, chat_id: str | None = None, *, dry_run: bool = True
) -> dict[str, Any]:
    """Render ``result`` and return the payload (dry-run) or post it.

    Args:
        result: Scanner payload dict (``ScanResult`` or the API response).
        chat_id: Target chat/channel. Defaults to the ``SCAN_CHANNEL`` env var.
        dry_run: When true (default) nothing is sent.

    Returns:
        dict with ``ok``, ``dry_run`` and the exact ``payload`` used.
    """
    card = scan_card(result)
    target = chat_id or os.getenv(CHANNEL_ENV, "")
    payload = {
        "chat_id": target,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "text": card.html,
        "chars": len(card.html),
        "limit": MAX_CHARS,
        "fallback_text": card.text,
        "fallback_chars": len(card.text),
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "posted": False, "payload": payload}
    if not target:
        return {
            "ok": False,
            "dry_run": False,
            "error": f"no chat_id and {CHANNEL_ENV} unset",
            "payload": payload,
        }

    from app.domains.telegram.alert_hooks import send_alert

    sent = await send_alert(card.html, chat_id=target, alert_type="scanner")
    fallback = False
    if not sent.get("ok"):
        # Telegram rejected the markup (HTTP 400) — re-send the text rendering,
        # which carries no tags for the parser to trip over.
        sent = await send_alert(card.text, chat_id=target, alert_type="scanner", parse_mode=None)
        fallback = True
    return {
        "ok": bool(sent.get("ok")),
        "dry_run": False,
        "posted": bool(sent.get("ok")),
        "used_fallback": fallback,
        "payload": payload,
        "result": sent,
    }
