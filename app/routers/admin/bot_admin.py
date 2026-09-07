"""
Bot admin endpoints — bot management, user management, analytics, broadcast.

Powers the Telegram admin panel. All endpoints require:
  1. ``X-API-Key`` header matching the shared secret (RMI_AUTH_TOKEN or
     ADMIN_API_KEY) — the global AuthMiddleware already enforces this for
     every /api/v1/admin/* path; we re-verify here so the router is safe
     even if the middleware is bypassed in dev.
  2. The caller must be the bot owner: their Telegram user ID is sent as
     ``X-User-ID`` and must be in the owner set (OWNER_CHAT_ID /
     ADMIN_CHAT_ID / TELEGRAM_ALLOWED_USERS). Non-owners get 403.

State, bans, analytics and the broadcast queue live in Redis via
``rugmunchbot.admin_state`` so the bot runtime and the
backend see identical values. User records are queried from the bot's
SQLite database (same schema as app/domains/telegram/rugmunchbot/db.py).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from rugmunchbot.admin_state import (  # Phase 3: own repo
    ban_user,
    banned_user_ids,
    enqueue_broadcast,
    get_bot_state,
    get_command_stats,
    get_daily_analytics,
    is_user_banned,
    list_broadcasts,
    set_bot_state,
    unban_user,
)

logger = logging.getLogger(__name__)

bot_admin_router = APIRouter(prefix="/bot", tags=["admin-bot"])

VALID_TIERS = ("free", "scout", "hunter", "pro")


# ══════════════════════════════════════════════
# Auth
# ══════════════════════════════════════════════


def _shared_secrets() -> set[str]:
    """Accepted X-API-Key values: RMI_AUTH_TOKEN and/or ADMIN_API_KEY."""
    secrets = set()
    for key in ("RMI_AUTH_TOKEN", "ADMIN_API_KEY"):
        val = os.getenv(key, "").strip()
        if val:
            secrets.add(val)
    try:
        from app.config import settings

        if settings.rmi_auth_token:
            secrets.add(settings.rmi_auth_token)
    except Exception as exc:
        logger.debug("auth-token collection skipped: %s", exc)
    return secrets


def _owner_ids() -> set[int]:
    """Resolve owner Telegram user IDs from env/config.

    Empty when nothing is configured — the caller then denies (fail-closed);
    there is deliberately no hardcoded fallback owner.
    """
    ids: set[int] = set()
    for var in ("OWNER_CHAT_ID", "ADMIN_CHAT_ID", "TELEGRAM_ALLOWED_USERS"):
        raw = os.getenv(var, "").strip()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
    try:
        from rugmunchbot.config import OWNER_IDS

        ids |= {int(x) for x in OWNER_IDS if str(x).isdigit()}
    except Exception:
        logger.debug("bot_admin: rugmunchbot OWNER_IDS unavailable", exc_info=True)
    try:
        from app.config import settings

        raw = settings.telegram_allowed_users
        for part in str(raw).split(","):
            if part.strip().isdigit():
                ids.add(int(part.strip()))
    except Exception:
        logger.debug("bot_admin: settings.telegram_allowed_users unavailable", exc_info=True)
    return ids


async def verify_bot_admin(request: Request) -> bool:
    """Require a valid shared secret AND an owner caller. 401/403 otherwise."""
    presented = (request.headers.get("X-API-Key") or "").strip()
    if not presented or presented not in _shared_secrets():
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    caller = (request.headers.get("X-User-ID") or "").strip()
    if not caller.isdigit() or int(caller) not in _owner_ids():
        raise HTTPException(status_code=403, detail="Only the bot owner can access bot admin")
    return True


# ══════════════════════════════════════════════
# Bot SQLite access (same schema as rugmunchbot/db.py)
# ══════════════════════════════════════════════


def _resolve_bot_db_path() -> str:
    """Find the bot's SQLite DB.

    Priority:
      1. The bot db module's resolved path (BOT_DB_PATH env override, then
         package-local data dir, then /tmp fallback — same logic the bot
         itself uses at runtime; also honors test monkeypatching).
      2. Host dev path (repo checkout on the same machine as the API).
      3. /tmp fallback (used by the bot in read-only containers).
    """
    try:
        from rugmunchbot import db as bot_db

        if os.path.exists(bot_db.DB_PATH):
            return bot_db.DB_PATH
    except Exception as exc:
        logger.debug("bot DB path detection failed: %s", exc)

    candidates = [
        os.getenv("BOT_DB_PATH", "").strip(),
        os.getenv("BOT_USERS_DB_PATH", ""),
        os.path.join(
            os.path.dirname(__file__), "..", "..", "domains", "telegram", "rugmunchbot", "data", "bot_users.db"
        ),
        "/tmp/rmi/telegram/bot_users.db",  # nosec B108 — known bot DB mirror path, stable inter-process lookup
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    # Last resort: let the bot db module resolve (may create an empty file).
    try:
        from rugmunchbot import db as bot_db

        return bot_db.DB_PATH
    except Exception:
        return os.path.join(
            os.path.dirname(__file__), "..", "..", "domains", "telegram", "rugmunchbot", "data", "bot_users.db"
        )


def _open_db() -> sqlite3.Connection | None:
    path = _resolve_bot_db_path()
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except (sqlite3.Error, OSError) as exc:
        logger.warning("bot_admin: could not open bot DB %s: %s", path, exc)
        return None


def _get_user_row(user_id: int) -> dict[str, Any] | None:
    conn = _open_db()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as exc:
        logger.warning("bot_admin: user lookup failed user_id=%s: %s", user_id, exc)
        return None
    finally:
        conn.close()


def _sync_bot_state(key: str, enabled: bool) -> None:
    """Mirror a Redis toggle into the bot's SQLite ``bot_state`` table.

    The bot runtime reads toggles from its own SQLite (db.get_bot_state),
    so the admin API persists both places — Redis for the admin API surface,
    SQLite so the change affects live bot behavior immediately.
    """
    value = "on" if enabled else "off"
    try:
        from rugmunchbot import db as bot_db

        bot_db.set_bot_state(key, value)
    except Exception as exc:
        logger.warning("bot_admin: could not sync bot state %s=%s: %s", key, value, exc)


# ══════════════════════════════════════════════
# Models
# ══════════════════════════════════════════════


class BotToggleRequest(BaseModel):
    enabled: bool


class ChatToggleRequest(BaseModel):
    chat_enabled: bool


class TierRequest(BaseModel):
    tier: str = Field(..., min_length=1, max_length=20)


class BroadcastRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


# ══════════════════════════════════════════════
# Bot control
# ══════════════════════════════════════════════


@bot_admin_router.post("/toggle")
async def bot_toggle(req: BotToggleRequest, _: bool = Depends(verify_bot_admin)) -> dict[str, Any]:
    """Enable or disable the entire bot."""
    state = await set_bot_state(enabled=req.enabled)
    _sync_bot_state("bot_enabled", req.enabled)
    return {"status": "ok", "enabled": state.get("enabled", False)}


@bot_admin_router.post("/toggle-chat")
async def bot_toggle_chat(req: ChatToggleRequest, _: bool = Depends(verify_bot_admin)) -> dict[str, Any]:
    """Enable or disable AI chat (message handler AI tiers)."""
    state = await set_bot_state(chat_enabled=req.chat_enabled)
    _sync_bot_state("chat_enabled", req.chat_enabled)
    return {"status": "ok", "chat_enabled": state.get("chat_enabled", False)}


@bot_admin_router.get("/status")
async def bot_status(_: bool = Depends(verify_bot_admin)) -> dict[str, Any]:
    """Current bot state + headline user counts."""
    state = await get_bot_state()
    conn = _open_db()
    users = active_today = 0
    if conn is not None:
        try:
            users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] or 0
            day_start = int((datetime.now(UTC) - timedelta(days=1)).timestamp())
            active_today = (
                conn.execute("SELECT COUNT(*) AS c FROM users WHERE last_active >= ?", (day_start,)).fetchone()["c"]
                or 0
            )
        except sqlite3.Error as exc:
            logger.warning("bot_admin: status count failed: %s", exc)
        finally:
            conn.close()
    return {
        "enabled": bool(state.get("enabled", True)),
        "chat_enabled": bool(state.get("chat_enabled", True)),
        "users": users,
        "active_today": active_today,
    }


# ══════════════════════════════════════════════
# User management
# ══════════════════════════════════════════════


@bot_admin_router.get("/users")
async def bot_users(
    _: bool = Depends(verify_bot_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    tier: str | None = Query(None),
    search: str | None = Query(None, max_length=100),
) -> dict[str, Any]:
    """List bot users with pagination, optional tier filter and search."""
    conn = _open_db()
    if conn is None:
        return {"users": [], "total": 0, "page": page, "per_page": per_page, "status": "db_unavailable"}

    where: list[str] = []
    params: list[Any] = []
    if tier:
        if tier not in VALID_TIERS:
            raise HTTPException(status_code=400, detail=f"Invalid tier. Options: {', '.join(VALID_TIERS)}")
        where.append("tier = ?")
        params.append(tier)
    if search:
        like = f"%{search}%"
        where.append("(username LIKE ? OR first_name LIKE ? OR CAST(user_id AS TEXT) LIKE ?)")
        params.extend([like, like, like])

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * per_page
    try:
        # `where_sql` is a code literal (empty or fixed ?-placeholder SQL); values bound via params.
        total = conn.execute("SELECT COUNT(*) AS c FROM users" + where_sql, params).fetchone()["c"] or 0
        rows = conn.execute(
            "SELECT * FROM users" + where_sql + " ORDER BY last_active DESC LIMIT ? OFFSET ?",
            [*params, per_page, offset],
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("bot_admin: users list failed: %s", exc)
        return {"users": [], "total": 0, "page": page, "per_page": per_page, "status": "error", "error": str(exc)}
    finally:
        conn.close()

    banned = set(await banned_user_ids())
    users = []
    for r in rows:
        u = dict(r)
        u["is_banned"] = bool(u.get("is_banned")) or int(u["user_id"]) in banned
        users.append(u)
    return {"users": users, "total": total, "page": page, "per_page": per_page}


@bot_admin_router.get("/users/{user_id}")
async def bot_user_detail(user_id: int, _: bool = Depends(verify_bot_admin)) -> dict[str, Any]:
    """Detailed user record: tier, usage, message counts, join date."""
    row = _get_user_row(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    conn = _open_db()
    week = datetime.now(UTC).strftime("%Y-%m-%d")
    scans_week = ai_week = 0
    if conn is not None:
        try:
            wu = conn.execute(
                "SELECT scans, ai_msgs FROM weekly_usage WHERE user_id = ? AND week_start = ?",
                (user_id, week),
            ).fetchone()
            if wu:
                scans_week, ai_week = wu["scans"], wu["ai_msgs"]
            watch_count = (
                conn.execute(
                    "SELECT COUNT(*) AS c FROM watchlists WHERE user_id = ? AND is_active = 1", (user_id,)
                ).fetchone()["c"]
                or 0
            )
        except sqlite3.Error as exc:
            logger.warning("bot_admin: user detail failed user_id=%s: %s", user_id, exc)
            watch_count = 0
        finally:
            conn.close()
    else:
        watch_count = 0

    banned = await is_user_banned(user_id)
    created = row.get("created_at")
    return {
        "user_id": user_id,
        "username": row.get("username"),
        "first_name": row.get("first_name"),
        "tier": row.get("tier", "free"),
        "tier_expires": row.get("tier_expires", 0),
        "is_banned": bool(row.get("is_banned")) or banned,
        "created_at": created,
        "join_date": datetime.fromtimestamp(created, UTC).isoformat() if created else None,
        "last_active": row.get("last_active"),
        "total_scans": row.get("total_scans", 0),
        "total_ai_msgs": row.get("total_ai_msgs", 0),
        "messages": row.get("total_ai_msgs", 0),
        "scans_this_week": scans_week,
        "ai_msgs_this_week": ai_week,
        "watchlist_count": watch_count,
        "stars_balance": row.get("stars_balance", 0),
        "referral_code": row.get("referral_code"),
    }


@bot_admin_router.post("/users/{user_id}/tier")
async def bot_user_set_tier(user_id: int, req: TierRequest, _: bool = Depends(verify_bot_admin)) -> dict[str, Any]:
    """Change a user's tier (free, scout, hunter, pro)."""
    tier = req.tier.lower()
    if tier not in VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"Invalid tier. Options: {', '.join(VALID_TIERS)}")
    if _get_user_row(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        from rugmunchbot import db as bot_db

        bot_db.set_user_tier(user_id, tier, days=30)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update tier: {exc}") from exc
    return {"status": "ok", "user_id": user_id, "tier": tier, "expires_in_days": 30 if tier != "free" else 0}


@bot_admin_router.post("/users/{user_id}/ban")
async def bot_user_ban(user_id: int, _: bool = Depends(verify_bot_admin)) -> dict[str, Any]:
    """Ban a user (Redis set + SQLite is_banned flag)."""
    if _get_user_row(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    await ban_user(user_id)
    conn = _open_db()
    if conn is not None:
        try:
            conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
            conn.commit()
        except sqlite3.Error as exc:
            logger.warning("bot_admin: ban persist failed user_id=%s: %s", user_id, exc)
        finally:
            conn.close()
    return {"status": "ok", "user_id": user_id, "is_banned": True}


@bot_admin_router.post("/users/{user_id}/unban")
async def bot_user_unban(user_id: int, _: bool = Depends(verify_bot_admin)) -> dict[str, Any]:
    """Unban a user (Redis set + SQLite is_banned flag)."""
    if _get_user_row(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    await unban_user(user_id)
    conn = _open_db()
    if conn is not None:
        try:
            conn.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
            conn.commit()
        except sqlite3.Error as exc:
            logger.warning("bot_admin: unban persist failed user_id=%s: %s", user_id, exc)
        finally:
            conn.close()
    return {"status": "ok", "user_id": user_id, "is_banned": False}


# ══════════════════════════════════════════════
# Analytics
# ══════════════════════════════════════════════


@bot_admin_router.get("/analytics/daily")
async def bot_analytics_daily(_: bool = Depends(verify_bot_admin), days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    """Per-day messages, new users, AI queries and tokens used."""
    return {"days": await get_daily_analytics(days)}


@bot_admin_router.get("/analytics/usage")
async def bot_analytics_usage(_: bool = Depends(verify_bot_admin)) -> dict[str, Any]:
    """Tier breakdown, top users, popular commands."""
    conn = _open_db()
    tier_breakdown: list[dict[str, Any]] = []
    top_users: list[dict[str, Any]] = []
    if conn is not None:
        try:
            tier_breakdown = [
                {"tier": r["tier"], "count": r["c"]}
                for r in conn.execute("SELECT tier, COUNT(*) AS c FROM users GROUP BY tier ORDER BY c DESC").fetchall()
            ]
            top_users = [
                dict(r)
                for r in conn.execute(
                    "SELECT user_id, username, tier, total_scans, total_ai_msgs, last_active "
                    "FROM users ORDER BY (total_scans + total_ai_msgs) DESC LIMIT 10"
                ).fetchall()
            ]
        except sqlite3.Error as exc:
            logger.warning("bot_admin: usage analytics failed: %s", exc)
        finally:
            conn.close()

    commands = await get_command_stats(20)
    return {
        "tier_breakdown": tier_breakdown,
        "top_users": top_users,
        "popular_commands": commands,
        "banned_count": len(await banned_user_ids()),
    }


# ══════════════════════════════════════════════
# Broadcast
# ══════════════════════════════════════════════


@bot_admin_router.post("/broadcast")
async def bot_broadcast(req: BroadcastRequest, _: bool = Depends(verify_bot_admin)) -> dict[str, Any]:
    """Queue a message for delivery to all users.

    The bot picks this up from the Redis queue in a background job and
    delivers it to every non-banned user.
    """
    bcast = await enqueue_broadcast(req.message)
    return {
        "status": "queued" if bcast.get("status") == "queued" else bcast.get("status", "error"),
        "broadcast_id": bcast.get("id"),
        "message": req.message,
        "created_at": bcast.get("created_at"),
        "error": bcast.get("error"),
    }


@bot_admin_router.get("/broadcasts")
async def bot_broadcasts(_: bool = Depends(verify_bot_admin), limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """Recent broadcast history with delivery status."""
    return {"broadcasts": await list_broadcasts(limit)}
