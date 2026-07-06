"""FastAPI dependencies - re-exports from app.domains.auth.

Phase 3B of AUDIT-2026-Q3.md.

Public API:
- get_current_user, require_auth, require_public_profile
"""
from app.auth import (  # noqa: F401
    get_current_user,
    require_auth,
    require_public_profile,
)
