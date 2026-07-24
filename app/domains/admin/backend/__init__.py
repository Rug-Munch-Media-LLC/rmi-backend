"""
RMI Admin Backend - Split into modular components.
Re-exports all public symbols for backward compatibility.
"""
from app.domains.admin.backend.audit import AuditLogger
from app.domains.admin.backend.auth_decorator import require_admin
from app.domains.admin.backend.health import SystemHealthMonitor
from app.domains.admin.backend.models import PERMISSIONS, AdminRole, AuditLogEntry, has_permission
from app.domains.admin.backend.security import SecurityManager
from app.domains.admin.backend.session import SessionManager
from app.domains.admin.backend.users import AdminUserStore

__all__ = [
    "PERMISSIONS",
    "AdminRole",
    "AdminUserStore",
    "AuditLogEntry",
    "AuditLogger",
    "SecurityManager",
    "SessionManager",
    "SystemHealthMonitor",
    "has_permission",
    "require_admin",
]
