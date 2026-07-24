"""Database connection pooling - Redis + Postgres with auto-reconnect."""

from __future__ import annotations

import os

from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from app.config import settings as _s
    REDIS_URL = _s.redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    PG_URL = _s.database_url or os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
except Exception:
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    PG_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")

_redis_pool = None
_pg_pool = None
