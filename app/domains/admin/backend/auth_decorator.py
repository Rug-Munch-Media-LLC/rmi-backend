"""
Admin Backend — Auth Decorator
================================
require_admin() decorator — verify admin authentication and authorization.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from app.domains.admin.backend.audit import AuditLogger
from app.domains.admin.backend.models import AdminRole, has_permission
from app.domains.admin.backend.security import SecurityManager
from app.domains.admin.backend.session import SessionManager
from app.domains.admin.backend.users import AdminUserStore


async def require_admin(
    request: Request,
    required_permission: str = "",
    min_role: AdminRole = AdminRole.VIEWER,
) -> dict[str, Any]:
    """
    Verify admin authentication and authorization.

    Usage:
        admin = await require_admin(request, "users.write", AdminRole.ADMIN)
    """
    session_token = request.headers.get("X-Admin-Session", "")
    if not session_token:
        raise HTTPException(status_code=401, detail="Admin session required")

    session = await SessionManager.validate_session(session_token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    client_ip = request.client.host if request.client else ""
    block_info = await SecurityManager.is_ip_blocked(client_ip)
    if block_info:
        raise HTTPException(status_code=403, detail="IP blocked")

    admin = await AdminUserStore.get_admin(session["admin_id"])
    if not admin or not admin.get("is_active", True):
        raise HTTPException(status_code=403, detail="Admin account inactive")

    role = AdminRole(admin.get("role", "viewer"))
    role_order = {
        AdminRole.VIEWER: 0,
        AdminRole.SUPPORT: 1,
        AdminRole.MODERATOR: 2,
        AdminRole.ADMIN: 3,
        AdminRole.SUPERADMIN: 4,
    }
    if role_order[role] < role_order[min_role]:
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    if required_permission and not has_permission(role, required_permission):
        raise HTTPException(status_code=403, detail="Permission denied")

    rate_check = await SecurityManager.check_rate_limit(
        f"admin:{admin['id']}",
        "admin_write" if required_permission.endswith(".write") else "default",
    )
    if not rate_check["allowed"]:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Reset at {rate_check['reset_at']}")

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="admin.access",
        resource_type="endpoint",
        resource_id=str(request.url.path),
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        session_id=session_token,
    )

    return {"admin": admin, "session": session, "role": role}
