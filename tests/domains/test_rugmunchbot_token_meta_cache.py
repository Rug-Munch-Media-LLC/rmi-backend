"""Tests for the token-identity Redis cache in ``tools/token_meta.py``.

Proves the caching contract:
  (a) identity is cached by address with TTL 600s; price separately 60s;
  (b) a cache hit returns the merged identity+price WITHOUT any network call
      (the resolver's fallback chain is never invoked);
  (c) the cache is fail-open: Redis unavailable (``_redis_client()`` → None)
      degrades to live resolution and still returns a valid identity block;
  (d) ``scanner_intel`` resolves identity through the cached ``token_meta``
      tool path (``_token_meta_dict``) instead of bypassing it.

The Redis client is faked at the ``token_meta._redis_client`` boundary; the
backend/API fetchers are faked at the module boundary (no live network).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.telegram.rugmunchbot.tools import scanner as scanner_mod, token_meta
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

ADDR = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
CHAIN = "ethereum"


class _FakeRedis:
    """Minimal async Redis double: dict-backed get/set with TTL ignored."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route Redis through a fresh fake; stub the fallback chain."""
    fake = _FakeRedis()
    monkeypatch.setattr(token_meta, "_redis_client", lambda: fake)

    async def _stub(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
        return None

    monkeypatch.setattr(token_meta, "_meta_from_backend_databus", _stub)
    monkeypatch.setattr(token_meta, "_meta_from_backend_scanner", _stub)
    monkeypatch.setattr(token_meta, "_meta_from_dexscreener", _stub)
    monkeypatch.setattr(token_meta, "_meta_from_coingecko", _stub)
    return


class TestCacheTtls:
    def test_identity_and_price_ttls(self) -> None:
        assert token_meta._META_IDENTITY_TTL == 600
        assert token_meta._META_PRICE_TTL == 60

    def test_cache_keys_are_address_scoped(self) -> None:
        assert token_meta._identity_key(ADDR, CHAIN) == f"rmi:bot:token_meta:{CHAIN}:{ADDR}"
        assert token_meta._price_key(ADDR, CHAIN) == f"rmi:bot:token_price:{CHAIN}:{ADDR}"


class TestCacheBehavior:
    async def test_miss_resolves_live_then_populates_both_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        meta = await token_meta.resolve_token_meta(address=ADDR, chain=CHAIN)
        # No source returned anything → the canonical "no image" fallback block.
        assert meta["address"] == ADDR
        assert meta["image_url"] == token_meta.NO_IMAGE_AVAILABLE
        fake = token_meta._redis_client()
        assert fake is not None
        assert fake.store.get(f"rmi:bot:token_meta:{CHAIN}:{ADDR}") is not None

    async def test_hit_serves_cached_identity_without_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = token_meta._redis_client()
        assert fake is not None
        # Pre-populate both sub-keys as the live path would have.
        identity = {
            "address": ADDR,
            "name": "TestToken",
            "symbol": "TST",
            "chain": CHAIN,
            "image_url": "https://example.com/logo.png",
            "image_available": True,
            "image_source": "dexscreener",
        }
        fake.store[token_meta._identity_key(ADDR, CHAIN)] = __import__("json").dumps(identity)
        fake.store[token_meta._price_key(ADDR, CHAIN)] = __import__("json").dumps({"price_usd": "0.001234"})

        called = False

        async def _boom(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
            nonlocal called
            called = True
            raise AssertionError("fallback chain must not run on cache hit")

        monkeypatch.setattr(token_meta, "_meta_from_backend_databus", _boom)

        meta = await token_meta.resolve_token_meta(address=ADDR, chain=CHAIN)
        assert called is False
        assert meta["name"] == "TestToken"
        assert meta["symbol"] == "TST"
        assert meta["price_usd"] == "0.001234"  # merged from the 60s price key
        assert meta["image_url"] == "https://example.com/logo.png"
        assert meta["image_available"] is True

    async def test_expired_price_key_falls_through_to_live_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = token_meta._redis_client()
        assert fake is not None
        identity = {
            "address": ADDR,
            "name": "TestToken",
            "symbol": "TST",
            "chain": CHAIN,
            "image_url": token_meta.NO_IMAGE_AVAILABLE,
            "image_available": False,
            "image_source": "none",
        }
        fake.store[token_meta._identity_key(ADDR, CHAIN)] = __import__("json").dumps(identity)
        # NO price key — price expired → live re-resolution for a fresh price.

        async def _live_price(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
            return {
                "address": ADDR,
                "chain": CHAIN,
                "name": "TestToken",
                "symbol": "TST",
                "price_usd": 0.5,
                "image_url": token_meta.NO_IMAGE_AVAILABLE,
                "image_available": False,
                "image_source": "none",
            }

        monkeypatch.setattr(token_meta, "_meta_from_backend_databus", _live_price)
        meta = await token_meta.resolve_token_meta(address=ADDR, chain=CHAIN)
        assert meta["price_usd"] == 0.5
        # The fresh price was re-cached under the 60s key.
        assert fake.store.get(token_meta._price_key(ADDR, CHAIN)) is not None

    async def test_fail_open_when_redis_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(token_meta, "_redis_client", lambda: None)

        async def _live(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
            return {
                "address": ADDR,
                "chain": CHAIN,
                "name": "LiveToken",
                "symbol": "LIVE",
                "price_usd": 1.0,
                "image_url": "https://example.com/live.png",
                "image_available": True,
                "image_source": "dexscreener",
            }

        monkeypatch.setattr(token_meta, "_meta_from_backend_databus", _live)
        meta = await token_meta.resolve_token_meta(address=ADDR, chain=CHAIN)
        assert meta["name"] == "LiveToken"
        assert meta["price_usd"] == 1.0


class TestScannerRoutesThroughTokenMeta:
    async def test_scanner_intel_uses_cached_token_meta_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """scanner_intel must resolve identity via ``token_meta`` (cached),
        not ``resolve_token_meta`` directly (the previous bypass)."""
        meta = {"address": ADDR, "name": "T", "symbol": "TST", "image_url": "x", "image_available": True}

        async def _fake_token_meta(**kwargs: Any) -> ToolResult:
            return ToolResult(tool="token_meta", ok=True, data=meta)

        monkeypatch.setattr(scanner_mod, "token_meta", _fake_token_meta)
        resolved = await scanner_mod._token_meta_dict(ADDR, CHAIN, symbol="TST")
        assert resolved == meta
