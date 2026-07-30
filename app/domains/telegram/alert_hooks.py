"""
Alert Hooks — Send scanner/system alerts to Telegram.

Provides a configurable notification bridge for the scanner subsystem
and admin monitoring to push critical alerts to Telegram channels/groups.

Usage:
    from app.domains.telegram.alert_hooks import send_alert

    await send_alert(
        chat_id="-1001234567890",
        message="🚨 High-risk token detected: ...",
        alert_type="scanner",
    )
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("alert_hooks")

# Default Telegram Bot API endpoint (rugmunchbot or admin bot)
_TELEGRAM_API = "https://api.telegram.org/bot"

# Configurable alert targets — set via env vars
# Format: comma-separated "chat_id:alert_type" pairs
# Example: ALERT_TARGETS="-100111:scanner,admin_chat_id:system"
_ALERT_TARGETS_ENV = os.getenv("ALERT_TARGETS", "")
_BOT_TOKEN_ENV_KEY = os.getenv("ALERT_BOT_TOKEN_KEY", "BOT_TOKEN")


def _get_alert_targets() -> list[dict[str, str]]:
    """Parse ALERT_TARGETS env var into structured targets."""
    if not _ALERT_TARGETS_ENV:
        return []
    targets: list[dict[str, str]] = []
    for pair in _ALERT_TARGETS_ENV.split(","):
        pair = pair.strip()
        if ":" in pair:
            chat_id, alert_type = pair.split(":", 1)
            targets.append({"chat_id": chat_id.strip(), "alert_type": alert_type.strip()})
        elif pair:
            targets.append({"chat_id": pair.strip(), "alert_type": "all"})
    return targets


async def send_alert(
    message: str,
    chat_id: str | None = None,
    alert_type: str = "system",
    bot_token: str | None = None,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True,
) -> dict[str, Any]:
    """Send an alert message to a Telegram chat.

    Args:
        message: The alert text (HTML supported).
        chat_id: Target chat/group/channel ID. Falls back to ALERT_TARGETS.
        alert_type: Filter type — only sends if matches target's type or 'all'.
        bot_token: Bot API token. Falls back to BOT_TOKEN env var.
        parse_mode: 'HTML' or 'Markdown'.
        disable_web_page_preview: Whether to strip link previews.

    Returns:
        dict with success status and Telegram API response.
    """
    token = bot_token or os.getenv(_BOT_TOKEN_ENV_KEY, "")
    if not token:
        logger.error("alert_hooks.send_alert: no bot token configured")
        return {"ok": False, "error": "no_bot_token"}

    targets = _get_alert_targets() if chat_id is None else [{"chat_id": chat_id, "alert_type": "all"}]

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=10) as client:
        for target in targets:
            if target["alert_type"] not in ("all", alert_type):
                continue
            try:
                resp = await client.post(
                    f"{_TELEGRAM_API}{token}/sendMessage",
                    json={
                        "chat_id": target["chat_id"],
                        "text": message,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": disable_web_page_preview,
                    },
                )
                payload = resp.json()
                if payload.get("ok"):
                    results.append({"ok": True, "chat_id": target["chat_id"]})
                else:
                    logger.warning(
                        "Telegram API error for %s: %s",
                        target["chat_id"],
                        payload.get("description", "unknown"),
                    )
                    results.append(
                        {"ok": False, "chat_id": target["chat_id"], "error": payload.get("description")}
                    )
            except httpx.HTTPError as e:
                logger.warning("HTTP error sending alert to %s: %s", target["chat_id"], e)
                results.append({"ok": False, "chat_id": target["chat_id"], "error": str(e)[:100]})

    return {"ok": any(r["ok"] for r in results), "results": results}


async def send_scanner_alert(
    token_address: str,
    chain: str,
    risk_score: float,
    threats: list[str],
    token_name: str = "Unknown",
    token_symbol: str = "???",
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Convenience: format and send a scanner alert for a high-risk token.

    Args:
        token_address: The contract address.
        chain: Blockchain name.
        risk_score: 0-100 risk score.
        threats: List of detected threat strings.
        token_name: Token name.
        token_symbol: Token symbol.
        chat_id: Override target chat ID.

    Returns:
        dict from send_alert().
    """
    if risk_score < 60 or not threats:
        return {"ok": True, "skipped": "risk_below_threshold"}

    threat_list = "\n".join(f"🔴 {t}" for t in threats[:5])
    message = (
        f"🚨 <b>High-Risk Token Detected</b>\n\n"
        f"<b>{token_name}</b> ({token_symbol})\n"
        f"<code>{token_address}</code>\n"
        f"⛓️ {chain.capitalize()}\n\n"
        f"⚠️ <b>Risk Score: {risk_score:.0f}%</b>\n\n"
        f"{threat_list}\n\n"
        f"🔍 Full report: https://rugmunch.io/scanner?token={token_address}&chain={chain}"
    )
    return await send_alert(message=message, chat_id=chat_id, alert_type="scanner")


async def send_system_alert(
    title: str,
    detail: str,
    severity: str = "warning",
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Convenience: format and send a system/infrastructure alert.

    Args:
        title: Short alert title.
        detail: Detailed description.
        severity: 'critical', 'warning', or 'info'.
        chat_id: Override target chat ID.

    Returns:
        dict from send_alert().
    """
    icons = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}
    icon = icons.get(severity, "ℹ️")
    message = (
        f"{icon} <b>{title}</b>\n\n"
        f"{detail}\n\n"
        f"🕐 <i>Auto-generated alert</i>"
    )
    return await send_alert(message=message, chat_id=chat_id, alert_type="system")


async def send_parity_alert(
    chain: str,
    divergence_pct: float,
    expected_block: int,
    actual_block: int,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Convenience: alert when a chain indexer falls behind (>10% divergence)."""
    if divergence_pct < 10:
        return {"ok": True, "skipped": "below_threshold"}

    message = (
        f"⚠️ <b>Chain Parity Alert: {chain.capitalize()}</b>\n\n"
        f"Divergence: <b>{divergence_pct:.1f}%</b>\n"
        f"Expected block: <code>{expected_block:,}</code>\n"
        f"Actual block:   <code>{actual_block:,}</code>\n"
        f"Behind by: <b>{expected_block - actual_block:,}</b> blocks\n\n"
        f"🛠️ Suggested action: Check lite-indexer logs"
    )
    return await send_alert(message=message, chat_id=chat_id, alert_type="system")
