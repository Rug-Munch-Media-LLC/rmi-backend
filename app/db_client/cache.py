"""
RMI Database Client - Redis Cache Helpers
==========================================
Write-through and read-through caching with Redis.
"""

from __future__ import annotations

import asyncio
import json
import os
from functools import wraps
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

# ═══════════════════════════════════════════════════════════
# RETRY UTILITIES
# ═══════════════════════════════════════════════════════════


def _async_retry(max_retries: int = 3, delay: float = 0.5, backoff: float = 2.0) -> Any:
    """Simple async retry decorator for Supabase operations."""

    def decorator(func: Callable) -> Any:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exc = None
            wait = delay
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt >= max_retries:
                        raise
                    logger.warning(f"[DB] Retry {attempt + 1}/{max_retries} for {func.__name__}: {exc}")
                    await asyncio.sleep(wait)
                    wait *= backoff
            raise last_exc

        return wrapper

    return decorator


# ═══════════════════════════════════════════════════════════
# REDIS CACHE HELPERS
# ═══════════════════════════════════════════════════════════

_CACHE_TTL = 300  # 5 minutes default


async def _get_redis() -> Any | None:
    """Lazy Redis client."""
    if redis is None:
        return None
    try:
        host = os.getenv("REDIS_HOST", "127.0.0.1")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD") or None
        db = int(os.getenv("REDIS_DB", "0"))
        return redis.Redis(host=host, port=port, password=password, db=db, decode_responses=True)
    except Exception as exc:
        logger.warning("[DB] Redis init failed: %s", exc)
        return None


async def cache_set(key: str, value: Any, ttl: int = _CACHE_TTL) -> None:
    r = await _get_redis()
    if r is None:
        return
    try:
        await r.set(key, json.dumps(value), ex=ttl)
    except Exception as exc:
        logger.warning("[DB] cache_set failed: %s", exc)


async def cache_get(key: str) -> Any | None:
    r = await _get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("[DB] cache_get failed: %s", exc)
        return None


async def cache_delete(key: str) -> None:
    r = await _get_redis()
    if r is None:
        return
    try:
        await r.delete(key)
    except Exception as exc:
        logger.warning("[DB] cache_delete failed: %s", exc)


async def cache_hash_set(hash_name: str, field: str, value: Any) -> None:
    r = await _get_redis()
    if r is None:
        return
    try:
        await r.hset(hash_name, field, json.dumps(value))
    except Exception as exc:
        logger.warning("[DB] cache_hash_set failed: %s", exc)


async def cache_hash_get_all(hash_name: str) -> dict[str, Any]:
    r = await _get_redis()
    if r is None:
        return {}
    try:
        raw = await r.hgetall(hash_name)
        return {k: json.loads(v) for k, v in raw.items()}
    except Exception as exc:
        logger.warning("[DB] cache_hash_get_all failed: %s", exc)
        return {}


# ═══════════════════════════════════════════════════════════
# MIGRATION HELPER
# ═══════════════════════════════════════════════════════════


async def sync_redis_to_supabase() -> dict[str, int]:
    """
    Read all data from Redis hashes and upsert into Supabase tables.
    Returns counts of synced records per table.
    """
    # Local imports to avoid circular dependency
    from .client import RmiDatabase as _RmiDatabase
    from .models import Alert, InvestigationCase, TrenchesPost

    counts = {"cases": 0, "trenches_posts": 0, "alerts": 0, "tasks": 0}
    r = await _get_redis()
    if r is None:
        logger.warning("[DB] Redis unavailable; nothing to migrate")
        return counts

    db = _RmiDatabase()

    # ── Cases ──
    try:
        cases_raw = await r.hgetall("rmi:cases")
        for case_json in cases_raw.values():
            try:
                data = json.loads(case_json)
                case = InvestigationCase(
                    id=data.get("id", ""),
                    target=data.get("target", ""),
                    type=data.get("type", "wallet"),
                    status=data.get("status", "open"),
                    evidence=data.get("evidence") or data.get("evidence", []),
                    agents_assigned=data.get("agents_assigned") or data.get("agents_assigned", []),
                    created_at=data.get("created_at") or data.get("created"),
                    updated_at=data.get("updated_at") or data.get("updated") or data.get("created"),
                    risk_score=float(data.get("risk_score", 0)),
                    findings=data.get("findings") or {},
                )
                await db.cases.create(case)
                counts["cases"] += 1
            except Exception as exc:
                logger.warning("[DB] Failed to migrate case: %s", exc)
    except Exception as exc:
        logger.warning("[DB] Cases migration error: %s", exc)

    # ── Trenches Posts ──
    try:
        posts_raw = await r.hgetall("rmi:trenches:posts")
        for post_json in posts_raw.values():
            try:
                data = json.loads(post_json)
                post = TrenchesPost(
                    id=data.get("id"),
                    title=data.get("title", ""),
                    content=data.get("content", ""),
                    category=data.get("category", "discussion"),
                    author_id=data.get("author_id"),
                    upvotes=int(data.get("upvotes", 0)),
                    comments=int(data.get("comments", 0)),
                    tags=data.get("tags") or [],
                    created_at=data.get("created_at") or data.get("created"),
                )
                await db.trenches_posts.create(post)
                counts["trenches_posts"] += 1
            except Exception as exc:
                logger.warning("[DB] Failed to migrate post: %s", exc)
    except Exception as exc:
        logger.warning("[DB] Posts migration error: %s", exc)

    # ── Alerts ──
    try:
        alerts_raw = await r.hgetall("rmi:alerts")
        for alert_json in alerts_raw.values():
            try:
                data = json.loads(alert_json)
                alert = Alert(
                    id=data.get("id"),
                    token_address=data.get("token") or data.get("token_address", ""),
                    types=data.get("types") or data.get("alert_types") or [],
                    webhook_url=data.get("webhook") or data.get("webhook_url"),
                    active=data.get("active", True),
                    created_at=data.get("created_at") or data.get("created"),
                )
                await db.alerts.create(alert)
                counts["alerts"] += 1
            except Exception as exc:
                logger.warning("[DB] Failed to migrate alert: %s", exc)
    except Exception as exc:
        logger.warning("[DB] Alerts migration error: %s", exc)

    # ── Tasks (migrate as cases with type=task) ──
    try:
        tasks_raw = await r.hgetall("rmi:tasks")
        for task_json in tasks_raw.values():
            try:
                data = json.loads(task_json)
                case = InvestigationCase(
                    id=data.get("id", ""),
                    target=data.get("command", "task"),
                    type="task",
                    status=data.get("status", "open"),
                    evidence=[],
                    agents_assigned=[data.get("agent", "")] if data.get("agent") else [],
                    created_at=data.get("created") or data.get("created_at"),
                    updated_at=data.get("created") or data.get("created_at"),
                    risk_score=0.0,
                    findings={
                        "context": data.get("context", {}),
                        "priority": data.get("priority", "normal"),
                    },
                )
                await db.cases.create(case)
                counts["tasks"] += 1
            except Exception as exc:
                logger.warning("[DB] Failed to migrate task: %s", exc)
    except Exception as exc:
        logger.warning("[DB] Tasks migration error: %s", exc)

    logger.info("[DB] Migration complete: %s", counts)
    return counts
