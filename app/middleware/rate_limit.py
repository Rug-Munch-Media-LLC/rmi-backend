"""
Rate Limiting Middleware
========================

Redis-backed rate limiting for API endpoints.
Supports:
- Per-endpoint rate limits
- Tier-based limits (FREE, BASIC, PREMIUM, ENTERPRISE)
- Burst protection
- Automatic key expiration
"""

from app.core.logging import get_logger

import redis
from fastapi import Request

logger = get_logger(__name__)


# ─── RATE LIMIT CONFIGURATION ────────────────────────────────────

RATE_LIMITS = {
    "FREE": {"rph": 10, "rpd": 100, "burst": 2},  # requests per hour, per day, burst
    "BASIC": {"rph": 50, "rpd": 500, "burst": 5},
    "PREMIUM": {"rph": 200, "rpd": 2000, "burst": 10},
    "ENTERPRISE": {"rph": 1000, "rpd": 10000, "burst": 50},
}

# Default limits if tier not found
DEFAULT_LIMITS = {"rph": 10, "rpd": 100, "burst": 2}


class RateLimiter:
    """Rate limiter with Redis backend."""

    def __init__(self, redis_client: redis.Redis | None = None):
        self.redis = redis_client
        self.enabled = redis_client is not None

    def check(self, key: str, limit: int, window: int = 3600) -> tuple[bool, int]:
        """
        Check if request is within rate limit.

        Args:
            key: Unique identifier for rate limit bucket
            limit: Maximum requests allowed
            window: Time window in seconds (default: 1 hour)

        Returns:
            Tuple of (allowed, remaining_count)
        """
        if not self.enabled:
            return True, limit  # No Redis = unlimited

        try:
            # Use Redis pipeline for atomic operations
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window)
            results = pipe.execute()

            current_count = results[0]
            remaining = max(0, limit - current_count)

            if current_count > limit:
                logger.warning(f"Rate limit exceeded for key: {key}")
                return False, 0

            return True, remaining

        except redis.RedisError as e:
            logger.error(f"Redis error in rate limiter: {e}")
            return True, limit  # Fail open if Redis down

    def get_tier_limits(self, tier: str) -> dict[str, int]:
        """Get rate limit configuration for a tier."""
        return RATE_LIMITS.get(tier.upper(), DEFAULT_LIMITS)

    def check_tier(self, request: Request, tier: str, endpoint: str = "default") -> bool:
        """
        Check rate limit for a specific tier and endpoint.

        Args:
            request: FastAPI request object
            tier: User's authentication tier
            endpoint: API endpoint name

        Returns:
            True if allowed, False if rate limited
        """
        if not self.enabled:
            return True  # Unl...[truncated]
