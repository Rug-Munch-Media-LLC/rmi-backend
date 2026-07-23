"""
Admin Backend
=============
Administrative backend for platform management, including user management,
audit logging, and system configuration.

Backward-compatibility shim: this module now lives in ``app.domains.admin``.

Usage:
    from app.admin_backend import AuditLogger, require_admin, SecurityManager
"""

from app.domains.admin.admin_backend import *  # noqa: F401, F403
