"""Pure unit tests for the prediction-market data module + chat intent.

Covers ``app/domains/markets/prediction_markets.py``:
  - ``fetch_trending_markets``: normalizes a real Gamma-shaped response
    (yes/no prices from ``outcomePrices``), fail-open → [].
  - ``rank_interesting``: sorts by interest score, tags categories, drops
    closed/illiquid, applies ``min_volume``.
  - ``format_market_list``: renders the numbered 📊 list.

And the chat intent wiring in ``app/domains/chat_tools.py``:
  - ``_detect_tool_intent`` returns "prediction" for Polymarket-style asks
    and None for unrelated questions ("what is a honeypot").
  - ``_exec_prediction`` renders from a mocked fetch; None on empty.

No network, DB, or LLM calls — the Gamma client is mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from app.domains import chat_tools
from app.domains.chat_tools import _detect_tool_intent, _exec_prediction
from app.domains.markets import prediction_markets
from app.domains.markets.prediction_markets import (
    fetch_trending_markets,
    format_market_list,
    normalize_market,
    rank_interesting,
)

# ── Fixtures ───────────────────────────────────────────────────────────


def _gamma_market(**overrides: object) -> dict:
    """A realistic Gamma API market object (shape verified live 2026-08-13)."""
    market: dict = {
        "id": "562006",
        "question": "Will Eric Trump win the 2028 Republican presidential nomination?",
        "conditionId": "0xca68152902c3581ab42feb04921b8b1cd3c4e80d06f255ae077259fca0bba15c",
        "slug": "will-eric-trump-win-the-2028-republican-presidential-nomination",
        "endDate": "2028-11-07T00:00:00Z",
        "liquidity": "2100895.17482",
        "volume": "9999186.869245995",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.0035", "0.9965"]',
        "category": None,
        "active": True,
        "closed": False,
        "events": [
            {
                "category": "Politics",
                "tags": [{"label": "Politics", "id": "politics"}],
            }
        ],
    }
    market.update(overrides)
    return market


class _FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self._payload


class _FakeClient:
    """Minimal async client double: records params, returns canned payload."""

    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self._status_code = status_code
        self.last_params: dict | None = None

    async def get(self, url: str, params: dict | None = None, **kwargs: object) -> _FakeResponse:
        self.last_url = url
        self.last_params = params
        return _FakeResponse(self._payload, self._status_code)

    async def aclose(self) -> None:
        pass


# ── 1. fetch_trending_markets ─────────────────────────────────────────


async def test_fetch_normalizes_real_gamma_shape() -> None:
    client = _FakeClient([_gamma_market()])
    markets = await fetch_trending_markets(limit=15, client=client)  # type: ignore[arg-type]
    assert len(markets) == 1
    m = markets[0]
    assert m["question"] == "Will Eric Trump win the 2028 Republican presidential nomination?"
    assert m["slug"] == "will-eric-trump-win-the-2028-republican-presidential-nomination"
    assert m["url"] == ("https://polymarket.com/event/will-eric-trump-win-the-2028-republican-presidential-nomination")
    assert m["end_date"] == "2028-11-07T00:00:00Z"
    assert m["volume"] == pytest.approx(9999186.869245995)
    assert m["liquidity"] == pytest.approx(2100895.17482)
    # Yes price parsed from outcomePrices[0] (JSON string), not outcomes labels.
    assert m["yes_price"] == pytest.approx(0.0035)
    # Category falls back to the event's category when market category is null.
    assert m["category"] == "Politics"


async def test_fetch_uses_volume_ordering_params() -> None:
    client = _FakeClient([])
    await fetch_trending_markets(limit=15, client=client)  # type: ignore[arg-type]
    assert client.last_params == {
        "active": "true",
        "closed": "false",
        "limit": 15,
        "order": "volume",
        "ascending": "false",
    }


async def test_fetch_fails_open_on_http_error() -> None:
    client = _FakeClient({}, status_code=500)
    assert await fetch_trending_markets(client=client) == []  # type: ignore[arg-type]


async def test_fetch_fails_open_on_network_error() -> None:
    client = SimpleNamespace(get=mock.AsyncMock(side_effect=RuntimeError("boom")))
    assert await fetch_trending_markets(client=client) == []  # type: ignore[arg-type]


async def test_fetch_fails_open_on_non_list_payload() -> None:
    client = _FakeClient({"not": "a list"})
    assert await fetch_trending_markets(client=client) == []  # type: ignore[arg-type]


async def test_fetch_skips_invalid_markets() -> None:
    client = _FakeClient([_gamma_market(), {"no": "slug"}, None, _gamma_market(slug="")])
    markets = await fetch_trending_markets(client=client)  # type: ignore[arg-type]
    assert len(markets) == 1


def test_normalize_market_yes_price_none_when_outcome_prices_missing() -> None:
    m = normalize_market(_gamma_market(outcomePrices=None, outcomes='["Yes", "No"]'))
    assert m is not None
    assert m["yes_price"] is None


# ── 2. rank_interesting ───────────────────────────────────────────────


def _normalized(**overrides: object) -> dict:
    m = normalize_market(_gamma_market())
    assert m is not None
    m.update(overrides)
    return m


def test_rank_sorts_by_interest_and_tags_categories() -> None:
    markets = [
        _normalized(
            question="Will Bitcoin hit $100k by 2027?",
            volume=9000000,
            liquidity=2000000,
            end_date="2026-12-31T00:00:00Z",
        ),
        _normalized(
            question="Who will win the 2028 presidential election?",
            volume=5000000,
            liquidity=1000000,
            end_date="2028-11-07T00:00:00Z",
        ),
        _normalized(
            question="Will the SEC indict the insider trading ring?",
            volume=1000000,
            liquidity=500000,
            end_date="2026-09-01T00:00:00Z",
        ),
    ]
    ranked = rank_interesting(markets)
    assert [m["question"] for m in ranked] == [m["question"] for m in markets]  # volume desc
    assert "crypto" in ranked[0]["tags"]
    assert "politics" in ranked[1]["tags"]
    assert "insider" in ranked[2]["tags"]
    assert all(m["interest"] > 0 for m in ranked)
    # Input not mutated.
    assert "tags" not in markets[0]
    assert "interest" not in markets[0]


def test_rank_drops_illiquid_and_closed_markets() -> None:
    markets = [
        _normalized(volume=100, liquidity=0),  # zero liquidity → illiquid
        _normalized(volume=0, liquidity=1000),  # zero volume → illiquid
        _normalized(volume=100, liquidity=100, yes_price=None),  # ok
    ]
    ranked = rank_interesting(markets)
    assert len(ranked) == 1
    assert ranked[0]["volume"] == 100


def test_rank_respects_min_volume() -> None:
    markets = [
        _normalized(volume=5000000, liquidity=1000000),
        _normalized(volume=50000, liquidity=100000),
    ]
    ranked = rank_interesting(markets, min_volume=1_000_000)
    assert len(ranked) == 1
    assert ranked[0]["volume"] == 5000000


def test_rank_recency_boost_end_date_sooner() -> None:
    # 2026-08-20 is ~1 week out (high recency); 2026-12-31 is >30 days (none).
    soon = _normalized(volume=100000, liquidity=100000, end_date="2026-08-20T00:00:00Z")
    far = _normalized(volume=100000, liquidity=100000, end_date="2026-12-31T00:00:00Z")
    [s, f] = rank_interesting([far, soon])
    assert s["interest"] > f["interest"]


# ── 3. format_market_list ─────────────────────────────────────────────


def test_format_market_list_renders_numbered_lines() -> None:
    markets = [
        _normalized(question="Will Bitcoin hit $100k?", volume=9000000, yes_price=0.42, category="Crypto"),
        _normalized(question="Who wins the election?", volume=5000000, yes_price=0.65, category=""),
    ]
    text = format_market_list(markets, top_n=8)
    assert "1. 📊 Will Bitcoin hit $100k? — YES 42% · vol $9,000,000 · Crypto" in text
    assert "2. 📊 Who wins the election? — YES 65% · vol $5,000,000" in text
    assert "https://polymarket.com/event/" in text
    assert text.index("1.") < text.index("2.")


def test_format_market_list_caps_top_n_and_uses_tags_fallback() -> None:
    markets = [_normalized(question=f"Q{i}", volume=100000, liquidity=100000, yes_price=0.5) for i in range(10)]
    for m in markets:
        m["category"] = ""
        m["tags"] = ["politics"]
    text = format_market_list(markets, top_n=3)
    assert len(text.splitlines()) == 3


def test_format_market_list_empty() -> None:
    assert format_market_list([]) == ""


def test_format_market_list_yes_price_none() -> None:
    m = _normalized(question="Mystery market", volume=1000, liquidity=1000, yes_price=None)
    text = format_market_list([m])
    assert "YES ?" in text


# ── 4. Chat intent: prediction ────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("interesting prediction markets", "prediction"),
        ("show me prediction markets", "prediction"),
        ("polymarket insider trading markets", "prediction"),
        ("what are the odds on btc", "prediction"),
        ("will bitcoin hit 100k happen", "prediction"),
        ("will trump win in 2028 happen", "prediction"),
        ("bet on solana", "prediction"),
        ("crypto market odds", "prediction"),
        ("what is a honeypot", None),
        ("how's the market", "market"),
        ("price of $PEPE", "price"),
        ("bet on solana price", "price"),  # explicit price phrase wins
    ],
)
def test_detect_tool_intent_prediction(message: str, expected: str | None) -> None:
    assert _detect_tool_intent(message) == expected


# ── 5. _exec_prediction ───────────────────────────────────────────────


async def test_exec_prediction_renders_from_mocked_fetch() -> None:
    markets = [_normalized(question="Will BTC hit 100k?", volume=9000000, yes_price=0.42)]
    with (
        mock.patch.object(prediction_markets, "fetch_trending_markets", new=mock.AsyncMock(return_value=markets)),
        mock.patch.object(
            prediction_markets, "rank_interesting", side_effect=lambda m, **kw: rank_interesting(m, **kw)
        ),
    ):
        text = await _exec_prediction("interesting prediction markets")
    assert text is not None
    assert "📊 Will BTC hit 100k?" in text
    assert "YES 42%" in text


async def test_exec_prediction_returns_none_on_empty() -> None:
    with (
        mock.patch.object(prediction_markets, "fetch_trending_markets", new=mock.AsyncMock(return_value=[])),
    ):
        assert await _exec_prediction("prediction markets") is None


async def test_exec_prediction_fails_open_on_error() -> None:
    with (
        mock.patch.object(
            prediction_markets, "fetch_trending_markets", new=mock.AsyncMock(side_effect=RuntimeError("boom"))
        ),
    ):
        assert await _exec_prediction("prediction markets") is None


def test_prediction_wired_into_executors() -> None:
    assert "prediction" in chat_tools._EXECUTORS
    assert chat_tools._EXECUTORS["prediction"] is _exec_prediction
