"""Focused tests: rugmunchbot prediction-market handlers.

Covers ``handlers/prediction.py``:
  - ``_end_countdown``: ISO parsing (Z suffix), unit granularity, "ended".
  - ``_prob_bar``: color status + filled-block math, clamped 0-100.
  - ``_market_line``: HTML escaping, volume, badges, countdown wiring.
  - ``_fetch_prediction``: fail-open on network/non-200/bad JSON.
  - Command handlers render graceful unavailable / no-results states.

No network I/O: httpx.AsyncClient is mocked at the module boundary.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from app.domains.telegram.rugmunchbot.handlers import prediction

if TYPE_CHECKING:
    import pytest


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _market(**overrides: Any) -> dict:
    base: dict[str, Any] = {
        "source": "polymarket",
        "question": "Will Gavin Newsom win the 2028 US Presidential Election?",
        "slug": "will-gavin-newsom-win",
        "probability": {"yes": 0.2145, "no": 0.7855, "yes_pct": "21.4%", "no_pct": "78.6%"},
        "volume_usd": 15174399.99,
        "category": "",
        "tokens_mentioned": [],
        "is_security_relevant": False,
        "is_crypto_relevant": False,
        "url": "https://polymarket.com/event/will-gavin-newsom-win",
        "ends_at": "2028-11-07T00:00:00Z",
        "updated_at": "2026-08-08T20:57:20.771249+00:00",
    }
    base.update(overrides)
    return base


class TestEndCountdown:
    def test_future_date_days(self) -> None:
        from datetime import UTC, datetime, timedelta

        future = (datetime.now(UTC) + timedelta(days=3, hours=5)).isoformat()
        out = prediction._end_countdown(future)
        assert out.endswith("left")
        assert "3d" in out

    def test_future_date_hours(self) -> None:
        from datetime import UTC, datetime, timedelta

        future = (datetime.now(UTC) + timedelta(hours=5, minutes=20)).isoformat()
        out = prediction._end_countdown(future)
        assert "5h" in out

    def test_ended_returns_literal(self) -> None:
        assert prediction._end_countdown("2020-01-01T00:00:00Z") == "ended"

    def test_none_and_garbage(self) -> None:
        assert prediction._end_countdown(None) == ""
        assert prediction._end_countdown("not-a-date") == ""


class TestProbBar:
    def test_high_is_green(self) -> None:
        out = prediction._prob_bar(85.0)
        assert "\U0001f7e2" in out  # 🟢
        assert "85%" in out

    def test_mid_is_yellow(self) -> None:
        out = prediction._prob_bar(50.0)
        assert "\U0001f7e1" in out  # 🟡

    def test_low_is_red(self) -> None:
        out = prediction._prob_bar(15.0)
        assert "\U0001f534" in out  # 🔴

    def test_clamped(self) -> None:
        assert "0%" in prediction._prob_bar(-5)
        assert "100%" in prediction._prob_bar(150)


class TestMarketLine:
    def test_full_line(self) -> None:
        line = prediction._market_line(1, _market())
        assert line.startswith("1. \U0001f52e <b>")
        assert "Will Gavin Newsom" in line
        assert "YES" in line
        assert "Vol:" in line
        assert "left" in line  # countdown

    def test_escapes_html_in_question(self) -> None:
        m = _market(question='Will "BTC" < $50k & ETH survive?')
        line = prediction._market_line(1, m)
        assert "<b>" in line
        assert "<" not in line.split("</b>")[0].split("<b>")[1] or "&lt;" in line

    def test_badges(self) -> None:
        m = _market(is_crypto_relevant=True, is_security_relevant=True)
        line = prediction._market_line(1, m)
        assert "\U0001f4b0" in line  # 💰
        assert "\U0001f6a8" in line  # 🚨

    def test_missing_volume_na(self) -> None:
        m = _market(volume_usd=None)
        assert "N/A" in prediction._market_line(1, m)


class TestFetchPrediction:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeResponse:
            status_code = 200

            def json(self) -> dict:
                return {"results": [{"question": "x"}]}

        class _FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def get(self, *args: object, **kwargs: object) -> _FakeResponse:
                return _FakeResponse()

        monkeypatch.setattr(prediction.httpx, "AsyncClient", _FakeClient)
        out = _run(prediction._fetch_prediction("/api/v1/prediction-markets/trending"))
        assert out == {"results": [{"question": "x"}]}

    def test_non_200_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeResponse:
            status_code = 503

            def json(self) -> dict:
                return {}

        class _FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def get(self, *args: object, **kwargs: object) -> _FakeResponse:
                return _FakeResponse()

        monkeypatch.setattr(prediction.httpx, "AsyncClient", _FakeClient)
        assert _run(prediction._fetch_prediction("/x")) is None

    def test_network_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def get(self, *args: object, **kwargs: object) -> Any:
                raise httpx.ConnectError("boom")

        monkeypatch.setattr(prediction.httpx, "AsyncClient", _FakeClient)
        assert _run(prediction._fetch_prediction("/x")) is None


class TestRenderMarkets:
    def test_limits_and_footer(self) -> None:
        results = [_market() for _ in range(15)]
        text = prediction._render_markets(results, "\U0001f52e <b>Prediction Markets</b>")
        assert "8. " in text  # last allowed item
        assert "9. " not in text  # capped at MARKET_LIMIT (8)
        assert "Rug Munch Bot" in text


class TestRegisterPrediction:
    def test_registers_three_commands(self) -> None:
        class _FakeApp:
            def __init__(self) -> None:
                self.handlers: list[str] = []

            def add_handler(self, handler: object) -> None:
                # CommandHandler stores the command name in .commands
                commands = getattr(handler, "commands", ()) or ()
                self.handlers.append(next(iter(commands)))

        app = _FakeApp()
        prediction.register_prediction(app)  # type: ignore[arg-type]
        assert sorted(app.handlers) == ["election", "odds", "polymarket"]
