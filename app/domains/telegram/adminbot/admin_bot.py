"""
admin_bot — RugMunchBot management operations.

Stats, toggles, broadcasts, and user listing for the RugMunchBot
(``@rugmunchbot``) driven from the admin bot. Reads the bot's SQLite
database and Redis state directly (same host, same schema as
``app.domains.telegram.rugmunchbot``), so no backend HTTP round-trip
is required and every function fails open when the DB/Redis is down.

Pattern follows admin_ops / admin_server: allowlist-safe subprocesses
(where used), pure parse/format functions for testability, and a
``result`` dict with ``ok``/``output``/``error``/``duration``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any

logger = logging.getLogger("admin_bot.admin_bot")

# Mirror of the backend bot_admin router's DB resolution, so the admin
# process reads exactly the database the running bot writes to.
_BOT_DB_CANDIDATES: tuple[str, ...] = (
    "/srv/work/repos/rmi-backend/app/domains/telegram/rugmunchbot/data/bot_users.db",
    "/tmp/rmi/telegram/bot_users.db",  # nosec B108 — known bot DB mirror path, stable inter-process lookup  # noqa: S108
)

# How many users to list in /admin_bot_users.
USERS_LIST_LIMIT = 15


def _ok(output: str = "", duration: float = 0.0) -> dict[str, Any]:
    return {"ok": True, "output": output, "error": "", "duration": duration}


def _err(msg: str) -> dict[str, Any]:
    return {"ok": False, "output": "", "error": msg, "duration": 0.0}


def _resolve_db_path() -> str:
    """Resolve the RugMunchBot SQLite path (env override, then known paths)."""
    override = os.getenv("BOT_DB_PATH", "").strip()
    if override:
        return override
    for path in _BOT_DB_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return _BOT_DB_CANDIDATES[0]


def _open_db() -> sqlite3.Connection | None:
    """Open the bot SQLite DB read-only, or None when unavailable."""
    path = _resolve_db_path()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except (sqlite3.Error, OSError) as exc:
        logger.warning("admin_bot: cannot open bot DB %s: %s", path, exc)
        return None


# ═══════════════════════════════════════════════
# BOT STATS
# ═══════════════════════════════════════════════


def _sqlite_counts() -> dict[str, int]:
    """Users / active-today / messages-today / scans-today from SQLite."""
    counts = {"users": 0, "active_today": 0, "messages_today": 0, "scans_today": 0, "banned": 0}
    conn = _open_db()
    if conn is None:
        return counts
    try:
        counts["users"] = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] or 0
        start_of_day = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))
        counts["active_today"] = (
            conn.execute("SELECT COUNT(*) AS c FROM users WHERE last_active >= ?", (start_of_day,)).fetchone()["c"] or 0
        )
        counts["banned"] = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_banned = 1").fetchone()["c"] or 0
        today = time.strftime("%Y-%m-%d")
        row = conn.execute("SELECT messages FROM daily_stats WHERE date = ?", (today,)).fetchone()
        counts["messages_today"] = row["messages"] if row else 0
        counts["scans_today"] = (
            conn.execute("SELECT COUNT(*) AS c FROM scan_history WHERE created_at >= ?", (start_of_day,)).fetchone()[
                "c"
            ]
            or 0
        )
    except sqlite3.Error as exc:
        logger.warning("admin_bot: sqlite counts failed: %s", exc)
    finally:
        conn.close()
    return counts


async def bot_stats() -> dict[str, Any]:
    """RugMunchBot headline stats: state, users, activity today."""
    t0 = time.time()
    state: dict[str, Any] = {"enabled": True, "chat_enabled": True}
    try:
        from app.domains.telegram.rugmunchbot import admin_state

        state = await admin_state.get_bot_state()
    except Exception as exc:  # fail open — Redis may be down
        logger.warning("admin_bot: bot_state unavailable: %s", exc)

    counts = _sqlite_counts()

    # Prefer Redis analytics for messages-today (bot runtime increments these);
    # fall back to the SQLite daily_stats counter.
    messages_today = counts["messages_today"]
    try:
        from app.domains.telegram.rugmunchbot import admin_state

        days = await admin_state.get_daily_analytics(1)
        if days:
            messages_today = max(messages_today, int(days[0].get("messages", 0)))
    except Exception as exc:
        logger.warning("admin_bot: daily analytics failed: %s", exc)

    return {
        # Module contract: every public op returns ok/output/error/duration.
        # bot_stats fails open by design (zero counts when DB/Redis is down),
        # so ok is always True; callers render via format_bot_stats.
        "ok": True,
        "output": "",
        "error": "",
        **counts,
        "messages_today": messages_today,
        "enabled": bool(state.get("enabled", True)),
        "chat_enabled": bool(state.get("chat_enabled", True)),
        "duration": time.time() - t0,
    }


def format_bot_stats(data: dict[str, Any]) -> str:
    """Render the RugMunchBot stats card."""
    enabled = data.get("enabled", True)
    chat = data.get("chat_enabled", True)
    state_emoji = "🟢" if enabled else "🔴"
    chat_emoji = "🟢" if chat else "🔴"
    lines = [
        "🤖 *RugMunchBot Stats*\n",
        f"{state_emoji} Bot: {'**ENABLED**' if enabled else '**DISABLED**'}",
        f"💬 Chat: {'on' if chat else 'off'} {chat_emoji}",
        "",
        f"👥 Users: `{data.get('users', 0)}` (🚫 `{data.get('banned', 0)}` banned)",
        f"🟢 Active today: `{data.get('active_today', 0)}`",
        f"💬 Messages today: `{data.get('messages_today', 0)}`",
        f"🔍 Scans today: `{data.get('scans_today', 0)}`",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# BOT TOGGLE
# ═══════════════════════════════════════════════


async def bot_toggle(enabled: bool) -> dict[str, Any]:
    """Enable or disable the RugMunchBot (Redis state + SQLite fallback)."""
    t0 = time.time()
    try:
        from app.domains.telegram.rugmunchbot import admin_state, db as bot_db

        state = await admin_state.set_bot_state(enabled=enabled)
        bot_db.set_bot_state("bot_enabled", "on" if enabled else "off")
        return _ok(
            f"Bot {'✅ enabled' if enabled else '🔴 disabled'} (state: {state.get('enabled')})",
            time.time() - t0,
        )
    except Exception as exc:
        return _err(f"Failed to toggle bot: {exc}")


async def bot_toggle_chat(enabled: bool) -> dict[str, Any]:
    """Enable or disable AI chat on the RugMunchBot."""
    t0 = time.time()
    try:
        from app.domains.telegram.rugmunchbot import admin_state, db as bot_db

        state = await admin_state.set_bot_state(chat_enabled=enabled)
        bot_db.set_bot_state("chat_enabled", "on" if enabled else "off")
        return _ok(
            f"Chat {'✅ enabled' if enabled else '🔴 disabled'} (state: {state.get('chat_enabled')})",
            time.time() - t0,
        )
    except Exception as exc:
        return _err(f"Failed to toggle chat: {exc}")


# ═══════════════════════════════════════════════
# BROADCAST
# ═══════════════════════════════════════════════


async def bot_broadcast(message: str) -> dict[str, Any]:
    """Queue a broadcast message for delivery to all bot users."""
    t0 = time.time()
    if not message or not message.strip():
        return _err("Message cannot be empty")
    try:
        from app.domains.telegram.rugmunchbot import admin_state

        bcast = await admin_state.enqueue_broadcast(message.strip(), sender="adminbot")
        if bcast.get("status") == "queued":
            return _ok(
                f"📬 Broadcast queued (id `{bcast.get('id')}`) for delivery to all users.",
                time.time() - t0,
            )
        return _err(f"Broadcast not queued: {bcast.get('status', 'unknown')} — {bcast.get('error', '')}")
    except Exception as exc:
        return _err(f"Failed to queue broadcast: {exc}")


async def bot_broadcasts(limit: int = 10) -> dict[str, Any]:
    """Recent broadcast history with delivery status."""
    t0 = time.time()
    try:
        from app.domains.telegram.rugmunchbot import admin_state

        records = await admin_state.list_broadcasts(limit)
        if not records:
            return _ok("📬 No broadcasts in history yet.", time.time() - t0)
        lines = ["📬 *Broadcast History*\n"]
        status_emoji = {"queued": "⏳", "sending": "📤", "completed": "✅", "failed": "❌", "error": "❌"}
        for b in records[:limit]:
            emoji = status_emoji.get(b.get("status", ""), "ℹ️")
            lines.append(
                f"{emoji} `{b.get('id', '?')}` ({b.get('status', '?')}) "
                f"s/b/f {b.get('sent', 0)}/{b.get('total', 0)}/{b.get('failed', 0)}"
            )
            msg = (b.get("message") or "").replace("\n", " ")[:60]
            if msg:
                lines.append(f"   💬 {msg}")
        return _ok("\n".join(lines), time.time() - t0)
    except Exception as exc:
        return _err(f"Failed to list broadcasts: {exc}")


# ═══════════════════════════════════════════════
# USER LISTING
# ═══════════════════════════════════════════════


def _recent_users(limit: int = USERS_LIST_LIMIT) -> list[dict[str, Any]]:
    """Most recently active bot users (id, name, tier, last_active)."""
    conn = _open_db()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT user_id, username, first_name, tier, is_banned, last_active "
            "FROM users ORDER BY last_active DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.warning("admin_bot: recent users failed: %s", exc)
        return []
    finally:
        conn.close()


def bot_users(limit: int = USERS_LIST_LIMIT) -> dict[str, Any]:
    """List the most recently active RugMunchBot users."""
    t0 = time.time()
    rows = _recent_users(limit)
    if not rows:
        return _ok("👥 No users found in the bot database.", time.time() - t0)
    lines = ["👥 *Recent RugMunchBot Users*\n"]
    for u in rows:
        name = u.get("username") or u.get("first_name") or str(u.get("user_id", "?"))
        tier = u.get("tier", "free")
        banned = " 🚫" if u.get("is_banned") else ""
        last = u.get("last_active") or 0
        ago = f"{int(time.time() - last) // 3600}h ago" if last else "never"
        lines.append(f"• `{name[:24]}` — {tier}{banned} · {ago}")
    lines.append(f"\nShowing {len(rows)} most recent")
    return _ok("\n".join(lines), time.time() - t0)


# ═══════════════════════════════════════════════
# SCANNER (scans today)
# ═══════════════════════════════════════════════


def scanner_today() -> dict[str, Any]:
    """Scans recorded today in the bot DB (complements backend scan_stats)."""
    t0 = time.time()
    counts = _sqlite_counts()
    return _ok(str(counts.get("scans_today", 0)), time.time() - t0)


# ═══════════════════════════════════════════════
# SCANNER STATS (bot-level, from scan_history)
# ═══════════════════════════════════════════════


def scanner_stats() -> dict[str, Any]:
    """Scanner activity from the bot's scan_history: today / week / total.

    Avg time per scan is not tracked in the bot DB, so we report activity
    volume and per-chain breakdown instead (backend metrics cover latency).
    """
    t0 = time.time()
    conn = _open_db()
    if conn is None:
        return _err("Cannot open bot DB for scanner stats")
    try:
        start_of_day = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))
        start_of_week = int(
            time.mktime(
                time.strptime(
                    time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400)),
                    "%Y-%m-%d",
                )
            )
        )
        scans_today = (
            conn.execute("SELECT COUNT(*) AS c FROM scan_history WHERE created_at >= ?", (start_of_day,)).fetchone()[
                "c"
            ]
            or 0
        )
        scans_week = (
            conn.execute("SELECT COUNT(*) AS c FROM scan_history WHERE created_at >= ?", (start_of_week,)).fetchone()[
                "c"
            ]
            or 0
        )
        scans_total = conn.execute("SELECT COUNT(*) AS c FROM scan_history").fetchone()["c"] or 0
        unique_tokens = conn.execute("SELECT COUNT(DISTINCT token_address) AS c FROM scan_history").fetchone()["c"] or 0

        chain_rows = conn.execute(
            "SELECT chain, COUNT(*) AS c FROM scan_history GROUP BY chain ORDER BY c DESC LIMIT 6"
        ).fetchall()
        by_chain = {r["chain"] or "unknown": r["c"] for r in chain_rows}
        avg_risk = conn.execute("SELECT AVG(risk_score) AS a FROM scan_history").fetchone()["a"]
        avg_risk = round(float(avg_risk), 1) if avg_risk is not None else None
    except sqlite3.Error as exc:
        conn.close()
        return _err(f"Scanner stats query failed: {exc}")
    conn.close()

    lines = ["🔍 *Scanner Stats* (bot)\n"]
    lines.append(f"• Scans today: `{scans_today}`")
    lines.append(f"• Scans (7d): `{scans_week}`")
    lines.append(f"• Total scans: `{scans_total}`")
    lines.append(f"• Unique tokens: `{unique_tokens}`")
    if avg_risk is not None:
        lines.append(f"• Avg risk score: `{avg_risk}`")
    if by_chain:
        lines.append("\n*By chain:*")
        for chain, count in by_chain.items():
            lines.append(f"  • `{chain}`: {count}")
    lines.append("\nℹ️ Avg scan latency is reported by the backend metrics endpoint.")
    return _ok("\n".join(lines), time.time() - t0)
