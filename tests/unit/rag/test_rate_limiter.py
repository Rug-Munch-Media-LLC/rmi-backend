"""Unit tests for app/rag/rate_limiter.py — token-bucket ingestion rate limiter.

Covers the Redis-backed check/remaining/reset API with get_redis
monkeypatched. Verifies allow/block decisions, counter hygiene (expiry,
zremrangebyscore), and fail-open behavior when Redis is unavailable or
raises.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.rag.rate_limiter import RateLimiter


class TestRateLimiterCheck:
    @pytest.mark.asyncio
    async def test_redis_unavailable_fails_open(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
        limiter = RateLimiter(max_per_minute=10)
        assert await limiter.check() is True

    @pytest.mark.asyncio
    async def test_allow_when_under_limit(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.zremrangebyscore.return_value = 0
        fake.zcard.return_value = 2
        fake.pipeline.return_value.__enter__.return_value = fake.pipeline.return_value
        fake.pipeline.return_value.zadd = MagicMock()
        fake.pipeline.return_value.expire = MagicMock()
        fake.pipeline.return_value.execute = MagicMock(return_value=[True, True])
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)

        limiter = RateLimiter(max_per_minute=10)
        assert await limiter.check() is True
        fake.pipeline.return_value.zadd.assert_called_once()
        fake.pipeline.return_value.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_block_when_over_limit(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.zremrangebyscore.return_value = 0
        fake.zcard.return_value = 10  # at max
        fake.pipeline.return_value.__enter__.return_value = fake.pipeline.return_value
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)

        limiter = RateLimiter(max_per_minute=10)
        assert await limiter.check() is False
        fake.zadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_block_when_count_exceeds_remaining(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.zremrangebyscore.return_value = 0
        fake.zcard.return_value = 8  # 2 remaining
        fake.pipeline.return_value.__enter__.return_value = fake.pipeline.return_value
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)

        limiter = RateLimiter(max_per_minute=10)
        assert await limiter.check(count=3) is False

    @pytest.mark.asyncio
    async def test_multiple_count_adds_multiple_members(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.zremrangebyscore.return_value = 0
        fake.zcard.return_value = 0
        fake.pipeline.return_value.__enter__.return_value = fake.pipeline.return_value
        fake.pipeline.return_value.zadd = MagicMock()
        fake.pipeline.return_value.expire = MagicMock()
        fake.pipeline.return_value.execute = MagicMock(return_value=[True] * 2)
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)

        limiter = RateLimiter(max_per_minute=10)
        assert await limiter.check(count=4) is True
        assert fake.pipeline.return_value.zadd.call_count == 4

    @pytest.mark.asyncio
    async def test_exception_fails_open(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.zcard.side_effect = ConnectionError("down")
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)

        limiter = RateLimiter(max_per_minute=10)
        assert await limiter.check() is True

    @pytest.mark.asyncio
    async def test_counter_has_expiry(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.zremrangebyscore.return_value = 0
        fake.zcard.return_value = 0
        fake.pipeline.return_value.__enter__.return_value = fake.pipeline.return_value
        fake.pipeline.return_value.zadd = MagicMock()
        fake.pipeline.return_value.expire = MagicMock()
        fake.pipeline.return_value.execute = MagicMock(return_value=[True, True])
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)

        limiter = RateLimiter(max_per_minute=10)
        await limiter.check()
        fake.pipeline.return_value.expire.assert_called_once()
        ttl = fake.pipeline.return_value.expire.call_args[0][1]
        assert ttl == 120


class TestRateLimiterRemaining:
    @pytest.mark.asyncio
    async def test_redis_unavailable_returns_max(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
        limiter = RateLimiter(max_per_minute=42)
        assert await limiter.remaining() == 42

    @pytest.mark.asyncio
    async def test_remaining_computation(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.zremrangebyscore.return_value = 0
        fake.zcard.return_value = 7
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)

        limiter = RateLimiter(max_per_minute=10)
        assert await limiter.remaining() == 3

    @pytest.mark.asyncio
    async def test_remaining_floored_at_zero(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.zremrangebyscore.return_value = 0
        fake.zcard.return_value = 15
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)

        limiter = RateLimiter(max_per_minute=10)
        assert await limiter.remaining() == 0

    @pytest.mark.asyncio
    async def test_exception_returns_max(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.zcard.side_effect = ConnectionError("down")
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)

        limiter = RateLimiter(max_per_minute=10)
        assert await limiter.remaining() == 10


class TestRateLimiterReset:
    @pytest.mark.asyncio
    async def test_redis_unavailable_noop(self, monkeypatch) -> None:
        monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
        limiter = RateLimiter(max_per_minute=10)
        await limiter.reset()  # must not raise

    @pytest.mark.asyncio
    async def test_reset_deletes_counter(self, monkeypatch) -> None:
        fake = MagicMock()
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        limiter = RateLimiter(max_per_minute=10)
        await limiter.reset()
        fake.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_exception_noop(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.delete.side_effect = ConnectionError("down")
        monkeypatch.setattr("app.core.redis.get_redis", lambda: fake)
        limiter = RateLimiter(max_per_minute=10)
        await limiter.reset()  # must not raise
