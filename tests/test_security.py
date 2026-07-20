"""Tests for security-related functionality: auth, rate limiting, and API key checks.

Covers:
- require_auth / get_current_user dependency
- Rate limiter configuration and tracking
- Internal API key validation
- Slow rate limiter
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest


# ── Rate Limiter Configuration ─────────────────────────────────────────


class TestProviderConfiguration:
    """Provider configuration logic."""

    def test_configure_providers_without_credits(self):
        """configure_providers works without OpenRouter credits."""
        import app.rate_limiter as rl

        rl.configure_providers(has_openrouter_credits=False)
        assert len(rl.PROVIDERS) >= 8
        # OpenRouter should have lower RPD without credits
        for p in rl.PROVIDERS:
            if "openrouter" in p.id:
                assert p.rpd == 50

    def test_configure_providers_with_credits(self):
        """configure_providers with credits sets higher OpenRouter limit."""
        import app.rate_limiter as rl

        rl.configure_providers(has_openrouter_credits=True)
        assert len(rl.PROVIDERS) >= 8
        for p in rl.PROVIDERS:
            if "openrouter" in p.id:
                assert p.rpd == 1000

    def test_provider_has_gemini(self):
        """Provider list includes Gemini."""
        from app.rate_limiter import PROVIDERS, configure_providers

        configure_providers(has_openrouter_credits=True)
        gemini_ids = [p.id for p in PROVIDERS if p.id.startswith("gemini")]
        assert len(gemini_ids) >= 1

    def test_provider_has_local_fallback(self):
        """Provider list includes local BGE fallback."""
        from app.rate_limiter import PROVIDERS, configure_providers

        configure_providers()
        ids = [p.id for p in PROVIDERS]
        assert "local_bge" in ids
        assert "hash" in ids


# ── RateTracker ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_tracker_can_call_returns_true_initially():
    """RateTracker.can_call returns True for unused provider."""
    from app.rate_limiter import PROVIDERS, RateTracker, configure_providers

    configure_providers()
    tracker = RateTracker()

    # Find a provider with high limits
    hash_provider = next(p for p in PROVIDERS if p.id == "hash")
    result = await tracker.can_call(hash_provider)

    assert result is True


@pytest.mark.asyncio
async def test_rate_tracker_can_call_respects_limits():
    """RateTracker respects provider limits."""
    from app.rate_limiter import ProviderLimit, RateTracker

    tracker = RateTracker()
    # Use limits where 90% margin rounds to > 0 so first call passes
    # rpd=20, rpm=10 → margins: int(20*0.9)=18, int(10*0.9)=9
    limited = ProviderLimit(
        id="test_limited",
        name="Limited",
        model="test",
        dims=384,
        base_url="",
        key_env="",
        rpm=10,
        rpd=20,
        free=True,
        priority=0,
    )

    # First call should work
    can1 = await tracker.can_call(limited)
    assert can1 is True

    # Record 1 call
    await tracker.record(limited)

    # RPM should still be available (1 < 9 margin)
    can_after_one = await tracker.can_call(limited)
    assert can_after_one is True

    # Record 9 more calls to hit RPM margin (1 + 9 = 10 >= 9)
    for _ in range(9):
        await tracker.record(limited)

    # Now RPM limit should be hit
    can2 = await tracker.can_call(limited)
    # With 90% safety margin, 10 used of 10 RPM -> 90% threshold -> limited
    assert can2 is False


@pytest.mark.asyncio
async def test_rate_tracker_remaining():
    """RateTracker.remaining returns correct remaining counts."""
    from app.rate_limiter import ProviderLimit, RateTracker

    tracker = RateTracker()
    provider = ProviderLimit(
        id="test_remaining",
        name="Test",
        model="test",
        dims=384,
        base_url="",
        key_env="",
        rpm=100,
        rpd=1000,
        free=True,
        priority=0,
    )

    remaining = await tracker.remaining(provider)
    assert remaining["day_remaining"] > 0
    assert remaining["minute_remaining"] > 0


# ── Embedding Cache ────────────────────────────────────────────────────


def test_embedding_cache_hit():
    """Embedding cache returns cached results."""
    from app.rate_limiter import EmbeddingCache

    cache = EmbeddingCache(max_size=100)
    text = "Hello world"
    vector = [0.1, 0.2, 0.3]

    cache.set(text, vector, "test_provider")
    result = cache.get(text)

    assert result is not None
    assert result[0] == vector
    assert result[1] == "test_provider"


def test_embedding_cache_miss():
    """Embedding cache returns None for unknown text."""
    from app.rate_limiter import EmbeddingCache

    cache = EmbeddingCache(max_size=100)
    result = cache.get("unknown text")

    assert result is None


def test_embedding_cache_eviction():
    """Embedding cache evicts oldest entries when full."""
    from app.rate_limiter import EmbeddingCache

    cache = EmbeddingCache(max_size=20)
    for i in range(25):
        cache.set(f"text_{i}", [float(i)], "test")

    # Should have evicted some
    assert len(cache._cache) <= 20

    # Stats should be present
    stats = cache.stats()
    assert "size" in stats
    assert stats["size"] <= 20


def test_embedding_cache_stats():
    """Embedding cache stats track hits and misses."""
    from app.rate_limiter import EmbeddingCache

    cache = EmbeddingCache(max_size=100)
    cache.set("key1", [1.0], "p1")

    cache.get("key1")  # hit
    cache.get("key1")  # hit
    cache.get("key2")  # miss

    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["misses"] >= 1
    assert stats["hit_rate"] > 0


# ── Internal API Key ───────────────────────────────────────────────────


def test_internal_api_key():
    """INTERNAL_API_KEY is a non-empty string."""
    import os

    with patch.dict(os.environ, {"RMI_INTERNAL_KEY": "test-internal-key-12345"}, clear=False):
        # Force reload the module to pick up the env var
        import importlib

        from app.core import internal_key

        importlib.reload(internal_key)
        key = internal_key.INTERNAL_API_KEY

        assert isinstance(key, str)
        assert len(key) > 0
        assert key == "test-internal-key-12345"


# ── Slow Rate Limiter (app.core.slowrate) ──────────────────────────────


def test_rate_limiter_imports():
    """Slow rate limiter module imports cleanly."""
    from app.core.slowrate import limiter

    assert limiter is not None


# ── Health Route Config Validation ─────────────────────────────────────


def test_check_config_no_issues_with_env():
    """Config validation passes with environment variables set."""
    from app.core.health_route import check_config

    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://user:pass@localhost/db",
            "REDIS_URL": "redis://localhost:6379/0",
            "CLICKHOUSE_URL": "http://localhost:8123/",
        },
        clear=False,
    ):
        issues = check_config()
        # Required vars are set, so no missing issues
        required_missing = [i for i in issues if i["severity"] == "missing"]
        # But there might be placeholder issues if the env has ***
        assert len(required_missing) == 0
