"""Unit tests for Redis-backed admin API key revocation enforcement.

Covers the auth middleware change that wires the admin panel's ``rmi:api_keys``
Redis store into request authentication, so a revoked or expired key is
actually rejected instead of remaining valid.

The logic under test is ``app.core.auth._is_valid_admin_api_key`` and its
read-through cache helper ``_valid_admin_api_key_hashes``.
"""

from __future__ import annotations

import hashlib
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.core import auth as auth_module


def _key_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _store_entry(raw_key: str, *, status: str = "active", expires_at=None) -> str:
    return json.dumps(
        {
            "name": "test",
            "key_hash": _key_hash(raw_key),
            "scopes": [],
            "status": status,
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": expires_at,
        }
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the module-level cache between tests."""
    auth_module._admin_api_keys_cache = None
    yield
    auth_module._admin_api_keys_cache = None


@pytest.fixture
def fake_redis():
    """Return an AsyncMock whose hgetall returns the given store dict."""
    store: dict[str, str] = {}

    async def _hgetall(_name: str) -> dict[str, str]:
        return dict(store)

    client = AsyncMock()
    client.hgetall = AsyncMock(side_effect=_hgetall)
    return client, store


async def test_active_key_is_valid(fake_redis) -> None:
    client, store = fake_redis
    raw = "rmi_active_key_123"
    store["key_1"] = _store_entry(raw)

    with patch("app.core.redis.get_redis_async", return_value=client):
        assert await auth_module._is_valid_admin_api_key(raw) is True


async def test_revoked_key_is_rejected(fake_redis) -> None:
    client, store = fake_redis
    raw = "rmi_revoked_key_123"
    store["key_1"] = _store_entry(raw, status="revoked")

    with patch("app.core.redis.get_redis_async", return_value=client):
        assert await auth_module._is_valid_admin_api_key(raw) is False


async def test_expired_key_is_rejected(fake_redis) -> None:
    client, store = fake_redis
    raw = "rmi_expired_key_123"
    store["key_1"] = _store_entry(raw, expires_at=time.time() - 1000)

    with patch("app.core.redis.get_redis_async", return_value=client):
        assert await auth_module._is_valid_admin_api_key(raw) is False


async def test_unknown_key_is_rejected(fake_redis) -> None:
    client, store = fake_redis
    store["key_1"] = _store_entry("rmi_some_other_key")

    with patch("app.core.redis.get_redis_async", return_value=client):
        assert await auth_module._is_valid_admin_api_key("rmi_unknown_key") is False


async def test_empty_store_rejects(fake_redis) -> None:
    client, _store = fake_redis

    with patch("app.core.redis.get_redis_async", return_value=client):
        assert await auth_module._is_valid_admin_api_key("rmi_any_key") is False


async def test_cache_invalidation_drops_revoked_key(fake_redis) -> None:
    """After a key is revoked and the cache invalidated, it is rejected."""
    client, store = fake_redis
    raw = "rmi_cache_key_123"
    store["key_1"] = _store_entry(raw)

    with patch("app.core.redis.get_redis_async", return_value=client):
        # First call populates the cache with the active key.
        assert await auth_module._is_valid_admin_api_key(raw) is True

        # Revoke the key in the store and invalidate the cache.
        store["key_1"] = _store_entry(raw, status="revoked")
        auth_module._invalidate_admin_api_keys_cache()

        # Now the key must be rejected.
        assert await auth_module._is_valid_admin_api_key(raw) is False
