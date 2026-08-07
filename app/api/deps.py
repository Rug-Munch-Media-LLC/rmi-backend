"""Shared FastAPI dependencies.

Use these in route signatures to inject cross-cutting concerns:
    from app.api.deps import get_redis, get_current_user, get_settings

Actual implementations live in `app/core/`. This module is a re-export
facade so route authors don't need to know which core module owns what.
"""

from __future__ import annotations

import contextlib

# Re-exports - actual implementations come from app/core/.
# Core modules are populated by the parallel DeepSeek tasks (DS-1..DS-10).
# Until then, these imports will fail; routes should not depend on them yet.
try:
    from app.core.redis import get_redis
except ImportError:
    get_redis = None  # type: ignore[assignment]

try:
    from app.core.db import get_db
except ImportError:
    get_db = None  # type: ignore[assignment]

try:
    from app.core.auth import get_current_user, get_optional_user
except ImportError:
    get_current_user = None  # type: ignore[assignment]
    get_optional_user = None  # type: ignore[assignment]

with contextlib.suppress(ImportError):
