"""
Lightweight Redis-backed Circuit Breaker for external API calls.
Prevents cascading failures when external services (Helius, OpenRouter, etc.) go down.
"""

from app.core.logging import get_logger
import os
import time

import redis

logger = get_logger(__name__)


def _get_redis() -> redis.Redis:
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", ""),
        decode_responses=True,
        socket_timeout=2,
    )


async def check_circuit(service: str) -> bool:
    """
    Returns True if circuit is OPEN (do not call the service).
    Returns False if CLOSED or HALF_OPEN (safe to call).
    """
    try:
        r = _get_redis()
        state = r.get(f"circuit:{service}:state")

        if state == "OPEN":
            opened_at = float(r.get(f"circuit:{service}:opened_at") or 0)
            if time.time() - opened_at > 60:  # 60 seconds cooldown
                r.set(f"circuit:{service}:state", "HALF_OPEN")
                logger.info(f"Circuit breaker for {service} moving to HALF_OPEN")
                return False
            return True  # Still OPEN, block the call

        return False  # CLOSED or HALF_OPEN
    except Exception as e:
        logger.error(f"Circuit breaker check failed for {service}: {e}")
        return False  # Fail open if Redis is down


async def record_success(service: str):
    try:
        r = _get_redis()
        r.set(f"circuit:{service}:state", "CLOSED")
        r.delete(f"circuit:{service}:failures")
    except Exception as e:
        logger.error(f"Circuit breaker success record failed for {service}: {e}")


async def record_failure(service: str):
    try:
        r = _get_redis()
        failures = r.incr(f"circuit:{service}:failures")
        if failures >= 5:
            r.set(f"circuit:{service}:state", "OPEN")
            r.set(f"circuit:{service}:opened_at", str(time.time()))
            logger.warning(f"Circuit breaker for {service} TRIPPED (OPEN) after 5 failures")
    except Exception as e:
        logger.error(f"Circuit breaker failure record failed for {service}: {e}")
