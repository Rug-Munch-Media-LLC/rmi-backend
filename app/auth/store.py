"""User storage helpers - re-exports from app.auth.

Phase 3B of AUDIT-2026-Q3.md.

Public API:
- _get_user, _get_user_by_email, _save_user, _delete_user
- _is_valid_email
"""
from app.auth import (  # noqa: F401
    _delete_user,
    _get_user,
    _get_user_by_email,
    _is_valid_email,
    _save_user,
)
