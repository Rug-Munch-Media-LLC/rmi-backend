"""Error-path tests for the Daily Market Summary job (fail-open pipeline).

Complements ``tests/domains/test_daily_market_summary.py`` with the hostile
failure modes: every data fetcher raising at once, fetchers returning the
wrong type, ``None``-filled context serializing to "N/A", a garbage
``generated_at``, empty LLM output after cleanup, and a failing Telegram
send (which must skip the feed publish and return 0 — never crash the
JobQueue tick). No live network — every boundary is faked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock, patch

from app.domains.telegram.rugmunchbot.channels import (
    daily_market_summary as dms,
    scheduler as channels_scheduler,
)

if TYPE_CHECKING:
    import pytest


def _ctx() -> tuple[Mock, AsyncMock]:
    bot = AsyncMock()
    ctx = Mock()
    ctx.bot = bot
    return ctx, bot


# ── gather_summary_data: all fetchers down / wrong types ────────────────────


class TestGatherSummaryDataErrors:
    async def test_all_fetchers_raising_returns_empty_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every source down → empty blocks + no sources, never raises."""

        async def _boom(trends: Any = None) -> Any:
            raise RuntimeError("source down")

        for fn in (
            "_fetch_major_prices",
            "_fetch_market_condition",
            "_fetch_trending",
            "_fetch_movers",
            "_fetch_news",
            "_fetch_narratives",
            "_fetch_kol_signals",
        ):
            monkeypatch.setattr(dms, fn, _boom)
        monkeypatch.setattr(dms, "DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED", True)

        data = await dms.gather_summary_data()
        assert data["prices"] == {}
        assert data["condition"] == {}
        assert data["trending"] == []
        assert data["movers"] == {}
        assert data["news"] == []
        assert data["narratives"] == []
        assert data["kol_signals"] == []
        assert data["sources"] == []

    async def test_wrong_type_fetcher_returns_cleaned_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A fetcher returning a non-container (e.g. a bare string) is
        cleaned to an empty value instead of crashing the gather."""

        async def _wrong_type() -> Any:
            return "not-a-container"

        for fn in ("_fetch_major_prices", "_fetch_market_condition", "_fetch_trending", "_fetch_movers", "_fetch_news"):
            monkeypatch.setattr(dms, fn, _wrong_type)
        monkeypatch.setattr(dms, "DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED", False)

        data = await dms.gather_summary_data()
        assert data["prices"] == {}
        assert data["condition"] == {}
        assert data["trending"] == []
        assert data["movers"] == {}
        assert data["news"] == []


# ── _serialize_context: None-filled data renders "N/A" ──────────────────────


class TestSerializeContextErrors:
    def test_none_filled_context_renders_na(self) -> None:
        """Provider degradation (None / empty / unparseable values) renders
        as 'N/A' — never crashes, never fabricates a number."""
        ctx = {
            "prices": {
                "btc_price": None,
                "btc_change_24h": None,
                "eth_price": "garbage",
                "eth_change_24h": None,
                "sol_price": 0,
                "sol_change_24h": None,
            },
            "condition": {
                "signals": {
                    "fear_greed": None,
                    "global_market_cap_usd": None,
                    "global_market_cap_change_24h": None,
                },
                "verdict": None,
                "reasons": None,
                "confidence": None,
            },
            "trending": [{"symbol": "TST", "change_24h": None, "volume_24h": None, "market_cap": None, "chain": None}],
            "movers": {"gainers": [], "losers": []},
            "news": [{"content": None}, {"title": "Real headline"}],
        }
        out = dms._serialize_context(ctx)
        assert "N/A" in out
        assert "Real headline" in out  # usable news still surfaces

    def test_empty_context_serializes_empty(self) -> None:
        assert dms._serialize_context({}) == ""

    def test_cleanup_html_none_and_blank(self) -> None:
        assert dms.cleanup_html(None) == ""
        assert dms.cleanup_html("   \n ") == ""

    def test_build_messages_garbage_generated_at_falls_back(self) -> None:
        """A malformed generated_at renders '—' — the date line never crashes."""
        messages = dms.build_messages({"generated_at": "not-a-date", "prices": {}})
        assert "Daily Market Brief for —" in messages[1]["content"]


# ── post_daily_market_summary: send failure skips feed, returns 0 ───────────


class TestPostJobErrors:
    async def test_job_returns_zero_when_send_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A Telegram send failure returns 0 AND skips the feed publish —
        the job degrades without raising."""
        ctx, _ = _ctx()
        monkeypatch.setattr(dms, "gather_summary_data", AsyncMock(return_value={"sources": ["prices"], "prices": {}}))
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100main")
        send_mock = AsyncMock(return_value=False)
        monkeypatch.setattr(channels_scheduler, "_safe_send", send_mock)
        feed_mock = AsyncMock(return_value={"published": True})
        monkeypatch.setattr(dms, "publish_to_feed", feed_mock)
        with patch("app.llm_config.chat_with_fallback", new=AsyncMock(return_value="<b>report</b>")):
            result = await dms.post_daily_market_summary(ctx)
        assert result == 0
        send_mock.assert_awaited_once()
        feed_mock.assert_not_awaited()

    async def test_job_returns_zero_when_llm_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """chat_with_fallback returning None (provider failure shape) is
        treated like empty output — skip, no post."""
        ctx, _ = _ctx()
        monkeypatch.setattr(dms, "gather_summary_data", AsyncMock(return_value={"sources": ["prices"], "prices": {}}))
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100main")
        send_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(channels_scheduler, "_safe_send", send_mock)
        with patch("app.llm_config.chat_with_fallback", new=AsyncMock(return_value=None)):
            result = await dms.post_daily_market_summary(ctx)
        assert result == 0
        send_mock.assert_not_awaited()

    async def test_job_returns_zero_when_cleaned_output_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLM output that cleans down to nothing (whitespace/markdown-only)
        is skipped — never posts garbage."""
        ctx, _ = _ctx()
        monkeypatch.setattr(dms, "gather_summary_data", AsyncMock(return_value={"sources": ["prices"], "prices": {}}))
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100main")
        send_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(channels_scheduler, "_safe_send", send_mock)
        with patch("app.llm_config.chat_with_fallback", new=AsyncMock(return_value="   ")):
            result = await dms.post_daily_market_summary(ctx)
        assert result == 0
        send_mock.assert_not_awaited()
