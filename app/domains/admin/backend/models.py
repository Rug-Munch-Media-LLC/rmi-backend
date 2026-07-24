"""
Admin Backend — Data Models
============================
Role definitions, permission matrix, and audit log entry model.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from app.core.logging import get_logger

logger = get_logger("rmi_admin_backend")

class AdminRole(StrEnum):
    """Admin role hierarchy. Higher = more permissions."""

    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    MODERATOR = "moderator"
    VIEWER = "viewer"
    SUPPORT = "support"

# Permission matrix: role -> set of allowed permissions
PERMISSIONS = {
    AdminRole.SUPERADMIN: {"*"},
    AdminRole.ADMIN: {
        "dashboard.read", "users.read", "users.write", "users.ban",
        "content.read", "content.write", "system.read", "system.write",
        "security.read", "security.write", "financial.read", "financial.write",
        "api_keys.read", "api_keys.write", "webhooks.read", "webhooks.write",
        "backups.read", "backups.write", "settings.read", "settings.write",
        "logs.read", "analytics.read", "token_deploy.read", "token_deploy.write",
    },
    AdminRole.MODERATOR: {
        "dashboard.read", "users.read", "users.write", "users.ban",
        "content.read", "content.write", "security.read", "logs.read", "analytics.read",
    },
    AdminRole.VIEWER: {
        "dashboard.read", "users.read", "content.read", "system.read",
        "security.read", "financial.read", "analytics.read", "logs.read",
    },
    AdminRole.SUPPORT: {
        "dashboard.read", "users.read", "users.write", "content.read",
        "logs.read", "analytics.read",
    },
}

def has_permission(role: AdminRole, permission: str) -> bool:
    """Check if a role has a specific permission."""
    if role == AdminRole.SUPERADMIN:
        return True
    perms = PERMISSIONS.get(role, set())
    return permission in perms or "*" in perms

@dataclass
class AuditLogEntry:
    """Single audit log entry."""

    entry_id: str
    timestamp: str
    admin_id: str
    admin_email: str
    action: str
    resource_type: str
    resource_id: str
    ip_address: str
    user_agent: str
    before_state: dict | None = None
    after_state: dict | None = None
    status: str = "success"
    reason: str = ""
    session_id: str = ""
    request_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
