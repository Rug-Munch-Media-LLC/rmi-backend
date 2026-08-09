"""Tests for rugmunchbot daily market context (daily_context).

Verifies:
  * fetch_daily_data() assembles all sections from the four backend routes
  * format_daily_context() renders the exact LLM-facing compact string
  * format_market_section() renders the compact /start section
  * get_daily_context() caches in Redis (5 min TTL) and reuses the snapshot
  * fail-open: backend / Redis failures return "" instead of raising

No network calls: ``backend_get_json`` is monkeypatched with canned bodies
and ``get_redis_async`` is replaced with an in-memory fake.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domains.telegram.rugmunchbot import daily_context as dc


class FakeRedis:
    """Minimal async Redis stand-in (get/setex over an in-memory dict)."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> bytes | None:
        entry = self.store.get(key)
        if entry is None:
            return None
        return entry["value"].encode()

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = {"value": value, "ttl": ex}


# ── Canned backend responses (real route shapes) ────────────────────────

OVERVIEW_BODY = {
    "total_market_cap": 3.2e12,
    "total_volume_24h": 85e9,
    "btc_dominance": 60.5,
    "eth_dominance": 10.2,
    "btc_price": 67000.0,
    "btc_change_24h": 1.2,
    "eth_price": 3450.0,
    "eth_change_24h": 0.8,
    "fear_greed": {"value": 62, "classification": "Greed", "timestamp": "2026-08-09T00:00:00Z"},
    "updated_at": "2026-08-09T00:00:00Z",
}

MACRO_BODY = {
    "items": [
        {"asset": "BTC", "current_price": 67123.45, "change_24h": 2.4},
        {"asset": "ETH", "current_price": 3512.89, "change_24h": 1.1},
    ],
    "updated_at": "2026-08-09T00:00:00Z",
}

NEWS_BODY = {
    "items": [
        {
            "news_id": "n1",
            "title": "Bitcoin ETFs see record $2B daily inflows as institutions pile in",
            "source": "coindesk",
        },
        {"news_id": "n2", "title": "Solana DeFi TVL hits new all-time high"},
        {"news_id": "n3", "title": "Ethereum Pectra upgrade goes live on mainnet"},
    ],
    "total": 3,
    "offset": 0,
}

TRENDING_BODY = {
    "items": [
        {"symbol": "PEPE", "name": "Pepe", "change_24h": 12.34},
        {"symbol": "DOGE", "name": "Dogecoin", "change_24h": 4.2},
        {"symbol": "SOL", "name": "Solana", "change_24h": 2.1},
    ],
    "total": 3,
    "updated_at": "2026-08-09T00:00:00Z",
}

LAUNCHES_BODY = {
    "items": [
        {"token_address": "abc123...", "chain": "solana", "name": "MoonRocket", "symbol": "MR"},
        {"token_address": "def456...", "chain": "ethereum", "name": "WAGMI", "symbol": "WAGMI"},
        {"token_address": "ghl789...", "chain": "base", "name": "", "symbol": "BASED"},
        {"token_address": "0000000000000000000000000000000000000000", "chain": "ethereum", "name": "", "symbol": ""},
        {"token_address": "0xabc1234567890...", "chain": "base", "name": "DegenCoin", "symbol": "DGN"},
    ],
    "count": 5,
    "chain": "all",
    "sources": ["gmgn", "dexscreener_search"],
    "generated_at": "2026-08-09T00:00:00Z",
}

WHEN = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fr = FakeRedis()
    monkeypatch.setattr(dc, "get_redis_async", lambda: fr)
    return fr


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Monkeypatch backend_get_json with canned bodies per path."""

    async def _fake(
        path: str, params: dict[str, Any] | None = None, timeout_secs: float = 5.0
    ) -> dict[str, Any] | None:
        return {
            dc.OVERVIEW_PATH: OVERVIEW_BODY,
            dc.MACRO_PATH: MACRO_BODY,
            dc.NEWS_PATH: NEWS_BODY,
            dc.TRENDING_PATH: TRENDING_BODY,
            dc.LAUNCHES_PATH: LAUNCHES_BODY,
        }.get(path)

    monkeypatch.setattr(dc, "backend_get_json", _fake)
    return {}


class TestFetchDailyData:
    async def test_assembles_all_sections(self, fake_backend: dict[str, Any]) -> None:
        data = await dc.fetch_daily_data()

        # Prices now come from /market/overview (single-call) — macro is fallback.
        assert data["prices"] == {"btc": 67000.0, "btc_change_24h": 1.2, "eth": 3450.0, "eth_change_24h": 0.8}
        assert data["total_mcap"] == 3.2e12
        assert data["btc_dominance"] == 60.5
        assert data["fear_greed_value"] == 62
        assert data["fear_greed_label"] == "Greed"
        assert len(data["news"]) == 3
        assert data["news"][0].startswith("Bitcoin ETFs")
        assert data["trending"] == ["PEPE +12.3%", "DOGE +4.2%", "SOL +2.1%"]
        assert len(data["launches"]) == 5
        # name+symbol chain rendering
        assert data["launches"][0] == "MoonRocket (MR) [solana]"
        # empty name but symbol set -> symbol
        assert data["launches"][2] == "BASED [base]"
        # fully empty name/symbol -> short address
        assert data["launches"][3] == "00000000\u2026 [ethereum]"

    async def test_prices_fall_back_to_macro_when_overview_has_no_prices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Older /market/overview responses (no btc_price/eth_price fields) must
        still resolve prices via /market/macro — no regression for pre-fix backends."""
        no_price_overview = {k: v for k, v in OVERVIEW_BODY.items() if not k.startswith(("btc_", "eth_"))}

        async def _fake(
            path: str, params: dict[str, Any] | None = None, timeout_secs: float = 5.0
        ) -> dict[str, Any] | None:
            return {
                dc.OVERVIEW_PATH: no_price_overview,
                dc.MACRO_PATH: MACRO_BODY,
                dc.NEWS_PATH: NEWS_BODY,
                dc.TRENDING_PATH: TRENDING_BODY,
                dc.LAUNCHES_PATH: LAUNCHES_BODY,
            }.get(path)

        monkeypatch.setattr(dc, "backend_get_json", _fake)

        data = await dc.fetch_daily_data()
        assert data["prices"] == {"btc": 67123.45, "eth": 3512.89}

    async def test_all_backend_failures_yield_empty_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _down(path: str, params: dict[str, Any] | None = None, timeout_secs: float = 5.0) -> None:
            return None

        monkeypatch.setattr(dc, "backend_get_json", _down)

        data = await dc.fetch_daily_data()
        assert data["prices"] == {"btc": None, "eth": None}
        assert data["fear_greed_value"] is None
        assert data["news"] == []
        assert data["trending"] == []
        assert data["launches"] == []
        assert dc._has_content(data) is False

    async def test_partial_failure_keeps_working_sections(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _partial(
            path: str, params: dict[str, Any] | None = None, timeout_secs: float = 5.0
        ) -> dict[str, Any] | None:
            if path == dc.NEWS_PATH:
                return NEWS_BODY
            return None

        monkeypatch.setattr(dc, "backend_get_json", _partial)

        data = await dc.fetch_daily_data()
        assert len(data["news"]) == 3
        assert data["prices"] == {"btc": None, "eth": None}


class TestFormatDailyContext:
    def test_exact_compact_format(self) -> None:
        data = {
            "prices": {"btc": 67123.45, "eth": 3512.89},
            "total_mcap": 3.2e12,
            "btc_dominance": 60.5,
            "fear_greed_value": 62,
            "fear_greed_label": "Greed",
            "news": ["Bitcoin ETFs see record inflows", "Solana DeFi TVL ATH"],
            "trending": ["PEPE +12.3%", "DOGE +4.2%"],
            "launches": ["MoonRocket (MR) [solana]", "WAGMI (WAGMI) [ethereum]"],
        }
        text = dc.format_daily_context(data, when=WHEN)

        assert text.startswith(
            "CURRENT MARKET CONTEXT (Aug 9 2026): Bitcoin $67,123, ETH $3,513, Fear/Greed: 62 (Greed)."
        )
        assert "Top news: Bitcoin ETFs see record inflows; Solana DeFi TVL ATH." in text
        assert "Trending: PEPE +12.3%; DOGE +4.2%." in text
        assert "Recent launches: MoonRocket (MR) [solana]; WAGMI (WAGMI) [ethereum]." in text

    def test_missing_sections_render_gracefully(self) -> None:
        text = dc.format_daily_context({}, when=WHEN)
        assert "CURRENT MARKET CONTEXT (Aug 9 2026): Bitcoin n/a, Fear/Greed: n/a." in text
        assert "Top news: none." in text
        assert "Trending: none." in text
        assert "Recent launches: none." in text

    def test_btc_only_price(self) -> None:
        data = {"prices": {"btc": 67000.0, "eth": None}}
        text = dc.format_daily_context(data, when=WHEN)
        assert "Bitcoin $67,000, ETH n/a" not in text
        assert text.startswith("CURRENT MARKET CONTEXT (Aug 9 2026): Bitcoin $67,000, Fear/Greed: n/a.")


class TestFormatMarketSection:
    def test_compact_section_renders(self) -> None:
        data = {
            "prices": {"btc": 67123.45, "eth": 3512.89},
            "total_mcap": 3.2e12,
            "btc_dominance": 60.5,
            "fear_greed_value": 62,
            "fear_greed_label": "Greed",
            "news": ["Bitcoin ETFs see record $2B daily inflows"],
            "trending": ["PEPE +12.3%", "DOGE +4.2%"],
            "launches": ["MoonRocket (MR) [solana]"],
        }
        text = dc.format_market_section(data, when=WHEN)

        assert "<b>Today's Market</b> (Aug 9)" in text
        assert "BTC $67,123" in text
        assert "ETH $3,513" in text
        assert "F&G 62 (Greed)" in text
        assert "MCap $3.20T" in text
        assert "BTC dom 60.5%" in text
        assert "PEPE +12.3% · DOGE +4.2%" in text
        assert "Bitcoin ETFs see record $2B daily inflows" in text

    def test_section_escapes_html_from_news(self) -> None:
        data = {"news": ["Token <b>pumps</b> & dumps"], "trending": [], "launches": []}
        text = dc.format_market_section(data, when=WHEN)
        assert "<b>pumps</b>" not in text  # escaped
        assert "&lt;b&gt;pumps&lt;/b&gt;" in text


class TestCacheAndPublicApi:
    async def test_get_daily_context_caches_in_redis(self, fake_redis: FakeRedis, fake_backend: dict[str, Any]) -> None:
        first = await dc.get_daily_context()
        second = await dc.get_daily_context()

        assert first == second
        # Date is wall-clock-dependent; pin the stable parts only.
        assert first.startswith("CURRENT MARKET CONTEXT (")
        # Prices come from /market/overview (single-call) — the fixture's
        # OVERVIEW_BODY btc_price=67000.0 is the primary source, not macro.
        assert "Bitcoin $67,000" in first
        # Snapshot stored under the cache key with a 5-minute TTL.
        entry = fake_redis.store[dc.CACHE_KEY]
        assert entry["ttl"] == dc.CACHE_TTL
        cached = json.loads(entry["value"])
        assert cached["fear_greed_value"] == 62

    async def test_get_daily_context_fail_open_empty(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _down(path: str, params: dict[str, Any] | None = None, timeout_secs: float = 5.0) -> None:
            return None

        monkeypatch.setattr(dc, "backend_get_json", _down)

        assert await dc.get_daily_context() == ""
        assert await dc.get_market_section() == ""
        # Empty snapshots are never cached (transient outage self-heals).
        assert dc.CACHE_KEY not in fake_redis.store

    async def test_redis_down_still_returns_context(
        self, monkeypatch: pytest.MonkeyPatch, fake_backend: dict[str, Any]
    ) -> None:
        monkeypatch.setattr(dc, "get_redis_async", lambda: None)

        text = await dc.get_daily_context()
        assert text.startswith("CURRENT MARKET CONTEXT (")
        # Overview is the primary price source since 2026-08-09 (single-call);
        # fixture OVERVIEW_BODY btc_price=67000.0 wins over macro's 67123.45.
        assert "Bitcoin $67,000" in text

    async def test_market_section_uses_same_cache(self, fake_redis: FakeRedis, fake_backend: dict[str, Any]) -> None:
        section = await dc.get_market_section()
        assert "<b>Today's Market</b>" in section
        assert dc.CACHE_KEY in fake_redis.store
