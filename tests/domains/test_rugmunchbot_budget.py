"""Unit tests for the RugMunchBot credit-efficiency layer.

Covers the pieces added during the de-godding + API-credit pass:

  * ``tools/budget.py`` — per-provider per-minute call caps, cumulative
    credit-burn counters, "one source first, fallback only on miss" gate,
    and the stable-data TTL policy (prices 60s / trending 300s /
    market-overview 600s);
  * ``tools/_cache.py`` — stable, cheap hashed cache keys + policy TTLs;
  * ``tools/_client.py`` — every tool HTTP call is budget-gated BEFORE the
    request is made (over-budget providers degrade to a miss, never burn);
  * ``tools/menus.py`` — data-driven capability menu + help card.

No live network I/O: ``httpx.AsyncClient`` is faked at the ``_client``
module boundary like the rest of the tools test suite.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.domains.telegram.rugmunchbot.tools import _cache, _client, budget as budget_module, menus
from app.domains.telegram.rugmunchbot.tools.budget import (
    TTL_POLICY,
    BudgetTracker,
    effective_ttl,
    provider_for_url,
)

# The module-level tracker singleton (the `budget` module name is taken above).
budget = budget_module.budget


@pytest.fixture(autouse=True)
def _reset_budget() -> None:
    """Every test starts with a clean budget — the module singleton is shared."""
    budget.reset()
    yield
    budget.reset()


# ══════════════════════════════════════════════════════════════════════════
# provider_for_url — host → budget bucket
# ══════════════════════════════════════════════════════════════════════════


class TestProviderForUrl:
    def test_known_public_hosts(self) -> None:
        assert provider_for_url("https://api.dexscreener.com/latest/dex/tokens/x") == "dexscreener"
        assert provider_for_url("https://api.coingecko.com/api/v3/global") == "coingecko"
        assert provider_for_url("https://public-api.birdeye.so/defi/price") == "birdeye"
        assert provider_for_url("https://api.coinpaprika.com/v1/search") == "coinpaprika"
        assert provider_for_url("https://api.coincap.io/v2/assets") == "coincap"
        assert provider_for_url("https://api.llama.fi/v2/chains") == "defillama"
        assert provider_for_url("https://api.alternative.me/fng") == "alternative"

    def test_localhost_ports_map_to_local_services(self) -> None:
        assert provider_for_url("http://localhost:8000/api/v1/market/trending") == "backend"
        assert provider_for_url("http://127.0.0.1:8095/chat/context") == "bridge"
        assert provider_for_url("http://127.0.0.1:8889/search?q=x") == "searxng"

    def test_docker_backend_host(self) -> None:
        assert provider_for_url("http://rmi-backend:8000/api/v1/databus/token/x") == "backend"

    def test_unknown_host_conservative_bucket(self) -> None:
        assert provider_for_url("https://some.unmapped.api/v1") == "unknown"

    def test_explicit_provider_wins(self) -> None:
        assert provider_for_url("https://api.dexscreener.com/x", provider="coingecko") == "coingecko"
        assert provider_for_url("http://127.0.0.1:8000/x", provider="backend") == "backend"

    def test_query_and_fragment_stripped(self) -> None:
        assert provider_for_url("https://api.dexscreener.com/latest/dex/search?q=BONK#top") == "dexscreener"


# ══════════════════════════════════════════════════════════════════════════
# BudgetTracker — per-minute cap, misses, cumulative totals
# ══════════════════════════════════════════════════════════════════════════


class TestBudgetTracker:
    def test_acquire_counts_and_respects_cap(self) -> None:
        tracker = BudgetTracker()
        # coingecko cap is 15/min — the 16th call must be refused as a miss.
        for _ in range(budget_module.PROVIDER_LIMITS["coingecko"]):
            assert tracker.acquire("coingecko") is True
        assert tracker.acquire("coingecko") is False
        assert tracker.miss_count("coingecko") == 1
        assert tracker.total_count("coingecko") == budget_module.PROVIDER_LIMITS["coingecko"]
        assert tracker.window_count("coingecko") == budget_module.PROVIDER_LIMITS["coingecko"]

    def test_providers_are_independent(self) -> None:
        tracker = BudgetTracker()
        for _ in range(budget_module.PROVIDER_LIMITS["coingecko"]):
            tracker.acquire("coingecko")
        # DexScreener has its own cap and its own window — still open.
        assert tracker.acquire("dexscreener") is True
        assert tracker.miss_count("dexscreener") == 0

    def test_window_expires_after_60s(self) -> None:
        tracker = BudgetTracker()
        for _ in range(budget_module.PROVIDER_LIMITS["coingecko"]):
            tracker.acquire("coingecko")
        assert tracker.acquire("coingecko") is False
        # Pretend 61s passed — the window slides and the cap reopens.
        q = tracker._window["coingecko"]
        oldest = q[0]
        q[0] = oldest - 61.0  # type: ignore[index]  # test-only time shift
        assert tracker.acquire("coingecko") is True

    def test_summary_reports_credit_burn(self) -> None:
        tracker = BudgetTracker()
        tracker.acquire("dexscreener")
        tracker.acquire("dexscreener")
        tracker.acquire("birdeye")
        summary = tracker.summary()
        assert summary["dexscreener"]["total"] == 2
        assert summary["birdeye"]["total"] == 1
        assert summary["dexscreener"]["limit"] == budget_module.PROVIDER_LIMITS["dexscreener"]
        assert "window" in summary["dexscreener"]

    def test_unknown_provider_gets_conservative_default(self) -> None:
        tracker = BudgetTracker()
        for _ in range(budget_module.DEFAULT_PROVIDER_LIMIT):
            assert tracker.acquire("unknown") is True
        assert tracker.acquire("unknown") is False


# ══════════════════════════════════════════════════════════════════════════
# TTL policy — stable data lives longer (fewer API calls)
# ══════════════════════════════════════════════════════════════════════════


class TestTTLPolicy:
    def test_required_stable_data_ttls(self) -> None:
        assert TTL_POLICY["price_lookup"] == 60.0
        assert TTL_POLICY["trending"] == 300.0
        assert TTL_POLICY["market_overview"] == 600.0

    def test_effective_ttl_policy_wins_for_known_namespaces(self) -> None:
        # Registry currently registers trending ttl=120 and market ttl=60 —
        # the policy must extend those to the stable-data TTLs.
        assert effective_ttl("trending", 120.0) == 300.0
        assert effective_ttl("market_overview", 60.0) == 600.0
        assert effective_ttl("price_lookup", 60.0) == 60.0

    def test_effective_ttl_keeps_caller_ttl_for_unknown_namespaces(self) -> None:
        assert effective_ttl("no_such_tool", 42.0) == 42.0


# ══════════════════════════════════════════════════════════════════════════
# _cache — stable + cheap keys, policy-applied TTLs
# ══════════════════════════════════════════════════════════════════════════


class TestCacheKeys:
    def test_key_is_stable_and_cheap(self) -> None:
        k1 = _cache.make_cache_key("price_lookup", "address=0xabc")
        k2 = _cache.make_cache_key("price_lookup", "address=0xabc")
        assert k1 == k2  # stable across calls/runs
        assert len(k1) <= 32  # cheap / bounded

    def test_key_differs_by_namespace_or_query(self) -> None:
        base = _cache.make_cache_key("price_lookup", "address=0xabc")
        assert _cache.make_cache_key("price_lookup", "address=0xabd") != base
        assert _cache.make_cache_key("trending", "address=0xabc") != base

    def test_cache_applies_policy_ttl_on_set(self) -> None:
        cache: _cache.TTLCache[str] = _cache.TTLCache()
        cache.set("trending", "limit=10", "value", ttl=120.0)
        (namespace, _skey), (expires_at, value) = next(iter(cache._snapshot().items()))
        assert namespace == "trending"
        assert value == "value"
        # Policy TTL 300s, not the registry-passed 120s.
        assert 299.0 < expires_at - time.monotonic() <= 300.0

    def test_cache_get_set_roundtrip(self) -> None:
        cache: _cache.TTLCache[str] = _cache.TTLCache()
        cache.set("price_lookup", "symbol=BONK", "data", ttl=60.0)
        assert cache.get("price_lookup", "symbol=BONK") == "data"
        assert cache.get("price_lookup", "symbol=WIF") is None


# ══════════════════════════════════════════════════════════════════════════
# _client — every tool HTTP call is budget-gated
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class _FakeClient:
    """Minimal httpx.AsyncClient fake recording requests."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeClient.calls.append(url)
        return _Resp(200, {"ok": True, "url": url})

    async def post(self, url: str, json: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeClient.calls.append(url)
        return _Resp(200, {"ok": True, "url": url})


class TestClientBudgetGate:
    @pytest.fixture(autouse=True)
    def _fake_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _FakeClient.calls = []
        monkeypatch.setattr(_client.httpx, "AsyncClient", _FakeClient)
        return None

    async def test_http_makes_request_and_counts_provider(self) -> None:
        result = await _client.http_get_json("https://api.dexscreener.com/latest/dex/search?q=BONK")
        assert result == {"ok": True, "url": "https://api.dexscreener.com/latest/dex/search?q=BONK"}
        assert len(_FakeClient.calls) == 1
        assert budget.total_count("dexscreener") == 1
        assert budget.miss_count("dexscreener") == 0

    async def test_over_budget_provider_is_not_called(self) -> None:
        # Exhaust the coingecko per-minute cap via the tracker itself.
        for _ in range(budget_module.PROVIDER_LIMITS["coingecko"]):
            assert budget.acquire("coingecko") is True

        result = await _client.http_get_json("https://api.coingecko.com/api/v3/global")
        assert result is None  # miss — treated like a dead source
        assert _FakeClient.calls == []  # NO request was made (no credit burned)
        assert budget.miss_count("coingecko") == 1

    async def test_post_is_also_gated(self) -> None:
        result = await _client.http_post_json("http://127.0.0.1:8095/chat/context", json={"message": "hi"})
        assert result is not None
        assert budget.total_count("bridge") == 1

    async def test_budget_gate_triggers_fallback_chain(self) -> None:
        """One source first, fallback only on miss: when the primary provider
        is over budget the NEXT provider answers instead of a 5-API storm."""
        for _ in range(budget_module.PROVIDER_LIMITS["coingecko"]):
            budget.acquire("coingecko")

        # Primary (coingecko) → miss; fallback (dexscreener) → answered.
        primary = await _client.http_get_json("https://api.coingecko.com/api/v3/global")
        fallback = await _client.http_get_json("https://api.dexscreener.com/latest/dex/search?q=BONK")
        assert primary is None
        assert fallback is not None
        urls = _FakeClient.calls
        assert all("coingecko" not in u for u in urls)
        assert any("dexscreener" in u for u in urls)


# ══════════════════════════════════════════════════════════════════════════
# menus — data-driven capability menu + help card
# ══════════════════════════════════════════════════════════════════════════


class TestMenus:
    def test_capability_list_has_required_surfaces(self) -> None:
        labels = " | ".join(c.label for c in menus.CAPABILITIES)
        assert "Scan" in labels
        assert "Trending" in labels
        assert "Price" in labels
        assert "TA" in labels
        assert "Prediction" in labels
        assert "Market" in labels
        assert "Smart money" in labels
        assert "Ask knowledge" in labels

    def test_menu_keyboard_builds_all_buttons_plus_back(self) -> None:
        kb = menus.capability_menu_kb()
        rows = kb.inline_keyboard
        flat = [b for row in rows for b in row]
        assert len(flat) == len(menus.CAPABILITIES) + 1  # + back-to-menu
        assert flat[-1].callback_data == "menu_main"
        # Every capability callback is present exactly once.
        callbacks = [b.callback_data for b in flat[:-1]]
        assert sorted(callbacks) == sorted(c.callback for c in menus.CAPABILITIES)

    def test_help_text_lists_capabilities(self) -> None:
        text = menus.capability_help_text()
        assert "What can Rug Munch Bot do?" in text
        for c in menus.CAPABILITIES:
            assert c.label in text

    def test_extensible_by_appending(self) -> None:
        # The builder derives everything from CAPABILITIES — a new tuple
        # entry needs no other code change.
        original = len(menus.CAPABILITIES)
        augmented = (*menus.CAPABILITIES, menus.Capability("New", "desc", "run_new"))
        assert len(augmented) == original + 1
        # Rebuilding the grid with the augmented tuple is a pure function of
        # the tuple length — no switch, no registry, no new handler code.
        rows: list[list[Any]] = []
        for i in range(0, len(augmented), 2):
            rows.append(list(augmented[i : i + 2]))
        assert sum(len(r) for r in rows) == original + 1

    def test_capability_by_callback_lookup(self) -> None:
        assert menus.capability_by_callback("run_trending") is not None
        assert menus.capability_by_callback("no_such_cb") is None

    def test_chart_capability_present(self) -> None:
        # v12: Chart is a first-class capability wired to the existing
        # run_chart usage card (chart image + indicators).
        labels = " | ".join(c.label for c in menus.CAPABILITIES)
        assert "📈 Chart" in labels
        chart = menus.capability_by_callback("run_chart")
        assert chart is not None
        assert chart.label == "📈 Chart"
