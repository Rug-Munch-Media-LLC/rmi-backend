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

# Severe red flags that warrant a scanner alert even when the numeric risk
# score is below the threshold (honeypot, rugpull indicators, liquidity
# issues). Matched case-insensitively as substrings against risk flags.
SCANNER_ALERT_SEVERE_FLAGS: tuple[str, ...] = (
    "honeypot",
    "rugpull",
    "rug_pull",
    "rug_pattern",
    "rug_indicators",
    "lp_lock",
    "lp_removed",
    "liquidity_removed",
    "liquidity_low",
    "low_liquidity",
    "no_liquidity",
    "volume_fake",
    "wash_trad",
    "bundled_launch",
    "bundle_detect",
    "pump_dump",
    "dev_high_risk",
    "malicious_deployer",
    "high_risk_deployer",
    "suspicious_deployer",
    "concentrated_holders",
    "concentrated_lp",
    "extreme_lp",
    "extreme_volume",
    "new_token_under_1h",
    "flagged_spam",
    "sanctioned",
    "copycat",
    "active_proxy_owner",
    "risk_level=high",
    "risk_level=critical",
)


def scanner_alerts_enabled() -> bool:
    """Master switch for scanner → Telegram alerts.

    Reads ``SCANNER_ALERTS_ENABLED`` (default true) via ``app.config``.
    Disabled alerts are skipped without touching Telegram. Fail-open to
    enabled when the settings module cannot be loaded.
    """
    try:
        from app.config import settings

        return bool(settings.scanner_alerts_enabled)
    except Exception:
        return os.getenv("SCANNER_ALERTS_ENABLED", "true").lower() not in ("0", "false", "no", "off")


def _is_severe_flag(flag: str) -> bool:
    """Return True when a risk flag matches a known severe indicator."""
    norm = str(flag).lower()
    # Positive sentinel signals must never trip the severe gate: e.g.
    # SENTINEL_LP_LOCKED (LP >90% locked = healthy) is added to risk_flags
    # by apply_sentinel_scoring, and its substring "lp_lock" collides with
    # the severe "lp_lock" keyword — a healthy token would otherwise fire
    # a scanner alert. Negative sentinel signals (SENTINEL_LP_LOCK,
    # SENTINEL_DEV_THREAT, ...) do NOT end in "_locked" and still match.
    if norm.startswith("sentinel_") and norm.endswith("_locked"):
        return False
    return any(kw in norm for kw in SCANNER_ALERT_SEVERE_FLAGS)


def should_send_scanner_alert(risk_level: str, risk_flags: list[str]) -> bool:
    """Trigger gate for scanner alerts.

    Alerts fire when the token is ``high``/``critical`` risk OR when a
    severe red flag (honeypot, rugpull indicator, liquidity issue) is
    present. Honors the ``SCANNER_ALERTS_ENABLED`` config flag.
    """
    if not scanner_alerts_enabled():
        return False
    if risk_level in ("high", "critical"):
        return True
    return any(_is_severe_flag(flag) for flag in risk_flags)


def _default_scanner_chat_id() -> str | None:
    """Resolve the default scanner alert chat.

    Prefers operator-configured ``ALERT_TARGETS`` (send_alert uses those);
    falls back to the rugmunchbot ``CHANNELS['alerts']`` channel.
    """
    if _ALERT_TARGETS_ENV:
        return None
    try:
        from app.domains.telegram.rugmunchbot.config import CHANNELS

        return CHANNELS.get("alerts") or CHANNELS.get("scans")
    except Exception:
        return None


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
    token = bot_token or os.getenv(_BOT_TOKEN_ENV_KEY, "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("alert_hooks.send_alert: no bot token configured (set BOT_TOKEN or TELEGRAM_BOT_TOKEN)")
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
                    results.append({"ok": False, "chat_id": target["chat_id"], "error": payload.get("description")})
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
    if not scanner_alerts_enabled():
        return {"ok": True, "skipped": "alerts_disabled"}

    # Numeric threshold, but always send when a severe red flag is present
    # (honeypot, rugpull indicator, liquidity issue) — see spec.
    if not threats or (risk_score < 60 and not any(_is_severe_flag(t) for t in threats)):
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
    return await send_alert(
        message=message,
        chat_id=chat_id or _default_scanner_chat_id(),
        alert_type="scanner",
    )


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
    message = f"{icon} <b>{title}</b>\n\n{detail}\n\n🕐 <i>Auto-generated alert</i>"
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
