"""Central dependency injection hub for the RMI backend.

Every FastAPI dependency provider lives here so that:
1. Routers use `Depends(get_xxx)` instead of calling global singletons
2. Tests can override any dependency via `app.dependency_overrides`
3. Connection lifecycle (open/close) is managed by FastAPI's lifespan

Usage in routes:
    from app.core.dependencies import get_settings, get_http_client
    from fastapi import Depends
    from app.config import Settings

    @router.get("/example")
    async def example(
        settings: Settings = Depends(get_settings),
        client: httpx.AsyncClient = Depends(get_http_client),
    ):
        ...

Override in tests:
    async def mock_get_db():
        yield test_session

    app.dependency_overrides[get_db] = mock_get_db
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from app.config import Settings, get_settings as _get_settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.chain_client import ChainClient
    from app.core.rate_limiter import UnifiedRateLimiter
    from app.domains.scanner.unified_token_scanner import UnifiedTokenScanner
    from app.domains.wallet.unified_wallet_scanner import UnifiedWalletScanner
    from app.evm_connector import EVMClient
    from app.services.pryscraper_client import PryscraperClient

logger = get_logger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────

def get_settings() -> Settings:
    """Return cached Settings singleton.

    Wraps ``app.config.get_settings`` so that routers can use
    ``Depends(get_settings)`` and tests can override it.
    """
    return _get_settings()

# ── Database sessions ───────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Request-scoped async SQLAlchemy session.

    Yields a session that auto-commits on success, rolls back on error,
    and always closes.  Delegates to ``app.core.database.get_db``.
    """
    # Import lazily to avoid circular imports at module level
    from app.core.database import get_db as _get_db

    async for session in _get_db():
        yield session

# ── HTTP client ─────────────────────────────────────────────────────────────

_http_client: httpx.AsyncClient | None = None

async def get_http_client() -> httpx.AsyncClient:
    """Shared async HTTP client with connection pooling.

    Creates the client once (lazily) and reuses it for all requests.
    The client is intentionally **not** closed after each request so
    that TCP connections are reused.

    Override in tests with ``httpx.AsyncClient`` wired to
    ``pytest-httpx`` or ``respx``.
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            headers={"User-Agent": "RMI-Backend/2026.07"},
        )
        logger.info("Shared HTTP client created (max 100 connections)")
    return _http_client

async def close_http_client() -> None:
    """Close the shared HTTP client.  Called during app shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
        logger.info("Shared HTTP client closed")

# ── Redis ───────────────────────────────────────────────────────────────────

async def get_redis() -> Any | None:
    """Async Redis dependency — returns a Redis client or ``None``.

    Uses the central connection pool from ``app.core.redis`` so that
    connection limits are respected across the entire application.
    Falls back to ``None`` when Redis is unavailable (degraded mode).

    Example::

        @router.get("/foo")
        async def foo(r: Any | None = Depends(get_redis)):
            if r is not None:
                await r.set("key", "value")
    """
    from app.core.redis import get_redis_async

    return get_redis_async()

# ── Chain client (Solana) ────────────────────────────────────────────────────

def get_chain_client() -> ChainClient:
    """Chain client for Solana RPC (rate-limited, circuit-broken).

    Wraps ``app.chain_client.get_chain_client`` so it can be injected
    via ``Depends`` and overridden in tests.
    """
    from app.chain_client import get_chain_client as _get_chain_client

    return _get_chain_client()

# ── EVM client ───────────────────────────────────────────────────────────────

def get_evm_client(chain: str = "base") -> EVMClient:
    """EVM blockchain client for the given *chain*.

    Wraps ``app.evm_connector.get_evm_client``.  Callers remain free
    to pass the ``chain`` parameter; the underlying singleton ensures
    one client per chain.

    Override in tests by replacing this dependency::

        app.dependency_overrides[get_evm_client] = lambda: mock_client
    """
    from app.evm_connector import get_evm_client as _get_evm_client

    return _get_evm_client(chain=chain)

# ── Rate limiter ─────────────────────────────────────────────────────────────

def get_rate_limiter() -> UnifiedRateLimiter:
    """Unified rate limiter (token bucket, Redis + in-memory fallback).

    Wraps ``app.core.rate_limiter.get_rate_limiter``.
    """
    from app.core.rate_limiter import get_rate_limiter as _get_rate_limiter

    return _get_rate_limiter()

# ── Scanners ─────────────────────────────────────────────────────────────────

def get_token_scanner() -> UnifiedTokenScanner:
    """Token scanner singleton (50+ security modules).

    Wraps ``app.domains.scanner.unified_token_scanner.get_token_scanner``.
    """
    from app.domains.scanner.unified_token_scanner import get_token_scanner as _get

    return _get()

def get_wallet_scanner() -> UnifiedWalletScanner:
    """Wallet scanner singleton (entity clustering, RAG, risk scoring).

    Wraps ``app.domains.wallet.unified_wallet_scanner.get_wallet_scanner``.
    """
    from app.domains.wallet.unified_wallet_scanner import get_wallet_scanner as _get

    return _get()

# ── Pryscraper client ────────────────────────────────────────────────────────

def get_pryscraper_client() -> PryscraperClient:
    """Pryscraper HTTP client with circuit breaker.

    Lazily creates a ``PryscraperClient`` singleton so it can be
    injected via ``Depends``.
    """
    from app.services.pryscraper_client import PryscraperClient

    _client: PryscraperClient | None = getattr(get_pryscraper_client, "_instance", None)
    if _client is None:
        _client = PryscraperClient()
        get_pryscraper_client._instance = _client  # type: ignore[attr-defined]
    return _client

# ── Convenience: get client IP from request ─────────────────────────────────

def get_client_ip(request: Request) -> str:
    """Extract the real client IP from headers or the raw socket."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"

# ── Module-level sentinel cleanup (called from tests) ────────────────────────

def reset_http_client() -> None:
    """Reset the HTTP client singleton — used in test teardown."""
    global _http_client
    _http_client = None
