"""Password helpers - re-exports from app.domains.auth.

Phase 3B of AUDIT-2026-Q3.md.

Public API:
- hash_password, verify_password
"""

from app.domains.auth import (  # noqa: F401
    hash_password,
    verify_password,
)
