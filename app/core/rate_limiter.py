"""
Unified Rate Limiter — Token Bucket Algorithm
================================================
Redis-backed token bucket with in-memory fallback.
Standard X-RateLimit-Limit / X-RateLimit-Remaining / X-RateLimit-Reset headers.
Usable as: FastAPI middleware, FastAPI dependency, standalone async function.

Also contains: 3-tier pricing router (FREE/PRO/ENTERPRISE), PII-safe hash utilities.

Consolidates 7 separate rate limiter implementations into 1.
The old files (app/middleware/rate_limit.py, app/caching_shield/rate_limiter.py,
app/scan_rate_limiter.py, etc.) were removed — see each file's history.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time as _time
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from fastapi import Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.core.logging import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Rate Limit Configuration (from app/core/rate_limit_config.py)
# ═══════════════════════════════════════════════════════════════════════════

# (requests_per_minute, burst_multiplier)
# burst = max(1, requests_per_minute * burst_multiplier)
DEFAULT_RATE_LIMITS: dict[str, tuple[int, float]] = {
    "health": (1000, 0.2),  # 1000/min,  burst 200
    "auth": (5, 0.4),  # 5/min,     burst 2
    "x402": (1000, 0.2),  # 1000/min,  burst 200
    "scan": (10, 0.25),  # 10/min,    burst 2  (per API key)
    "ai": (10, 0.3),  # 10/min,    burst 3
    "market": (60, 0.25),  # 60/min,    burst 15
    "public": (100, 0.25),  # 100/min,   burst 25
    "mcp": (30, 0.25),  # 30/min,    burst 7
    "admin": (10, 0.2),  # 10/min,    burst 2
    "databus": (60, 0.25),  # 60/min,    burst 15  (per API key)
    "degenfeed:farcaster": (30, 0.25),  # 30/min, burst 7
    "degenfeed:lens": (30, 0.25),  # 30/min, burst 7
    "degenfeed:twitter": (15, 0.2),  # 15/min, burst 3
    "degenfeed:bluesky": (30, 0.25),  # 30/min, burst 7
    "degenfeed:mastodon": (60, 0.25),  # 60/min, burst 15
    "degenfeed:nostr": (60, 0.25),  # 60/min, burst 15
    "degenfeed:cross-post": (10, 0.3),  # 10/min, burst 3
    "default": (60, 0.25),  # 60/min,    burst 15
}


# ── Env-var overrides ─────────────────────────────────────────────────────
# Each rate limit can be tuned at deploy time without a code change:
#   RATE_LIMIT_GLOBAL   → global per-IP cap (req/min), default 100
#   RATE_LIMIT_SCANNER  → scanner endpoints (req/min per API key), default 10
#   RATE_LIMIT_AUTH     → auth endpoints (req/min per IP), default 5
#   RATE_LIMIT_DATABUS  → DataBus endpoints (req/min per API key), default 60
#   RATE_LIMIT_DEFAULT  → fallback for unmapped paths (req/min), default 60
# Values are clamped to >= 1. Invalid/non-numeric values fall back to defaults.
def _env_int(name: str, default: int) -> int:
    try:
        val = int(os.getenv(name, ""))
        return val if val >= 1 else default
    except (TypeError, ValueError):
        return default


RATE_LIMIT_GLOBAL: int = _env_int("RATE_LIMIT_GLOBAL", 100)
RATE_LIMIT_SCANNER: int = _env_int("RATE_LIMIT_SCANNER", 10)
RATE_LIMIT_AUTH: int = _env_int("RATE_LIMIT_AUTH", 5)
RATE_LIMIT_DATABUS: int = _env_int("RATE_LIMIT_DATABUS", 60)
RATE_LIMIT_DEFAULT: int = _env_int("RATE_LIMIT_DEFAULT", 60)

# Apply env-var overrides onto the static defaults (keeps burst factors).
_ENV_OVERRIDES: dict[str, int] = {
    "scan": RATE_LIMIT_SCANNER,
    "auth": RATE_LIMIT_AUTH,
    "databus": RATE_LIMIT_DATABUS,
    "default": RATE_LIMIT_DEFAULT,
}
for _cat, _rpm in _ENV_OVERRIDES.items():
    if _cat in DEFAULT_RATE_LIMITS:
        _burst_factor = DEFAULT_RATE_LIMITS[_cat][1]
        DEFAULT_RATE_LIMITS[_cat] = (_rpm, _burst_factor)

# Path prefix → category
PATH_CATEGORIES: dict[str, str] = {
    "/health": "health",
    "/live": "health",
    "/ready": "health",
    "/metrics": "health",
    "/register": "auth",
    "/login": "auth",
    "/2fa": "auth",
    "/forgot-password": "auth",
    "/api/v1/auth": "auth",
    "/api/v1/x402": "x402",
    "/pay": "x402",
    "/affiliates": "x402",
    "/usage": "x402",
    "/api/v1/scanner": "scan",
    "/api/v1/contract-analyzer": "scan",
    "/api/v1/death-clock": "scan",
    "/api/v1/rugmaps": "scan",
    "/api/v1/honeypot-map": "scan",
    "/api/v1/tvl-verify": "scan",
    "/api/v1/whale-alerts": "scan",
    "/api/v1/criminal-clusters": "scan",
    "/api/v1/address-profiler": "scan",
    "/api/v1/mev-advisory": "scan",
    "/api/v1/wallet-clusters": "scan",
    "/api/v1/ai": "ai",
    "/api/v1/chat": "ai",
    "/api/v1/market": "market",
    "/api/v1/news": "market",
    "/api/v1/bulletin": "market",
    "/api/v1/bulletin-board": "market",
    "/api/v1/catalog": "market",
    "/api/v1/token": "public",
    "/api/v1/wallet": "public",
    "/api/v1/labels": "public",
    "/mcp": "mcp",
    "/api/v1/degenfeed/farcaster": "degenfeed:farcaster",
    "/api/v1/degenfeed/lens": "degenfeed:lens",
    "/api/v1/degenfeed/twitter": "degenfeed:twitter",
    "/api/v1/degenfeed/bluesky": "degenfeed:bluesky",
    "/api/v1/degenfeed/mastodon": "degenfeed:mastodon",
    "/api/v1/degenfeed/nostr": "degenfeed:nostr",
    "/api/v1/degenfeed": "degenfeed:farcaster",
    "/api/v1/databus": "databus",
    "/api/v1/x402-databus": "databus",
    "/api/v1/admin": "admin",
    "/api/v1/_test": "admin",
}


def _resolve_rate_limit(path: str) -> tuple[float, float]:
    """Resolve (rate_per_sec, burst_size) for a request path."""
    for prefix, category in sorted(PATH_CATEGORIES.items(), key=lambda x: -len(x[0])):
        if path.startswith(prefix):
            rpm, burst_factor = DEFAULT_RATE_LIMITS[category]
            burst = max(1, int(rpm * burst_factor))
            return (rpm / 60.0, burst)
    rpm, burst_factor = DEFAULT_RATE_LIMITS["default"]
    burst = max(1, int(rpm * burst_factor))
    return (rpm / 60.0, burst)


# ═══════════════════════════════════════════════════════════════════════════
# Redis helpers
# ═══════════════════════════════════════════════════════════════════════════


def _get_limiter_settings():
    """Load rate limiter config from settings, falling back to env vars."""
    try:
        from app.config import settings as _s

        host = _s.redis_host
        port = _s.redis_port
        password = _s.redis_password
        db = _s.rate_limiter_redis_db
        salt = _s.rate_limit_salt
    except Exception:
        host = os.getenv("REDIS_HOST", "rmi-redis")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD", "")
        db = int(os.getenv("RATE_LIMITER_REDIS_DB", "4"))
        salt = os.getenv("RATE_LIMIT_SALT", "")
    return host, port, password, db, salt


REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, RATE_LIMITER_REDIS_DB, _rate_salt = _get_limiter_settings()
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/3"
RATE_LIMIT_SALT_SECRET = _rate_salt

# ═══════════════════════════════════════════════════════════════════════════
# PII-safe hash utilities (preserved from old file)
# ═══════════════════════════════════════════════════════════════════════════

RATE_LIMIT_SALT_SECRET = os.getenv("RATE_LIMIT_SALT", "")


def _get_daily_salt() -> bytes:
    """Derive a daily-rotating salt from server secret + today's UTC date.

    In production, RATE_LIMIT_SALT_SECRET MUST be set via RATE_LIMIT_SALT env var.
    If unset, falls back to a hash of the JWT_SECRET to avoid a hardcoded dev string.
    """
    date_str = _time.strftime("%Y-%m-%d")
    secret = RATE_LIMIT_SALT_SECRET.encode() if RATE_LIMIT_SALT_SECRET else None
    if not secret:
        # Fall back to JWT_SECRET as a unique-per-instance salt
        try:
            from app.config import settings

            jwt_part = (settings.jwt_secret or "").encode()
            if len(jwt_part) >= 16:
                secret = jwt_part
        except Exception:  # noqa: S110 — optional dependency
            pass
    if not secret:
        # Absolute last resort — instance-unique, not a hardcoded string
        import socket

        secret = socket.gethostname().encode() + b"rmi-rate-salt"
    return hmac.new(secret, date_str.encode(), hashlib.sha256).digest()


def _hash_identifier(identifier: str) -> str:
    """HMAC-hash a user identifier (wallet, API key, IP) for Redis storage."""
    salt = _get_daily_salt()
    return hmac.new(salt, identifier.encode(), hashlib.sha256).hexdigest()[:32]


# ═══════════════════════════════════════════════════════════════════════════
# Token Bucket Lua script — atomic Redis operation
# ═══════════════════════════════════════════════════════════════════════════

_TOKEN_BUCKET_LUA = """
local bucket_key = KEYS[1]
local burst_key  = KEYS[2]
local rate       = tonumber(ARGV[1])
local burst      = tonumber(ARGV[2])
local tokens     = tonumber(ARGV[3])
local now        = tonumber(ARGV[4])
local ttl        = tonumber(ARGV[5])

local current     = redis.call('GET', bucket_key)
local last_refill = redis.call('GET', burst_key)

local available = burst
if current and last_refill then
    available = tonumber(current)
    local elapsed = now - tonumber(last_refill)
    available = math.min(burst, available + elapsed * rate)
end

if available >= tokens then
    available = available - tokens
    redis.call('SETEX', bucket_key, ttl, available)
    redis.call('SET',   burst_key, now)
    return {1, available, burst, 0}
else
    local needed = tokens - available
    local wait   = math.ceil((needed / rate) * 100) / 100
    return {0, available, burst, wait}
end
"""  # noqa: S105 — config default

# ═══════════════════════════════════════════════════════════════════════════
# Unified Rate Limiter
# ═══════════════════════════════════════════════════════════════════════════


class UnifiedRateLimiter:
    """Token bucket rate limiter — Redis-backed with in-memory fallback.

    Usage as middleware:
        limiter = get_rate_limiter()
        app.add_middleware(UnifiedRateLimiterMiddleware, limiter=limiter)

    Usage as dependency:
        @router.get("/scan")
        async def scan(req: Request, rl=Depends(get_rate_limit_dependency("scan"))):
            ...

    Usage standalone:
        limiter = get_rate_limiter()
        allowed, headers = await limiter.acquire("my-key", rate=1.0, burst=5)
    """

    BUCKET_PREFIX = "rmi:unified_bucket:"
    BURST_PREFIX = "rmi:unified_burst:"
    TTL_SECONDS = 120

    # Retry connection after this many seconds of being in failed state
    _REDIS_RETRY_INTERVAL = 30.0

    def __init__(self) -> None:
        self._redis: Any = None
        self._redis_failed = False
        self._redis_failed_at: float = 0.0
        self._init_lock = asyncio.Lock()
        self._mem_buckets: dict[str, tuple[float, float]] = {}
        self._mem_lock = asyncio.Lock()
        self._lua_sha: str | None = None

    async def _ensure_redis(self) -> Any | None:
        if self._redis is not None:
            return self._redis
        if self._redis_failed:
            # Retry after interval — prevents permanent stuck-in-memory mode
            if _time.monotonic() - self._redis_failed_at < self._REDIS_RETRY_INTERVAL:
                return None
            # Time to retry: reset the flag so we attempt connection
            self._redis_failed = False
        async with self._init_lock:
            if self._redis is not None:
                return self._redis
            if self._redis_failed:
                return None
            try:
                import redis.asyncio as aioredis

                url = (
                    f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{RATE_LIMITER_REDIS_DB}"
                    if REDIS_PASSWORD
                    else f"redis://{REDIS_HOST}:{REDIS_PORT}/{RATE_LIMITER_REDIS_DB}"
                )
                self._redis = aioredis.from_url(url, socket_connect_timeout=2, decode_responses=False)
                await self._redis.ping()
                self._lua_sha = await self._redis.script_load(_TOKEN_BUCKET_LUA)
                logger.info("UnifiedRateLimiter: Redis connected OK")
                return self._redis
            except Exception as exc:
                logger.warning("UnifiedRateLimiter: Redis unavailable (%s), using in-memory fallback", exc)
                self._redis = None
                self._redis_failed = True
                self._redis_failed_at = _time.monotonic()
                return None

    # ── Core acquire ──────────────────────────────────────────────────

    async def acquire(
        self,
        key: str,
        rate: float = 1.0,
        burst: float = 5.0,
        tokens: float = 1.0,
    ) -> tuple[bool, dict[str, str]]:
        """Try to consume *tokens* from bucket *key*.

        Returns (allowed, headers_dict) where headers_dict has:
            X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset

        Resilience: Falls back to in-memory token bucket if Redis is down.
        Sets _redis_failed flag and nulls connection so next call retries.
        """
        redis = await self._ensure_redis()
        if redis:
            try:
                return await self._acquire_redis(redis, key, rate, burst, tokens)
            except Exception as exc:
                logger.warning("Redis acquire failed — rate limiter using in-memory fallback: %s", exc)
                # Null the stale connection so _ensure_redis retries next time
                self._redis = None
                self._redis_failed = True
                self._redis_failed_at = _time.monotonic()
        return await self._acquire_memory(key, rate, burst, tokens)

    async def _acquire_redis(
        self, redis: Any, key: str, rate: float, burst: float, tokens: float
    ) -> tuple[bool, dict[str, str]]:
        bucket_key = f"{self.BUCKET_PREFIX}{key}"
        burst_key = f"{self.BURST_PREFIX}{key}"
        now = _time.time()

        result = await redis.evalsha(
            self._lua_sha,
            2,
            bucket_key,
            burst_key,
            str(rate),
            str(int(burst)),
            str(tokens),
            str(now),
            str(self.TTL_SECONDS),
        )
        allowed = bool(result[0])
        remaining = int(float(result[1]))
        limit = int(float(result[2]))
        wait = float(result[3])

        # Reset when bucket fully refills
        remaining_tokens = max(0, remaining)
        time_to_full = (limit - remaining_tokens) / rate if rate > 0 else 0
        reset_at = int(now + time_to_full)

        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining_tokens),
            "X-RateLimit-Reset": str(reset_at),
        }

        if not allowed:
            headers["Retry-After"] = str(int(wait) + 1)

        return (allowed, headers)

    async def _acquire_memory(self, key: str, rate: float, burst: float, tokens: float) -> tuple[bool, dict[str, str]]:
        async with self._mem_lock:
            now = _time.monotonic()
            bucket = self._mem_buckets.get(key)

            if bucket:
                current, last_refill = bucket
                elapsed = now - last_refill
                current = min(burst, current + elapsed * rate)
            else:
                current = burst

            limit = int(burst)
            remaining = max(0, int(current))

            if current >= tokens:
                current -= tokens
                self._mem_buckets[key] = (current, now)
                remaining_after = max(0, int(current))
                time_to_full = (limit - remaining_after) / rate if rate > 0 else 0
                reset_at = int(_time.time() + time_to_full)
                return (
                    True,
                    {
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": str(remaining_after),
                        "X-RateLimit-Reset": str(reset_at),
                    },
                )

            needed = tokens - current
            wait = needed / rate
            time_to_full = (limit - remaining) / rate if rate > 0 else 0
            reset_at = int(_time.time() + time_to_full)
            return (
                False,
                {
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(reset_at),
                    "Retry-After": str(int(wait) + 1),
                },
            )

    # ── Per-path acquire (uses PATH_CATEGORIES) ───────────────────────

    async def acquire_path(self, path: str, client_id: str) -> tuple[bool, dict[str, str]]:
        """Acquire a token for the given request path and client."""
        rate, burst = _resolve_rate_limit(path)
        key = f"path:{path}:{client_id}"
        return await self.acquire(key, rate=rate, burst=burst)

    # ── Provider-style acquire (backward compat with RpcRateLimiter) ──

    async def acquire_provider(
        self,
        provider: str,
        method: str = "",
        tokens: int = 1,
        rate: float = 1.0,
        burst: float = 10.0,
    ) -> tuple[bool, float]:
        """Backward-compat with RpcRateLimiter.acquire(provider, method).
        Returns (allowed, wait_seconds)."""
        key = f"provider:{provider}" + (f":{method}" if method else "")
        _rate = rate
        _burst = burst
        redis = await self._ensure_redis()
        if redis:
            try:
                r, headers = await self._acquire_redis(redis, key, _rate, _burst, tokens)
                wait = float(headers.get("Retry-After", 0))
                return (r, wait)
            except (ConnectionError, OSError, TimeoutError) as e:
                logger.warning(f"acquire_provider failed: {e}")
        r, headers = await self._acquire_memory(key, _rate, _burst, tokens)
        wait = float(headers.get("Retry-After", 0))
        return (r, wait)

    # ── Stats ─────────────────────────────────────────────────────────

    async def stats(self, key_prefix: str = "") -> dict[str, Any]:
        """Return stats for buckets matching prefix."""
        redis = await self._ensure_redis()
        result: dict[str, Any] = {"redis_available": redis is not None, "buckets": {}}
        if redis:
            try:
                pattern = f"{self.BUCKET_PREFIX}{key_prefix}*"
                keys = []
                async for k in redis.scan_iter(match=pattern, count=100):
                    keys.append(k)
                for k in keys[:50]:
                    val = await redis.get(k)
                    if val is not None:
                        short = k.decode() if isinstance(k, bytes) else k
                        short = short.removeprefix(self.BUCKET_PREFIX)
                        result["buckets"][short] = round(float(val), 1)
            except Exception as exc:
                result["error"] = str(exc)
        else:
            for k, (tokens, _) in self._mem_buckets.items():
                if k.startswith(key_prefix):
                    result["buckets"][k] = round(tokens, 1)
        return result


# ═══════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════

_limiter_singleton: UnifiedRateLimiter | None = None


def get_rate_limiter() -> UnifiedRateLimiter:
    """Get the singleton unified rate limiter instance."""
    global _limiter_singleton
    if _limiter_singleton is None:
        _limiter_singleton = UnifiedRateLimiter()
    return _limiter_singleton


# ═══════════════════════════════════════════════════════════════════════════
# FastAPI Middleware
# ═══════════════════════════════════════════════════════════════════════════

from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402 — module docstring precedes import block

if TYPE_CHECKING:
    from starlette.responses import Response as StarletteResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that applies rate limits from PATH_CATEGORIES."""

    def __init__(self, app, limiter: UnifiedRateLimiter | None = None) -> None:
        super().__init__(app)
        self.limiter = limiter or get_rate_limiter()

    async def dispatch(self, request: Request, call_next: Any) -> StarletteResponse:
        client_id = _extract_client_id(request)

        # Global per-IP cap (default 100 req/min). Applied to every request
        # regardless of path, keyed on the same client identity as the
        # path-based limits. Fail-open: if the limiter cannot reach Redis it
        # falls back to the in-memory token bucket (never blocks on Redis).
        global_allowed, global_headers = await self.limiter.acquire(
            f"global:{client_id}",
            rate=RATE_LIMIT_GLOBAL / 60.0,
            burst=max(1, int(RATE_LIMIT_GLOBAL * 0.25)),
        )
        if not global_allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers=global_headers,
            )

        allowed, headers = await self.limiter.acquire_path(request.url.path, client_id)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers=headers,
            )
        response = await call_next(request)
        for k, v in headers.items():
            response.headers[k] = str(v)
        return response


# ═══════════════════════════════════════════════════════════════════════════
# FastAPI Dependency
# ═══════════════════════════════════════════════════════════════════════════

from fastapi import HTTPException  # noqa: E402 — module docstring precedes import block


class RateLimitDependency:
    """FastAPI dependency for per-endpoint rate limiting."""

    def __init__(
        self,
        limit_key: str = "default",
        rate: float | None = None,
        burst: float | None = None,
    ) -> None:
        self.limit_key = limit_key
        self._rate = rate
        self._burst = burst

    async def __call__(self, request: Request) -> Request:
        limiter = get_rate_limiter()
        client_id = _extract_client_id(request)

        if self._rate is not None and self._burst is not None:
            rate, burst = self._rate, self._burst
        else:
            if self.limit_key in DEFAULT_RATE_LIMITS:
                rpm, burst_factor = DEFAULT_RATE_LIMITS[self.limit_key]
                rate = rpm / 60.0
                burst = max(1, int(rpm * burst_factor))
            else:
                rate, burst = _resolve_rate_limit(request.url.path)

        key = f"dep:{self.limit_key}:{client_id}"
        allowed, headers = await limiter.acquire(key, rate=rate, burst=burst)

        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={k: str(v) for k, v in headers.items()},
            )

        request.state.rate_limit_headers = headers
        return request


def get_rate_limit_dependency(
    limit_key: str = "default",
    rate: float | None = None,
    burst: float | None = None,
) -> RateLimitDependency:
    """Create a FastAPI Depends() compatible rate limit dependency."""
    return RateLimitDependency(limit_key=limit_key, rate=rate, burst=burst)


# ═══════════════════════════════════════════════════════════════════════════
# Client ID extraction
# ═══════════════════════════════════════════════════════════════════════════


def _extract_client_id(request: Request) -> str:
    """Extract client identifier: API key > trusted real connection IP.

    Rate-limit identity MUST be keyed on the real peer IP (request.client.host),
    which uvicorn/Starlette derives from the trusted proxy chain. Never trust the
    raw X-Forwarded-For header — it is client-controlled and lets an attacker
    rotate identities to bypass per-IP rate limits.
    """
    # Prefer API key for authenticated users
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        return _hash_identifier(api_key)

    # Key on the real connection IP, never on client-supplied X-Forwarded-For.
    if request.client:
        return request.client.host
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# Tier / Pricing logic (preserved from old file)
#
# NOTE: The tier/pricing HTTP ROUTER (/api/v1/rate-limits) was extracted to
# ``app/core/rate_limit_tiers.py`` (2026-08-07) — it is UNMOUNTED dead code
# (not included in app/mount.py). Only the tier *logic* used by tests and the
# rate limiter lives here: Tier, TIER_LIMITS, _users, _get_tier_redis,
# get_user_tier, check_rate_limit.
# ═══════════════════════════════════════════════════════════════════════════


class Tier(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


TIER_LIMITS: dict[Tier, dict[str, Any]] = {
    Tier.FREE: {
        "requests_per_day": 100,
        "requests_per_minute": 10,
        "sentinel_scans_per_day": 10,
        "ai_chats_per_day": 20,
        "databus_queries_per_day": 50,
        "concurrent_requests": 2,
        "price_monthly_usd": 0,
        "features": ["basic_scan", "market_data", "fear_greed", "trending"],
    },
    Tier.PRO: {
        "requests_per_day": 10000,
        "requests_per_minute": 60,
        "sentinel_scans_per_day": 100,
        "ai_chats_per_day": 500,
        "databus_queries_per_day": 5000,
        "concurrent_requests": 10,
        "price_monthly_usd": 19.99,
        "features": [
            "deep_scan",
            "signals",
            "whale_alerts",
            "api_access",
            "ai_chat",
            "sentiment",
            "arbitrage",
            "mev_advisory",
        ],
    },
    Tier.ENTERPRISE: {
        "requests_per_day": 999999,
        "requests_per_minute": 300,
        "sentinel_scans_per_day": 99999,
        "ai_chats_per_day": 99999,
        "databus_queries_per_day": 99999,
        "concurrent_requests": 50,
        "price_monthly_usd": 499,
        "features": ["all", "custom_models", "white_label", "dedicated_support", "sla"],
    },
}

_users: dict[str, dict[str, Any]] = {}


def _get_tier_redis() -> Any:
    import redis

    return redis.from_url(REDIS_URL, decode_responses=True)


def get_user_tier(user_id: str) -> Tier:
    """Get user's current tier. Defaults to FREE."""
    if user_id in _users:
        return Tier(_users[user_id].get("tier", "free"))
    try:
        r = _get_tier_redis()
        tier = r.get(f"rmi:user_tier:{_hash_identifier(user_id)}")
        if tier:
            return Tier(tier)
    except (ConnectionError, OSError, TimeoutError, RedisError) as e:
        logger.warning(f"get_user_tier failed: {e}")
    return Tier.FREE


def check_rate_limit(user_id: str, endpoint: str) -> bool:
    """Check if user has remaining quota. Returns True if allowed."""
    tier = get_user_tier(user_id)
    limits = TIER_LIMITS[tier]

    try:
        r = _get_tier_redis()
        today = _time.strftime("%Y-%m-%d")
        hashed = _hash_identifier(user_id)

        minute_key = f"rmi:ratelimit:{hashed}:{today}:minute"
        minute_count = int(r.get(minute_key) or 0)
        if minute_count >= limits["requests_per_minute"]:
            return False

        day_key = f"rmi:ratelimit:{hashed}:{today}:total"
        day_count = int(r.get(day_key) or 0)
        if day_count >= limits["requests_per_day"]:
            return False

        pipe = r.pipeline()
        pipe.incr(minute_key)
        pipe.expire(minute_key, 60)
        pipe.incr(day_key)
        pipe.expire(day_key, 86400)
        pipe.execute()
        return True
    except Exception as e:
        logger.error("rate_limiter_error: %s — failing closed (rejecting request)", e)
        return False
