"""Database connection pooling - Redis + Postgres with auto-reconnect."""

import os

from app.core.logging import get_logger

logger = get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PG_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")

_redis_pool = None
_pg_pool = None
