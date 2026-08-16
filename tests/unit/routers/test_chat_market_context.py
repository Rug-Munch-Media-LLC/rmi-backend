"""Unit tests for the chat market-context block.

Covers ``app/routers/chat/_market_context.py`` (the additive context builder
injected into every chat system prompt) and its injection point in
``app/routers/chat/_messages.py``:

  - Zero-fabrication fallback: sources down → block is "" (never errors,
    never invents numbers).
  - Real values → compact block with BTC/ETH price + 24h, Fear & Greed,
    one-line verdict, and real trending symbols.
  - No minute-granularity timestamp (prefix-cache safety).
  - ≤300-token budget.
  - 60s cache: a second call within the TTL does not refetch upstream.
  - ``market_verdict`` pure function.
  - ``_build_messages`` appends the block AFTER the user turn (volatile data
    must stay past the byte-stable system-prompt prefix for llama.cpp
    ``cache_prompt`` KV reuse).
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest

from app.routers.chat import _market_context as mc
from app.routers.chat._messages import _build_messages
from app.routers.market_intel import cache as market_cache

PRICE_FIXTURE = {
    "bitcoin": {"usd": 67234.5, "usd_24h_change": 1.8},
    "ethereum": {"usd": 3521.25, "usd_24h_change": 2.3},
}

FNG_FIXTURE = {
    "data": [{"value": "68", "value_classification": "Greed"}],
}

TRENDING_FIXTURE = {
    "coins": [
        {"item": {"symbol": "pepe"}},
        {"item": {"symbol": "wif"}},
        {"item": {"symbol": "jup"}},
        {"item": {"symbol": "fet"}},
        {"item": {"symbol": "aioz"}},
    ]
}


@pytest.fixture(autouse=True)
def _clear_market_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the shared market-intel TTL cache between tests.

    Also pins the own-data enrichment seam to a no-op: the enrichment helper
    (``_trending_enrichment``) talks to ClickHouse + the shared CoinGecko
    provider, which unit tests must never touch. Dedicated enrichment
    coverage lives in ``test_chat_trending_enrichment.py``.
    """
    market_cache._cache.clear()
    monkeypatch.setattr(mc, "_fetch_enrichment_lines", AsyncMock(return_value=[]))
    yield
    market_cache._cache.clear()


def _patch_upstreams(monkeypatch: pytest.MonkeyPatch, *, prices, fng, trending) -> AsyncMock:
    """Point the chat module's upstream fetchers at fixture data.

    Returns the patched ``_cg`` mock so tests can assert call counts.
    """
    cg_mock = AsyncMock(side_effect=[prices, trending])
    monkeypatch.setattr(mc, "_cg", cg_mock)
    monkeypatch.setattr(mc, "_fng", AsyncMock(return_value=fng))
    return cg_mock


# ── Zero-fabrication fallback ──────────────────────────────────────────


async def test_build_returns_empty_when_sources_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """All sources down → block is "" (never raises, never fabricates)."""
    monkeypatch.setattr(mc, "_cg", AsyncMock(return_value=None))
    monkeypatch.setattr(mc, "_fng", AsyncMock(return_value=None))
    assert await mc.build_market_context_block() == ""


async def test_build_omits_block_without_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prices missing (even with F&G present) → omit the whole block."""
    _patch_upstreams(monkeypatch, prices={}, fng=FNG_FIXTURE, trending=TRENDING_FIXTURE)
    assert await mc.build_market_context_block() == ""


async def test_build_omits_block_without_fear_greed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fear & Greed missing → omit the whole block (verdict would be unfounded)."""
    _patch_upstreams(monkeypatch, prices=PRICE_FIXTURE, fng=None, trending=TRENDING_FIXTURE)
    assert await mc.build_market_context_block() == ""


# ── Happy path ─────────────────────────────────────────────────────────


async def test_build_returns_compact_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real values → block carries price, F&G, verdict, trending, day date."""
    _patch_upstreams(monkeypatch, prices=PRICE_FIXTURE, fng=FNG_FIXTURE, trending=TRENDING_FIXTURE)
    block = await mc.build_market_context_block()
    assert block.startswith("MARKET CONTEXT (live, as of ")
    assert "BTC $67,234 (+1.8% 24h)" in block
    assert "ETH $3,521 (+2.3% 24h)" in block
    assert "Fear & Greed: 68 (Greed)" in block
    assert "Verdict: risk-on" in block
    assert "Trending: PEPE, WIF, JUP, FET, AIOZ" in block


async def test_build_omits_trending_line_when_trending_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trending source down → block keeps prices/F&G/verdict, no Trending line."""
    _patch_upstreams(monkeypatch, prices=PRICE_FIXTURE, fng=FNG_FIXTURE, trending=None)
    block = await mc.build_market_context_block()
    assert "BTC $67,234" in block
    assert "Fear & Greed: 68" in block
    assert "Trending:" not in block


# ── Freshness / cache-safety ───────────────────────────────────────────


async def test_block_has_no_minute_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """No minute-granularity time (``HH:MM``) in the block — prefix-cache safe."""
    _patch_upstreams(monkeypatch, prices=PRICE_FIXTURE, fng=FNG_FIXTURE, trending=TRENDING_FIXTURE)
    block = await mc.build_market_context_block()
    assert re.search(r"\b\d{2}:\d{2}\b", block) is None


async def test_block_under_token_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block stays well under the 300-token budget (word-count proxy)."""
    _patch_upstreams(monkeypatch, prices=PRICE_FIXTURE, fng=FNG_FIXTURE, trending=TRENDING_FIXTURE)
    block = await mc.build_market_context_block()
    assert len(block.split()) <= 300


async def test_block_cached_for_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Within the 60s TTL the second call does not touch upstream again.

    First build fires ``_cg`` twice (price + trending). The second build
    within the TTL returns the cached block → no additional ``_cg`` calls.
    """
    cg_mock = _patch_upstreams(monkeypatch, prices=PRICE_FIXTURE, fng=FNG_FIXTURE, trending=TRENDING_FIXTURE)
    first = await mc.build_market_context_block()
    second = await mc.build_market_context_block()
    assert first == second
    assert cg_mock.call_count == 2  # price + trending, once per cache window


# ── Verdict pure function ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fng", "btc", "eth", "expected"),
    [
        (68, 1.8, 2.3, "risk-on"),
        (45, 0.5, 0.2, "neutral"),
        (30, -1.0, -0.5, "risk-off"),
        (55, -3.0, 1.0, "risk-off"),  # BTC momentum alone flips to risk-off
        (None, 1.0, 1.0, ""),  # no F&G → no verdict
    ],
)
def test_market_verdict(fng, btc, eth, expected: str) -> None:
    assert mc.market_verdict(fng, btc, eth) == expected


# ── Injection point ────────────────────────────────────────────────────


async def test_build_messages_injects_market_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_build_messages`` (used by BOTH website chat and the Telegram bot)
    appends the market block AFTER the user turn — NOT into the system prompt.

    The block is volatile (re-renders every 60s with live prices/trending);
    it must stay past the byte-stable system-prompt prefix so llama.cpp
    ``cache_prompt`` KV reuse is not broken on every cache window."""
    from app.routers.chat import _messages as messages_mod

    monkeypatch.setattr(messages_mod, "build_market_context_block", AsyncMock(return_value="MARKET CONTEXT (test)"))
    msgs = await _build_messages("user-test", "hello", system_context=None, history=[])
    system = next(m for m in msgs if m["role"] == "system")
    user = msgs[-1]
    assert user["role"] == "user"
    assert "MARKET CONTEXT (test)" in user["content"]
    assert "MARKET CONTEXT" not in system["content"]


async def test_build_messages_without_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the builder returns "" (sources down) neither the system prompt
    nor the user turn gains an empty/invented market block."""
    from app.routers.chat import _messages as messages_mod

    monkeypatch.setattr(messages_mod, "build_market_context_block", AsyncMock(return_value=""))
    msgs = await _build_messages("user-test", "hello", system_context=None, history=[])
    assert all("MARKET CONTEXT" not in m["content"] for m in msgs)
