"""
RMI Admin User Management API
=============================
Admin endpoints for managing users:
  - List all users with filters (tier, role, status, date range)
  - Get user details
  - Warn user (add warning note)
  - Flag user as suspicious
  - Ban / unban user
  - Delete user (soft delete)
  - Update user tier/role
  - Bulk actions

All endpoints require ADMIN or SUPERADMIN role.
"""

import json
import logging
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("rmi_admin_users")
router = APIRouter(tags=["admin-users"])


# ── Redis helper ──
async def require_admin(request: Request, min_role: str = "ADMIN"):
    """Require admin authentication."""
    from app.auth import get_current_user

    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    role = user.get("role", "USER")
    user.get("tier", "FREE")

    # Role hierarchy
    role_levels = {"USER": 0, "MODERATOR": 1, "ADMIN": 2, "SUPERADMIN": 3}
    required_level = role_levels.get(min_role, 2)
    user_level = role_levels.get(role, 0)

    if user_level < required_level:
        raise HTTPException(status_code=403, detail="Admin access required")

    return user


# ── Models ──
class UserStatus(StrEnum):
    ACTIVE = "active"
    WARNED = "warned"
    SUSPECT = "suspect"
    BANNED = "banned"
    DELETED = "deleted"


class UserListFilters(BaseModel):
    tier: str | None = None
    role: str | None = None
    status: UserStatus | None = None
    search: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)


class WarnUserRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)
    severity: str = Field("medium", pattern="^(low|medium|high|critical)$")


class UpdateUserRequest(BaseModel):
    tier: str | None = None
    role: str | None = None
    status: UserStatus | None = None
    display_name: str | None = None
    scans_remaining: int | None = None
    notes: str | None = None


class BulkActionRequest(BaseModel):
    user_ids: list[str]
    action: str = Field(..., pattern="^(warn|ban|unban|flag_suspect|clear_suspect|delete|restore|update_tier)$")
    params: dict[str, Any] | None = None


class UserAdminResponse(BaseModel):
    id: str
    email: str
    display_name: str
    wallet_address: str | None
    wallet_chain: str | None
    tier: str
    role: str
    status: str
    created_at: str
    last_login: str | None
    xp: int
    level: int
    scans_used: int
    scans_remaining: int
    warnings: list[dict]
    notes: str | None
    login_methods: list[str]


# ── Helper: Get all users from Redis ──
def _get_all_users() -> list[dict]:
    """Fetch all users from Redis."""
    r = get_redis()
    users_raw = r.hgetall("rmi:users")
    users = []
    for user_id, data in users_raw.items():
        try:
            user = json.loads(data)
            user["id"] = user_id
            users.append(user)
        except json.JSONDecodeError:
            continue
    return users


def _get_user_full(user_id: str) -> dict | None:
    """Get user with enriched data."""
    from app.auth import _get_user

    user = _get_user(user_id)
    if not user:
        return None

    r = get_redis()

    # Get warnings
    warnings_raw = r.hget("rmi:user_warnings", user_id)
    user["warnings"] = json.loads(warnings_raw) if warnings_raw else []

    # Get notes
    user["notes"] = r.hget("rmi:user_notes", user_id) or ""

    # Get status
    user["status"] = r.hget("rmi:user_status", user_id) or "active"

    # Get last login
    user["last_login"] = r.hget("rmi:user_last_login", user_id)

    # Get login methods
    wallets_raw = r.hget("rmi:user_wallets", user_id)
    wallets = json.loads(wallets_raw) if wallets_raw else []
    login_methods = ["email"] if user.get("password_hash") else []
    if user.get("wallet_address"):
        login_methods.append("wallet")
    if "google" in user.get("email", ""):
        login_methods.append("google")

    user["login_methods"] = login_methods
    user["wallet_count"] = len(wallets)

    return user


# ── Endpoints ──


@router.get("/admin/users")
async def list_users(
    request: Request,
    tier: str | None = Query(None),
    role: str | None = Query(None),
    status: str | None = Query(None),
    search: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """List all users with filtering and pagination."""
    await require_admin(request)

    users = _get_all_users()

    # Apply filters
    filtered = []
    for u in users:
        # Skip deleted unless specifically filtering
        user_status = get_redis().hget("rmi:user_status", u["id"]) or "active"
        if user_status == "deleted" and status != "deleted":
            continue

        if tier and u.get("tier", "FREE") != tier.upper():
            continue
        if role and u.get("role", "USER") != role.upper():
            continue
        if status and user_status != status:
            continue
        if search:
            search_lower = search.lower()
            match = (
                search_lower in u.get("email", "").lower()
                or search_lower in u.get("display_name", "").lower()
                or search_lower in u.get("wallet_address", "").lower()
                or search_lower in u.get("id", "").lower()
            )
            if not match:
                continue
        if date_from and u.get("created_at", "") < date_from:
            continue
        if date_to and u.get("created_at", "") > date_to:
            continue

        # Enrich
        u["status"] = user_status
        u["warnings_count"] = len(json.loads(get_redis().hget("rmi:user_warnings", u["id"]) or "[]"))
        filtered.append(u)

    # Sort
    reverse = sort_order == "desc"
    filtered.sort(key=lambda x: x.get(sort_by, ""), reverse=reverse)

    total = len(filtered)
    paginated = filtered[offset : offset + limit]

    # Clean response
    results = []
    for u in paginated:
        results.append(
            {
                "id": u["id"],
                "email": u.get("email", ""),
                "display_name": u.get("display_name", u.get("email", "")),
                "wallet_address": u.get("wallet_address"),
                "wallet_chain": u.get("wallet_chain"),
                "tier": u.get("tier", "FREE"),
                "role": u.get("role", "USER"),
                "status": u.get("status", "active"),
                "created_at": u.get("created_at"),
                "last_login": u.get("last_login"),
                "xp": u.get("xp", 0),
                "level": u.get("level", 1),
                "scans_used": u.get("scans_used", 0),
                "scans_remaining": u.get("scans_remaining", 5),
                "warnings_count": u.get("warnings_count", 0),
                "login_methods": ["email"] if u.get("password_hash") else ["wallet"] if u.get("wallet_address") else [],
            }
        )

    return {
        "users": results,
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters_applied": {
            "tier": tier,
            "role": role,
            "status": status,
            "search": search,
        },
    }


@router.get("/admin/users/{user_id}")
async def get_user_detail(user_id: str, request: Request):
    """Get detailed info about a specific user."""
    await require_admin(request)

    user = _get_user_full(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


@router.post("/admin/users/{user_id}/warn")
async def warn_user(user_id: str, req: WarnUserRequest, request: Request):
    """Add a warning to a user."""
    admin = await require_admin(request, min_role="MODERATOR")

    from app.auth import _get_user

    user = _get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    r = get_redis()
    warnings_raw = r.hget("rmi:user_warnings", user_id)
    warnings = json.loads(warnings_raw) if warnings_raw else []

    warning = {
        "id": secrets.token_hex(8),
        "reason": req.reason,
        "severity": req.severity,
        "issued_by": admin["id"],
        "issued_at": datetime.utcnow().isoformat(),
        "acknowledged": False,
    }
    warnings.append(warning)
    r.hset("rmi:user_warnings", user_id, json.dumps(warnings))

    # Update status to warned
    if req.severity in ("high", "critical"):
        r.hset("rmi:user_status", user_id, "warned")

    logger.info(f"[ADMIN] User {user_id} warned by {admin['id']}: {req.reason}")

    return {"status": "ok", "warning": warning}


@router.post("/admin/users/{user_id}/flag-suspect")
async def flag_suspect(user_id: str, request: Request, reason: str | None = Query(None)):
    """Flag a user as suspicious."""
    admin = await require_admin(request, min_role="MODERATOR")

    from app.auth import _get_user

    user = _get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    r = get_redis()
    r.hset("rmi:user_status", user_id, "suspect")

    # Add note
    if reason:
        existing = r.hget("rmi:user_notes", user_id) or ""
        note = f"[{datetime.utcnow().isoformat()}] FLAGGED SUSPECT by {admin['id']}: {reason}\n"
        r.hset("rmi:user_notes", user_id, existing + note)

    logger.info(f"[ADMIN] User {user_id} flagged as suspect by {admin['id']}")

    return {"status": "ok", "message": "User flagged as suspect"}


@router.post("/admin/users/{user_id}/clear-suspect")
async def clear_suspect(user_id: str, request: Request):
    """Clear suspect flag from a user."""
    admin = await require_admin(request, min_role="MODERATOR")

    r = get_redis()
    current = r.hget("rmi:user_status", user_id)
    if current == "suspect":
        r.hset("rmi:user_status", user_id, "active")

    logger.info(f"[ADMIN] User {user_id} suspect flag cleared by {admin['id']}")

    return {"status": "ok", "message": "Suspect flag cleared"}


@router.post("/admin/users/{user_id}/ban")
async def ban_user(user_id: str, request: Request, reason: str | None = Query(None)):
    """Ban a user."""
    admin = await require_admin(request, min_role="ADMIN")

    from app.auth import _get_user

    user = _get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent self-ban
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot ban yourself")

    r = get_redis()
    r.hset("rmi:user_status", user_id, "banned")

    if reason:
        existing = r.hget("rmi:user_notes", user_id) or ""
        note = f"[{datetime.utcnow().isoformat()}] BANNED by {admin['id']}: {reason}\n"
        r.hset("rmi:user_notes", user_id, existing + note)

    # Invalidate sessions (optional: delete JWTs)
    logger.info(f"[ADMIN] User {user_id} banned by {admin['id']}: {reason}")

    return {"status": "ok", "message": "User banned"}


@router.post("/admin/users/{user_id}/unban")
async def unban_user(user_id: str, request: Request):
    """Unban a user."""
    admin = await require_admin(request, min_role="ADMIN")

    r = get_redis()
    r.hset("rmi:user_status", user_id, "active")

    logger.info(f"[ADMIN] User {user_id} unbanned by {admin['id']}")

    return {"status": "ok", "message": "User unbanned"}


@router.post("/admin/users/{user_id}/delete")
async def delete_user(user_id: str, request: Request, reason: str | None = Query(None)):
    """Soft delete a user."""
    admin = await require_admin(request, min_role="ADMIN")

    from app.auth import _get_user, _save_user

    user = _get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    # Soft delete: mark as deleted but keep data
    user["deleted_at"] = datetime.utcnow().isoformat()
    user["deleted_by"] = admin["id"]
    user["delete_reason"] = reason or ""
    _save_user(user)

    r = get_redis()
    r.hset("rmi:user_status", user_id, "deleted")

    logger.info(f"[ADMIN] User {user_id} soft-deleted by {admin['id']}: {reason}")

    return {"status": "ok", "message": "User deleted"}


@router.post("/admin/users/{user_id}/restore")
async def restore_user(user_id: str, request: Request):
    """Restore a soft-deleted user."""
    admin = await require_admin(request, min_role="ADMIN")

    from app.auth import _get_user, _save_user

    user = _get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.pop("deleted_at", None)
    user.pop("deleted_by", None)
    user.pop("delete_reason", None)
    _save_user(user)

    r = get_redis()
    r.hset("rmi:user_status", user_id, "active")

    logger.info(f"[ADMIN] User {user_id} restored by {admin['id']}")

    return {"status": "ok", "message": "User restored"}


@router.patch("/admin/users/{user_id}")
async def update_user(user_id: str, req: UpdateUserRequest, request: Request):
    """Update user properties (tier, role, status, etc.)."""
    admin = await require_admin(request, min_role="ADMIN")

    from app.auth import _get_user, _save_user

    user = _get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    changes = []

    if req.tier is not None:
        old = user.get("tier", "FREE")
        user["tier"] = req.tier.upper()
        changes.append(f"tier: {old} -> {req.tier.upper()}")

    if req.role is not None:
        old = user.get("role", "USER")
        # Prevent promoting to superadmin unless you're superadmin
        if req.role.upper() == "SUPERADMIN" and admin.get("role") != "SUPERADMIN":
            raise HTTPException(status_code=403, detail="Only superadmin can assign superadmin role")
        user["role"] = req.role.upper()
        changes.append(f"role: {old} -> {req.role.upper()}")

    if req.status is not None:
        r = get_redis()
        r.hset("rmi:user_status", user_id, req.status.value)
        changes.append(f"status: -> {req.status.value}")

    if req.display_name is not None:
        user["display_name"] = req.display_name[:50]
        changes.append("display_name updated")

    if req.scans_remaining is not None:
        user["scans_remaining"] = req.scans_remaining
        changes.append(f"scans_remaining: -> {req.scans_remaining}")

    if req.notes is not None:
        r = get_redis()
        existing = r.hget("rmi:user_notes", user_id) or ""
        note = f"[{datetime.utcnow().isoformat()}] {admin['id']}: {req.notes}\n"
        r.hset("rmi:user_notes", user_id, existing + note)
        changes.append("notes added")

    user["updated_at"] = datetime.utcnow().isoformat()
    user["updated_by"] = admin["id"]
    _save_user(user)

    logger.info(f"[ADMIN] User {user_id} updated by {admin['id']}: {', '.join(changes)}")

    return {"status": "ok", "changes": changes}


@router.post("/admin/users/bulk")
async def bulk_action(req: BulkActionRequest, request: Request):
    """Perform bulk actions on multiple users."""
    admin = await require_admin(request, min_role="ADMIN")

    results = {"success": [], "failed": []}

    for user_id in req.user_ids:
        try:
            if req.action == "ban":
                r = get_redis()
                r.hset("rmi:user_status", user_id, "banned")
                results["success"].append({"id": user_id, "action": "banned"})
            elif req.action == "unban":
                r = get_redis()
                r.hset("rmi:user_status", user_id, "active")
                results["success"].append({"id": user_id, "action": "unbanned"})
            elif req.action == "flag_suspect":
                r = get_redis()
                r.hset("rmi:user_status", user_id, "suspect")
                results["success"].append({"id": user_id, "action": "flagged_suspect"})
            elif req.action == "clear_suspect":
                r = get_redis()
                r.hset("rmi:user_status", user_id, "active")
                results["success"].append({"id": user_id, "action": "cleared_suspect"})
            elif req.action == "delete":
                from app.auth import _get_user, _save_user

                user = _get_user(user_id)
                if user:
                    user["deleted_at"] = datetime.utcnow().isoformat()
                    user["deleted_by"] = admin["id"]
                    _save_user(user)
                    r = get_redis()
                    r.hset("rmi:user_status", user_id, "deleted")
                results["success"].append({"id": user_id, "action": "deleted"})
            elif req.action == "restore":
                from app.auth import _get_user, _save_user

                user = _get_user(user_id)
                if user:
                    user.pop("deleted_at", None)
                    user.pop("deleted_by", None)
                    _save_user(user)
                    r = get_redis()
                    r.hset("rmi:user_status", user_id, "active")
                results["success"].append({"id": user_id, "action": "restored"})
            elif req.action == "update_tier":
                tier = req.params.get("tier", "FREE") if req.params else "FREE"
                from app.auth import _get_user, _save_user

                user = _get_user(user_id)
                if user:
                    user["tier"] = tier
                    _save_user(user)
                results["success"].append({"id": user_id, "action": f"tier_updated_to_{tier}"})
        except Exception as e:
            results["failed"].append({"id": user_id, "error": str(e)})

    logger.info(
        f"[ADMIN] Bulk action {req.action} by {admin['id']}: {len(results['success'])} success, {len(results['failed'])} failed"
    )

    return results


@router.get("/admin/stats")
async def get_admin_stats(request: Request):
    """Get user statistics for admin dashboard."""
    await require_admin(request, min_role="VIEWER")

    users = _get_all_users()
    r = get_redis()

    stats = {
        "total_users": len(users),
        "by_tier": {},
        "by_role": {},
        "by_status": {"active": 0, "warned": 0, "suspect": 0, "banned": 0, "deleted": 0},
        "new_today": 0,
        "new_this_week": 0,
        "new_this_month": 0,
    }

    today = datetime.utcnow().date().isoformat()
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    month_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()

    for u in users:
        tier = u.get("tier", "FREE")
        role = u.get("role", "USER")
        stats["by_tier"][tier] = stats["by_tier"].get(tier, 0) + 1
        stats["by_role"][role] = stats["by_role"].get(role, 0) + 1

        status = r.hget("rmi:user_status", u["id"]) or "active"
        stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

        created = u.get("created_at", "")
        if created.startswith(today):
            stats["new_today"] += 1
        if created >= week_ago:
            stats["new_this_week"] += 1
        if created >= month_ago:
            stats["new_this_month"] += 1

    return stats
