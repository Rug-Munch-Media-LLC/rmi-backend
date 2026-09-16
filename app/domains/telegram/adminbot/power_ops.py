"""Power ops — heavy-lane control + community-bot dev console.

/heavy [start|stop|status]     control llama-chat-heavy (ff711 @ 8014)
/community [status|logs|restart|test <prompt>]
    develop and inspect the @rugmunchbot community pipeline (bridge :8095
    exercises the REAL guarded path the community hits).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update

from app.core.logging import get_logger
from app.domains.telegram.adminbot import admin_audit, secrets_scrub
from app.domains.telegram.adminbot.helpers import owner_only

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("admin_bot.power_ops")

HEAVY_UNIT = "llama-chat-heavy"
HEAVY_HEALTH_URL = "http://127.0.0.1:8014/v1/models"
COMMUNITY_UNITS = ("rugmunchbot", "chat-bridge")
BRIDGE_URL = "http://127.0.0.1:8095/v1/chat/completions"
BRIDGE_TIMEOUT_SECONDS = 300


async def _systemctl(action: str, unit: str, timeout: int = 150) -> tuple[int, str]:
    """Run systemctl <action> <unit>; returns (returncode, output)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/systemctl",
            action,
            unit,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:
        return 1, f"failed to spawn systemctl: {str(exc)[:180]}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"systemctl {action} {unit} timed out after {timeout}s"
    code = proc.returncode if proc.returncode is not None else 0
    return code, out.decode("utf-8", errors="replace").strip()


async def _journal(unit: str, lines: int = 25) -> str:
    """Tail a unit's journal; returns plain text."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/journalctl",
            "-u",
            unit,
            "-n",
            str(lines),
            "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return secrets_scrub.scrub(out.decode("utf-8", errors="replace").strip())
    except Exception as exc:
        return f"journalctl failed: {str(exc)[:180]}"


async def heavy_health() -> dict[str, Any] | None:
    """Probe the heavy lane; None when parked/unreachable."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.0)) as client:
            resp = await client.get(HEAVY_HEALTH_URL)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def format_heavy_status(active: str, health: dict[str, Any] | None, detail: str = "") -> str:
    """Pure formatter for the /heavy status card (unit-testable)."""
    if active == "active" and health is not None:
        return f"⚡ Heavy lane (ff711): ACTIVE · serving on 8014\n{detail}".strip()
    if active == "active":
        return f"⚡ Heavy lane: unit active, model still loading…\n{detail}".strip()
    return f"💤 Heavy lane: parked ({active}). Use /heavy start when needed."


async def heavy_status_text() -> str:
    _, detail = await _systemctl("is-active", HEAVY_UNIT)
    active = detail.splitlines()[0] if detail else "unknown"
    health = await heavy_health() if active == "active" else None
    return format_heavy_status(active, health)


@owner_only
async def cmd_heavy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Control the on-demand ff711 heavy lane."""
    if update.message is None:
        return
    action = ctx.args[0].lower() if ctx.args else "status"
    if action not in ("start", "stop", "status"):
        await update.message.reply_text("Usage: /heavy [start|stop|status]")
        return

    if action == "start":
        admin_audit.log_action(
            "heavy_start",
            user_id=update.effective_user.id if update.effective_user else None,
        )
        msg = await update.message.reply_text("⏳ Starting heavy lane (22G model load, ~45s)…")
        await _systemctl("start", HEAVY_UNIT, timeout=200)
        for _ in range(45):
            if await heavy_health() is not None:
                break
            await asyncio.sleep(2)
        if await heavy_health() is not None:
            await msg.edit_text(
                "⚡ Heavy lane READY — ff711 on 8014 (alias ff711).",
                reply_markup=heavy_keyboard(),
            )
        else:
            _, detail = await _systemctl("status", HEAVY_UNIT)
            await msg.edit_text(f"⚠️ Heavy lane did not become ready.\n\n{detail[:400]}")
        return

    if action == "stop":
        admin_audit.log_action(
            "heavy_stop",
            user_id=update.effective_user.id if update.effective_user else None,
        )
        code, detail = await _systemctl("stop", HEAVY_UNIT)
        state = "💤 Heavy lane parked." if code == 0 else f"⚠️ stop failed:\n{detail[:300]}"
        await update.message.reply_text(state, reply_markup=heavy_keyboard())
        return

    await update.message.reply_text(await heavy_status_text(), reply_markup=heavy_keyboard())


def heavy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚡ Start", callback_data="heavy_start"),
                InlineKeyboardButton("💤 Stop", callback_data="heavy_stop"),
                InlineKeyboardButton("📊 Status", callback_data="heavy_status"),
            ],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
        ]
    )


def community_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Status", callback_data="community_status"),
                InlineKeyboardButton("📜 Logs", callback_data="community_logs"),
            ],
            [
                InlineKeyboardButton("🔄 Restart Bot", callback_data="community_restart"),
                InlineKeyboardButton("⬅️ Main Menu", callback_data="main"),
            ],
        ]
    )


async def community_status_text() -> str:
    """Combined status card for the community bot + bridge."""
    lines = ["🐘 Community pipeline:"]
    for unit in COMMUNITY_UNITS:
        _, state = await _systemctl("is-active", unit)
        lines.append(f"• {unit}: {state.splitlines()[0] if state else 'unknown'}")
    tail = await _journal("rugmunchbot", lines=4)
    if tail:
        lines.append("\nRecent bot log:")
        lines.append(tail[-700:])
    return "\n".join(lines)


@owner_only
async def cmd_community(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Dev console for the community pipeline."""
    if update.message is None:
        return
    args = ctx.args or []
    action = args[0].lower() if args else "status"

    if action == "status":
        await update.message.reply_text(await community_status_text(), reply_markup=community_keyboard())
        return

    if action == "logs":
        text = await _journal("rugmunchbot", lines=30)
        await update.message.reply_text(
            f"📜 rugmunchbot — last 30 lines:\n\n{text[-3000:]}",
            reply_markup=community_keyboard(),
        )
        return

    if action == "restart":
        admin_audit.log_action(
            "community_restart",
            user_id=update.effective_user.id if update.effective_user else None,
        )
        code, detail = await _systemctl("restart", "rugmunchbot")
        state = "🔄 Bot restarted." if code == 0 else f"⚠️ restart failed:\n{detail[:300]}"
        await update.message.reply_text(state, reply_markup=community_keyboard())
        return

    if action == "test":
        prompt = " ".join(args[1:]).strip()
        if not prompt:
            await update.message.reply_text("Usage: /community test <prompt>")
            return
        await _community_test(update.message, prompt)
        return

    await update.message.reply_text("Usage: /community [status|logs|restart|test <prompt>]")


async def _community_test(message: Message, prompt: str) -> None:
    """Send a prompt through the REAL community bridge (guards included)."""
    import httpx

    msg = await message.reply_text("🧪 Sending through the community bridge (guards active)…")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(BRIDGE_TIMEOUT_SECONDS, connect=5.0, read=BRIDGE_TIMEOUT_SECONDS)
        ) as client:
            resp = await client.post(
                BRIDGE_URL,
                json={"messages": [{"role": "user", "content": prompt}], "stream": False},
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            await msg.edit_text(f"❌ Bridge HTTP {resp.status_code}: {resp.text[:300]}")
            return
        data = resp.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        blocked = "blocked: True" if data.get("blocked") else ""
        model = data.get("model", "?")
        header = f"🧪 Bridge reply ({model}){' · GUARD-BLOCKED' if blocked else ''}"
        await msg.edit_text(f"{header}\n\n{content[:3500]}")
    except Exception as exc:
        await msg.edit_text(f"❌ Bridge unreachable: {str(exc)[:250]}")
