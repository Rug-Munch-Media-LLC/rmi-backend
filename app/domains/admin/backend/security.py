"""
Admin Backend — Security Manager
==================================
Rate limiting, IP blocking, failed login tracking.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app.core.logging import get_logger

logger = get_logger("rmi_admin_backend")


class SecurityManager:
    """
    Security hardening for admin backend.
    - Rate limiting
    - IP blocking
    - Failed login tracking
    - Session management
    """

    RATE_LIMITS = {
        "default": (60, 60),
        "login": (5, 300),
        "admin_write": (30, 60),
        "token_deploy": (10, 3600),
        "snapshot": (20, 3600),
        "airdrop": (5, 3600),
    }

    @staticmethod
    async def check_rate_limit(
        identifier: str,
        limit_type: str = "default",
    ) -> dict[str, Any]:
        """Check if request is within rate limit."""
        max_req, window = SecurityManager.RATE_LIMITS.get(limit_type, (60, 60))
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"rate_limit:{limit_type}:{identifier}"
            now = int(time.time())
            await r.zremrangebyscore(key, 0, now - window)
            count = await r.zcard(key)

            if count >= max_req:
                oldest = await r.zrange(key, 0, 0, withscores=True)
                reset_at = (cast("float", oldest[0][1]) + window) if oldest else now + window
                return {
                    "allowed": False,
                    "remaining": 0,
                    "reset_at": reset_at,
                    "limit": max_req,
                    "window": window,
                }

            await r.zadd(key, {str(now): now})
            await r.expire(key, window)
            return {
                "allowed": True,
                "remaining": max_req - count - 1,
                "reset_at": now + window,
                "limit": max_req,
                "window": window,
            }

        except (ConnectionError, OSError, ValueError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Rate limit check failed: {e}")
            return {"allowed": True, "remaining": -1, "reset_at": 0}

    @staticmethod
    async def block_ip(ip: str, reason: str = "", duration_hours: int = 24) -> bool:
        """Block an IP address."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"blocked_ip:{ip}"
            data = {
                "ip": ip,
                "reason": reason,
                "blocked_at": datetime.now(UTC).isoformat(),
                "expires_at": (datetime.now(UTC) + timedelta(hours=duration_hours)).isoformat(),
                "duration_hours": duration_hours,
            }
            await r.setex(key, duration_hours * 3600, json.dumps(data))
            await r.sadd("blocked_ips:all", ip)
            logger.warning(f"IP blocked: {ip} for {duration_hours}h - {reason}")
            return True
        except (json.JSONDecodeError, ValueError, TypeError, ConnectionError, OSError) as e:
            logger.debug("block_ip_error", exc_info=True)
            logger.error(f"IP block failed: {e}")
            return False

    @staticmethod
    async def unblock_ip(ip: str) -> bool:
        """Unblock an IP address."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            await r.delete(f"blocked_ip:{ip}")
            await r.srem("blocked_ips:all", ip)
            return True
        except (ConnectionError, OSError, ValueError) as e:
            logger.debug("unblock_ip_error", exc_info=True)
            logger.error(f"IP unblock failed: {e}")
            return False

    @staticmethod
    async def is_ip_blocked(ip: str) -> dict[str, Any] | None:
        """Check if IP is blocked. Returns block info or None."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            data = await r.get(f"blocked_ip:{ip}")
            return json.loads(data) if data else None
        except (json.JSONDecodeError, ConnectionError, OSError, ValueError, KeyError, TypeError) as e:
            logger.debug("is_ip_blocked_error", exc_info=True)
            logger.error(f"IP block check failed: {e}")
            return None

    @staticmethod
    async def track_failed_login(identifier: str) -> int:
        """Track failed login attempts. Returns current count."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            key = f"failed_logins:{identifier}"
            count = await r.incr(key)
            await r.expire(key, 3600)
            if count >= 5:
                ip = identifier.split(":")[-1] if ":" in identifier else identifier
                await SecurityManager.block_ip(ip, "Too many failed login attempts", 1)
            return count
        except (ConnectionError, OSError, ValueError) as e:
            logger.debug("track_failed_login_error", exc_info=True)
            logger.error(f"Failed login tracking error: {e}")
            return 0

    @staticmethod
    async def reset_failed_login(identifier: str) -> bool:
        """Reset failed login counter (on successful login)."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )
            await r.delete(f"failed_logins:{identifier}")
            return True
        except (ConnectionError, OSError, ValueError) as e:
            logger.debug("reset_failed_login_error", exc_info=True)
            logger.error(f"Reset failed login error: {e}")
            return False
