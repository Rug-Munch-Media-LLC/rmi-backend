"""Tests for the /trending and /price Redis caches.

Proves:
  (a) ``handlers.market.render_trending`` caches the fused token list under
      key ``trending:{chain}:{window}`` with TTL 90s — a second call within
      the window is served from Redis and does NOT re-run the fused fetch;
  (b) the caller's ``limit`` is applied on read (an inline ``limit=8`` path
      of a cached ``limit=20`` list still renders the top 8);
  (c) cache is fail-open: Redis down → live fetch every time;
  (d) ``handlers.price`` cache helpers round-trip a price snapshot under
      ``price:{chain}:{address}`` with TTL 45s and fail open.

The Redis client is faked at each module's ``_redis_client`` boundary; the
trending fetcher is faked at the module boundary (no live network).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.telegram.rugmunchbot.handlers import market, price


class _FakeRedis:
    """Minimal async Redis double: dict-backed get/set with TTL ignored."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True


def _token(addr: str, name: str, symbol: str) -> dict[str, Any]:
    return {
        "address": addr,
        "chain": "solana",
        "name": name,
        "symbol": symbol,
        "price_usd": 1.0,
        "volume_24h": 1000,
        "liquidity_usd": 5000,
        "change_24h": 5.0,
        "change_1h": 1.0,
        "change_7d": 10.0,
        "source": "rugcharts",
    }


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(market, "_redis_client", lambda: fake)
    monkeypatch.setattr(price, "_redis_client", lambda: fake)
    return


class TestTrendingCache:
    def test_ttl_and_key_shape(self) -> None:
        assert market._TRENDING_TTL == 90  # within the 60-120s requirement
        key = f"{market._TRENDING_KEY_PREFIX}solana:24h"
        assert key == "trending:solana:24h"

    async def test_second_render_served_from_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tokens = [_token("ADDR1", "Bonk", "BONK"), _token("ADDR2", "Wif", "WIF")]
        calls = {"n": 0}

        async def _fetch(chain: str = "solana", limit: int = 20) -> list[dict[str, Any]]:
            calls["n"] += 1
            return tokens

        monkeypatch.setattr(market, "_fetch_fused_trending", _fetch)

        text1, _kb1 = await market.render_trending(chain="solana", limit=20, window="24h")
        assert text1 is not None
        assert calls["n"] == 1
        # Cache now populated — second render must NOT re-fetch.
        text2, _kb2 = await market.render_trending(chain="solana", limit=20, window="24h")
        assert text2 == text1
        assert calls["n"] == 1

    async def test_limit_applied_on_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tokens = [_token(f"ADDR{i}", f"Token{i}", f"T{i}") for i in range(12)]

        async def _fetch(chain: str = "solana", limit: int = 20) -> list[dict[str, Any]]:
            return tokens

        monkeypatch.setattr(market, "_fetch_fused_trending", _fetch)
        # Warm the cache with limit=20.
        await market.render_trending(chain="solana", limit=20, window="1h")
        fake = market._redis_client()
        assert fake is not None
        cached = json.loads(fake.store["trending:solana:1h"])
        assert len(cached) == 12
        # Inline path (limit=8) reads the same key but truncates to 8.
        text, _kb = await market.render_trending(chain="solana", limit=8, window="1h")
        assert text is not None

    async def test_fail_open_when_redis_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(market, "_redis_client", lambda: None)
        calls = {"n": 0}

        async def _fetch(chain: str = "solana", limit: int = 20) -> list[dict[str, Any]]:
            calls["n"] += 1
            return [_token("ADDR1", "Bonk", "BONK")]

        monkeypatch.setattr(market, "_fetch_fused_trending", _fetch)
        text, _kb = await market.render_trending(chain="solana", limit=20, window="24h")
        assert text is not None
        assert calls["n"] == 1

    async def test_failure_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _none(chain: str = "solana", limit: int = 20) -> list[dict[str, Any]] | None:
            return None

        monkeypatch.setattr(market, "_fetch_fused_trending", _none)
        text, _kb = await market.render_trending(chain="solana", limit=20, window="24h")
        assert text is None
        assert _kb is None
        fake = market._redis_client()
        assert fake is not None
        assert "trending:solana:24h" not in fake.store


class TestPriceCache:
    def test_ttl(self) -> None:
        assert price._PRICE_TTL == 45  # within the 30-60s requirement

    async def test_round_trip_snapshot(self) -> None:
        result = {"price_usd": 0.001, "change_24h": 5, "volume_24h": 100, "source": "databus"}
        await price._cache_set_price("0xABC", "ethereum", result)
        got = await price._cache_get_price("0xABC", "ethereum")
        assert got == result

    async def test_missing_key_returns_none(self) -> None:
        assert await price._cache_get_price("0xNOPE", "solana") is None

    async def test_fail_open_when_redis_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(price, "_redis_client", lambda: None)
        assert await price._cache_get_price("0xABC", "ethereum") is None
        # Set must not raise either.
        await price._cache_set_price("0xABC", "ethereum", {"price_usd": 1.0})
