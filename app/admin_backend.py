"""
Admin Backend
=============
Administrative backend for platform management, including user management,
audit logging, and system configuration.

Backward-compatibility shim: this module now lives in ``app.domains.admin``.

Usage:
    from app.admin_backend import AuditLogger, require_admin, SecurityManager
"""

from __future__ import annotations

from app.domains.admin.admin_backend import (
    PERMISSIONS,
    AdminRole,
    AdminUserStore,
    AuditLogEntry,
    AuditLogger,
    SecurityManager,
    SessionManager,
    SystemHealthMonitor,
    has_permission,
    require_admin,
)

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
