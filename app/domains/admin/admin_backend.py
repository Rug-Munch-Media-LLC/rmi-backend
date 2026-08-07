"""
RMI Admin Backend - Complete Administration System
===================================================
This file has been modularized into app/domains/admin/backend/ package.
This stub re-exports everything for backward compatibility.
"""

from __future__ import annotations

from app.domains.admin.backend import (
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
