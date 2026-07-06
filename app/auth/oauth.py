"""OAuth flows - re-exports from app.auth.

Phase 3B of AUDIT-2026-Q3.md.

Public API:
- google_auth_url, google_callback, google_callback_post
- github_auth_url, github_callback
- x_auth_url, x_callback
- telegram_auth
"""
from app.auth import (  # noqa: F401
    github_auth_url,
    github_callback,
    google_auth_url,
    google_callback,
    google_callback_post,
    telegram_auth,
    x_auth_url,
    x_callback,
)
