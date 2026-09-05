"""
Admin Backend API Router — /api/v1/admin/backend/*
====================================================
Serves the darkroom admin panel (static/admin.html).
Wires the business logic from app/domains/admin/backend/ to HTTP endpoints.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.domains.admin.backend import (
    AdminRole,
    AdminUserStore,
    AuditLogger,
    SecurityManager,
    SessionManager,
    SystemHealthMonitor,
    require_admin,
)

logger = get_logger("rmi_admin_backend_api")

backend_router = APIRouter(prefix="/backend", tags=["admin-backend"])


# ══════════════════════════════════════════════
# Auth dependency
# ══════════════════════════════════════════════


async def verify_session(request: Request) -> dict[str, Any]:
    """Validate X-Admin-Session header and return session data."""
    session_id = request.headers.get("X-Admin-Session", "")
    if not session_id:
        raise HTTPException(status_code=401, detail="Missing session")
    session = await SessionManager.validate_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return session


async def require_admin_session(request: Request) -> dict[str, Any]:
    """Require an ADMIN-role admin session (RBAC) and return session data.

    Wraps require_admin (app.domains.admin.backend.auth_decorator), which
    enforces role ordering (min_role), permission checks, IP blocking, admin
    account activity, per-admin rate limiting, and audit logging on top of
    session validation. Returns the session dict so handlers keep their
    existing shape (admin_id / admin_email). Prevents privilege escalation:
    a VIEWER/SUPPORT/MODERATOR session can no longer mutate privileged state.
    """
    result = await require_admin(request, min_role=AdminRole.ADMIN)
    return result["session"]


# ══════════════════════════════════════════════
# Bot DB helper (shared with bot_admin.py)
# ══════════════════════════════════════════════


def _resolve_bot_db_path() -> str:
    candidates = [
        os.getenv("BOT_DB_PATH", ""),
        "/app/bot-src/rugmunchbot/data/bot_users.db",  # Phase 3: bot repo mounted in container
        "/tmp/rmi/telegram/bot_users.db",  # noqa: S108  # nosec B108 — known bot DB mirror path, stable inter-process lookup
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    try:
        from rugmunchbot import db as bot_db  # Phase 3: bot code lives in its own repo (mounted at /srv/bot-src)

        return bot_db.DB_PATH
    except Exception:
        return "/app/bot-src/rugmunchbot/data/bot_users.db"


def _open_bot_db() -> sqlite3.Connection | None:
    path = _resolve_bot_db_path()
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except (sqlite3.Error, OSError) as exc:
        logger.warning("admin_backend: could not open bot DB %s: %s", path, exc)
        return None


def _redis() -> Any:
    """Get an async Redis connection."""
    import redis.asyncio as redis_lib

    return redis_lib.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", ""),
        decode_responses=True,
    )


# ══════════════════════════════════════════════
# Request/Response Models
# ══════════════════════════════════════════════


class LoginRequest(BaseModel):
    email: str
    password: str
    totp_code: str | None = None


class BlockIPRequest(BaseModel):
    ip: str
    reason: str = ""
    duration_hours: int = 24


class UnblockIPRequest(BaseModel):
    ip: str


class CreateAdminRequest(BaseModel):
    email: str
    password: str
    role: str = "viewer"


class CreateAPIKeyRequest(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=list)
    expires_days: int = 30


class CreateWebhookRequest(BaseModel):
    url: str
    events: list[str] = Field(default_factory=list)
    secret: str = ""


class AnnouncementRequest(BaseModel):
    title: str
    body: str
    active: bool = True


class BanUserRequest(BaseModel):
    reason: str = ""


# ══════════════════════════════════════════════
# Auth
# ══════════════════════════════════════════════


@backend_router.post("/auth/login")
async def login(req: LoginRequest, request: Request) -> dict[str, Any]:
    """Admin login — returns session token."""
    ip = request.client.host if request.client else "unknown"

    # Rate limit login attempts
    rate = await SecurityManager.check_rate_limit(f"login:{ip}", "login")
    if not rate.get("allowed"):
        raise HTTPException(status_code=429, detail="Too many login attempts")

    admin = await AdminUserStore.verify_admin_login(req.email, req.password)
    if not admin:
        await SecurityManager.track_failed_login(f"login:{ip}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    await SecurityManager.reset_failed_login(f"login:{ip}")

    role = AdminRole(admin.get("role", "viewer"))
    session_id = await SessionManager.create_session(
        admin_id=admin["id"],
        admin_email=admin["email"],
        role=role,
        ip_address=ip,
        user_agent=request.headers.get("User-Agent", ""),
    )

    if not session_id:
        raise HTTPException(status_code=500, detail="Session creation failed")

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="login",
        resource_type="session",
        resource_id=session_id,
        ip_address=ip,
    )

    return {"session": session_id, "admin": admin}


@backend_router.get("/auth/me")
async def me(session: dict[str, Any] = Depends(verify_session)) -> dict[str, Any]:
    """Get current admin info."""
    return {
        "admin": {
            "id": session["admin_id"],
            "email": session["admin_email"],
            "role": session["role"],
        }
    }


# ══════════════════════════════════════════════
# Dashboard
# ══════════════════════════════════════════════


@backend_router.get("/dashboard")
async def dashboard(session: dict[str, Any] = Depends(verify_session)) -> dict[str, Any]:
    """Dashboard overview — stats + health summary."""
    stats: dict[str, Any] = {
        "total_users": 0,
        "active_sessions": 0,
        "blocked_ips": 0,
        "total_deployments": 0,
    }

    # Bot users count
    conn = _open_bot_db()
    if conn:
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
            stats["total_users"] = row["c"] if row else 0
        except sqlite3.Error as e:
            logger.warning(f"dashboard failed: {e}")
        finally:
            conn.close()

    # Active sessions + blocked IPs from Redis
    try:
        r = _redis()
        stats["blocked_ips"] = len(await r.smembers("blocked_ips:all"))
        # Count active sessions across all admins
        admins = await AdminUserStore.list_admins()
        for a in admins:
            sessions = await SessionManager.get_active_sessions(a["id"])
            stats["active_sessions"] += len(sessions)
    except Exception:
        logger.debug("admin_backend: session count failed", exc_info=True)

    health = await SystemHealthMonitor.get_system_health()

    return {"stats": stats, "health": health}


# ══════════════════════════════════════════════
# System Health
# ══════════════════════════════════════════════


@backend_router.get("/system/health")
async def system_health(session: dict[str, Any] = Depends(verify_session)) -> dict[str, Any]:
    """Full system health report."""
    return await SystemHealthMonitor.get_system_health()


# ══════════════════════════════════════════════
# Users (bot users from SQLite)
# ══════════════════════════════════════════════


@backend_router.get("/users")
async def list_users(
    session: dict[str, Any] = Depends(verify_session),
    limit: int = Query(100, ge=1, le=500),
    search: str = Query("", max_length=100),
) -> dict[str, Any]:
    """List bot users."""
    conn = _open_bot_db()
    if conn is None:
        return {"users": [], "total": 0, "status": "db_unavailable"}

    where = ""
    params: list[Any] = []
    if search:
        where = "WHERE username LIKE ? OR first_name LIKE ? OR CAST(user_id AS TEXT) LIKE ?"
        like = f"%{search}%"
        params = [like, like, like]

    try:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM users {where}", params).fetchone()["c"] or 0  # noqa: S608  # nosec B608 - where is code literal or ?-placeholder SQL; values bound via params
        rows = conn.execute(
            f"SELECT * FROM users {where} ORDER BY last_active DESC LIMIT ?",  # noqa: S608  # nosec B608 - where is code literal or ?-placeholder SQL; values bound via params
            [*params, limit],
        ).fetchall()
        users = [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.warning("admin_backend: users list failed: %s", exc)
        return {"users": [], "total": 0, "status": "error"}
    finally:
        conn.close()

    return {"users": users, "total": total}


@backend_router.post("/users/{user_id}/ban")
async def ban_user(
    user_id: int,
    req: BanUserRequest | None = None,
    session: dict[str, Any] = Depends(require_admin_session),
) -> dict[str, Any]:
    """Ban a bot user."""
    conn = _open_bot_db()
    if conn is None:
        raise HTTPException(status_code=503, detail="Bot DB unavailable")
    try:
        conn.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        conn.close()

    await AuditLogger.log(
        admin_id=session["admin_id"],
        admin_email=session["admin_email"],
        action="ban_user",
        resource_type="bot_user",
        resource_id=str(user_id),
        reason=req.reason if req else "",
    )
    return {"status": "banned", "user_id": user_id}


# ══════════════════════════════════════════════
# Security
# ══════════════════════════════════════════════


@backend_router.get("/security/blocked-ips")
async def blocked_ips(session: dict[str, Any] = Depends(verify_session)) -> dict[str, Any]:
    """List all blocked IPs."""
    try:
        r = _redis()
        ips = await r.smembers("blocked_ips:all")
        result = []
        for ip in ips:
            data = await r.get(f"blocked_ip:{ip}")
            if data:
                result.append(json.loads(data))
            else:
                await r.srem("blocked_ips:all", ip)
        return {"blocked_ips": result}
    except Exception:
        return {"blocked_ips": []}


@backend_router.post("/security/block-ip")
async def block_ip(
    req: BlockIPRequest,
    session: dict[str, Any] = Depends(require_admin_session),
) -> dict[str, Any]:
    """Block an IP address."""
    ok = await SecurityManager.block_ip(req.ip, req.reason, req.duration_hours)
    if not ok:
        raise HTTPException(status_code=500, detail="Block failed")
    await AuditLogger.log(
        admin_id=session["admin_id"],
        admin_email=session["admin_email"],
        action="block_ip",
        resource_type="security",
        resource_id=req.ip,
        reason=req.reason,
    )
    return {"status": "blocked", "ip": req.ip}


@backend_router.post("/security/unblock-ip")
async def unblock_ip(
    req: UnblockIPRequest,
    session: dict[str, Any] = Depends(require_admin_session),
) -> dict[str, Any]:
    """Unblock an IP address."""
    ok = await SecurityManager.unblock_ip(req.ip)
    if not ok:
        raise HTTPException(status_code=500, detail="Unblock failed")
    await AuditLogger.log(
        admin_id=session["admin_id"],
        admin_email=session["admin_email"],
        action="unblock_ip",
        resource_type="security",
        resource_id=req.ip,
    )
    return {"status": "unblocked", "ip": req.ip}


@backend_router.get("/security/audit-logs")
async def audit_logs(
    session: dict[str, Any] = Depends(verify_session),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """Query audit logs."""
    entries = await AuditLogger.query(limit=limit)
    return {"logs": [e.to_dict() for e in entries]}


# ══════════════════════════════════════════════
# Admins
# ══════════════════════════════════════════════


@backend_router.get("/admins")
async def list_admins(session: dict[str, Any] = Depends(verify_session)) -> dict[str, Any]:
    """List all admin users."""
    admins = await AdminUserStore.list_admins()
    # Strip sensitive fields
    safe = [{k: v for k, v in a.items() if k not in ("password_hash", "two_factor_secret")} for a in admins]
    return {"admins": safe}


@backend_router.post("/admins")
async def create_admin(
    req: CreateAdminRequest,
    session: dict[str, Any] = Depends(require_admin_session),
) -> dict[str, Any]:
    """Create a new admin user."""
    try:
        role = AdminRole(req.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {req.role}") from None

    admin = await AdminUserStore.create_admin(
        email=req.email,
        password=req.password,
        role=role,
        created_by=session["admin_email"],
    )
    if not admin:
        raise HTTPException(status_code=409, detail="Admin already exists")

    await AuditLogger.log(
        admin_id=session["admin_id"],
        admin_email=session["admin_email"],
        action="create_admin",
        resource_type="admin",
        resource_id=admin["id"],
    )
    return {"admin": admin}


# ══════════════════════════════════════════════
# API Keys
# ══════════════════════════════════════════════


@backend_router.get("/api-keys")
async def list_api_keys(session: dict[str, Any] = Depends(verify_session)) -> dict[str, Any]:
    """List all API keys."""
    try:
        r = _redis()
        keys = await r.hgetall("rmi:api_keys")
        result = []
        for key_id, data in keys.items():
            d = json.loads(data)
            d["id"] = key_id
            d.pop("key_hash", None)
            result.append(d)
        return {"api_keys": result}
    except Exception:
        return {"api_keys": []}


@backend_router.post("/api-keys")
async def create_api_key(
    req: CreateAPIKeyRequest,
    session: dict[str, Any] = Depends(require_admin_session),
) -> dict[str, Any]:
    """Create a new API key."""
    import hashlib

    key_id = f"key_{secrets.token_hex(8)}"
    api_key = f"rmi_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    data = {
        "name": req.name,
        "key_hash": key_hash,
        "scopes": req.scopes,
        "status": "active",
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": session["admin_email"],
        "expires_at": (datetime.now(UTC).timestamp() + req.expires_days * 86400),
    }

    try:
        r = _redis()
        await r.hset("rmi:api_keys", key_id, json.dumps(data))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Key creation failed") from exc

    await AuditLogger.log(
        admin_id=session["admin_id"],
        admin_email=session["admin_email"],
        action="create_api_key",
        resource_type="api_key",
        resource_id=key_id,
    )
    return {"id": key_id, "api_key": api_key, "name": req.name}


@backend_router.post("/api-keys/{key_id}/revoke")
async def revoke_api_key(
    key_id: str,
    session: dict[str, Any] = Depends(require_admin_session),
) -> dict[str, Any]:
    """Revoke an API key."""
    try:
        r = _redis()
        data = await r.hget("rmi:api_keys", key_id)
        if not data:
            raise HTTPException(status_code=404, detail="Key not found")
        d = json.loads(data)
        d["status"] = "revoked"
        d["revoked_at"] = datetime.now(UTC).isoformat()
        d["revoked_by"] = session["admin_email"]
        await r.hset("rmi:api_keys", key_id, json.dumps(d))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Revoke failed") from exc

    # Drop the auth middleware's cached valid-key set so the revocation takes
    # effect immediately instead of waiting for the TTL.
    try:
        from app.core.auth import _invalidate_admin_api_keys_cache

        _invalidate_admin_api_keys_cache()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("api_key_cache_invalidate_failed err=%s", exc)

    await AuditLogger.log(
        admin_id=session["admin_id"],
        admin_email=session["admin_email"],
        action="revoke_api_key",
        resource_type="api_key",
        resource_id=key_id,
    )
    return {"status": "revoked", "id": key_id}


@backend_router.post("/api-keys/{key_id}/rotate")
async def rotate_api_key(
    key_id: str,
    session: dict[str, Any] = Depends(require_admin_session),
) -> dict[str, Any]:
    """Rotate an API key: revoke the old key and issue a new one.

    The new key inherits the old key's name, scopes, and expiry window. The
    raw new key is returned exactly once (only its sha256 hash is stored), so
    callers must persist it immediately.
    """
    import hashlib

    try:
        r = _redis()
        data = await r.hget("rmi:api_keys", key_id)
        if not data:
            raise HTTPException(status_code=404, detail="Key not found")
        d = json.loads(data)

        # Revoke the old key.
        d["status"] = "revoked"
        d["revoked_at"] = datetime.now(UTC).isoformat()
        d["revoked_by"] = session["admin_email"]
        await r.hset("rmi:api_keys", key_id, json.dumps(d))

        # Issue a new key inheriting name/scopes/expiry.
        new_key_id = f"key_{secrets.token_hex(8)}"
        api_key = f"rmi_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        new_data = {
            "name": d.get("name", "Rotated key"),
            "key_hash": key_hash,
            "scopes": d.get("scopes", []),
            "status": "active",
            "created_at": datetime.now(UTC).isoformat(),
            "created_by": session["admin_email"],
            "expires_at": d.get("expires_at"),
            "rotated_from": key_id,
        }
        await r.hset("rmi:api_keys", new_key_id, json.dumps(new_data))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Key rotation failed") from exc

    # Drop the auth middleware's cached valid-key set so the old key is
    # rejected and the new key is accepted immediately.
    try:
        from app.core.auth import _invalidate_admin_api_keys_cache

        _invalidate_admin_api_keys_cache()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("api_key_cache_invalidate_failed err=%s", exc)

    await AuditLogger.log(
        admin_id=session["admin_id"],
        admin_email=session["admin_email"],
        action="rotate_api_key",
        resource_type="api_key",
        resource_id=new_key_id,
    )
    return {"status": "rotated", "old_id": key_id, "id": new_key_id, "api_key": api_key}


# ══════════════════════════════════════════════
# Webhooks
# ══════════════════════════════════════════════


@backend_router.get("/webhooks")
async def list_webhooks(session: dict[str, Any] = Depends(verify_session)) -> dict[str, Any]:
    """List all webhooks."""
    try:
        r = _redis()
        hooks = await r.hgetall("rmi:webhooks")
        result = []
        for hook_id, data in hooks.items():
            d = json.loads(data)
            d["id"] = hook_id
            result.append(d)
        return {"webhooks": result}
    except Exception:
        return {"webhooks": []}


@backend_router.post("/webhooks")
async def create_webhook(
    req: CreateWebhookRequest,
    session: dict[str, Any] = Depends(require_admin_session),
) -> dict[str, Any]:
    """Create a new webhook."""
    hook_id = f"wh_{secrets.token_hex(8)}"
    data = {
        "url": req.url,
        "events": req.events,
        "secret": req.secret or secrets.token_urlsafe(16),
        "active": True,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": session["admin_email"],
    }

    try:
        r = _redis()
        await r.hset("rmi:webhooks", hook_id, json.dumps(data))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Webhook creation failed") from exc

    await AuditLogger.log(
        admin_id=session["admin_id"],
        admin_email=session["admin_email"],
        action="create_webhook",
        resource_type="webhook",
        resource_id=hook_id,
    )
    return {"id": hook_id, "url": req.url, "secret": data["secret"]}


# ══════════════════════════════════════════════
# Content / Announcements
# ══════════════════════════════════════════════


@backend_router.get("/content/announcements")
async def list_announcements(
    session: dict[str, Any] = Depends(verify_session),
    active_only: bool = Query(False),
) -> dict[str, Any]:
    """List announcements."""
    try:
        r = _redis()
        items = await r.lrange("rmi:announcements", 0, 99)
        result = []
        for item in items:
            d = json.loads(item)
            if active_only and not d.get("active"):
                continue
            result.append(d)
        return {"announcements": result}
    except Exception:
        return {"announcements": []}


@backend_router.post("/content/announcements")
async def create_announcement(
    req: AnnouncementRequest,
    session: dict[str, Any] = Depends(require_admin_session),
) -> dict[str, Any]:
    """Create an announcement."""
    data = {
        "id": f"ann_{secrets.token_hex(6)}",
        "title": req.title,
        "body": req.body,
        "active": req.active,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": session["admin_email"],
    }

    try:
        r = _redis()
        await r.lpush("rmi:announcements", json.dumps(data))
        await r.ltrim("rmi:announcements", 0, 99)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Announcement creation failed") from exc

    await AuditLogger.log(
        admin_id=session["admin_id"],
        admin_email=session["admin_email"],
        action="create_announcement",
        resource_type="content",
        resource_id=data["id"],
    )
    return {"announcement": data}
