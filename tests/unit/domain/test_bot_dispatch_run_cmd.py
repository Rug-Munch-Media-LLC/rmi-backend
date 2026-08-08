"""Tests for RugMunchBot menu dispatch — ``_dispatch_run_cmd``.

Regression coverage for the 2026-08-07 signature fix
("fix(bot): _dispatch_run_cmd signature"): the callback handler started
passing the post-``run_`` payload as a third argument
(``_dispatch_run_cmd(update, ctx, data[4:])``) while the function only
accepted ``(update, ctx)`` — a TypeError crash on every menu button press.

The function must:
  - accept the new optional ``cmd_arg`` without breaking old callers
  - route ``trending`` / ``news`` / ``sentiment`` to their live fetchers
  - show usage cards for other commands (from ``query.data``)
  - degrade gracefully when ``query`` or ``query.data`` is missing
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.telegram.rugmunchbot.handlers import callback as cb


class _FakeQuery:
    def __init__(self, data: str | None) -> None:
        self.data = data
        self.edited: list[tuple[str, dict[str, Any]]] = []

    async def edit_message_text(self, text: str, **kwargs: Any) -> None:
        self.edited.append((text, kwargs))


class _FakeUpdate:
    def __init__(self, query: _FakeQuery | None) -> None:
        self.callback_query = query


def _make_ctx() -> Any:
    return object()  # ctx is unused by _dispatch_run_cmd


class TestDispatchRunCmdSignature:
    @pytest.mark.asyncio
    async def test_accepts_optional_cmd_arg(self) -> None:
        """The new signature must accept the third positional arg (regression)."""
        query = _FakeQuery(data="run_chart")
        await cb._dispatch_run_cmd(_FakeUpdate(query), _make_ctx(), "chart")
        assert len(query.edited) == 1
        # Chart usage card rendered
        assert "Price Chart" in query.edited[0][0]

    @pytest.mark.asyncio
    async def test_omitted_cmd_arg_derives_from_query_data(self) -> None:
        """Old-style callers (no cmd_arg) still work via query.data."""
        query = _FakeQuery(data="run_wallet")
        await cb._dispatch_run_cmd(_FakeUpdate(query), _make_ctx())
        assert len(query.edited) == 1
        assert "Wallet Forensics" in query.edited[0][0]

    @pytest.mark.asyncio
    async def test_trending_routes_to_live_fetcher(self) -> None:
        """trending buttons hit the live inline fetcher, not the usage card."""
        query = _FakeQuery(data="run_trending")
        with patch.object(cb, "_inline_trending", new=AsyncMock()) as mock_inline:
            await cb._dispatch_run_cmd(_FakeUpdate(query), _make_ctx(), "trending")
        mock_inline.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_news_routes_to_live_fetcher(self) -> None:
        query = _FakeQuery(data="run_news")
        with patch.object(cb, "_inline_news", new=AsyncMock()) as mock_inline:
            await cb._dispatch_run_cmd(_FakeUpdate(query), _make_ctx(), "news")
        mock_inline.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sentiment_routes_to_live_fetcher(self) -> None:
        query = _FakeQuery(data="run_sentiment")
        with patch.object(cb, "_inline_sentiment", new=AsyncMock()) as mock_inline:
            await cb._dispatch_run_cmd(_FakeUpdate(query), _make_ctx(), "sentiment")
        mock_inline.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_command_shows_fallback_usage(self) -> None:
        """Unmapped commands get a generic /cmd usage hint, not a crash."""
        query = _FakeQuery(data="run_mysterycmd")
        await cb._dispatch_run_cmd(_FakeUpdate(query), _make_ctx(), "mysterycmd")
        assert len(query.edited) == 1
        assert "<code>/mysterycmd</code>" in query.edited[0][0]


class TestDispatchRunCmdDegradation:
    @pytest.mark.asyncio
    async def test_missing_query_returns_silently(self) -> None:
        """update.callback_query None must not raise (button pressed elsewhere)."""
        # Would raise AttributeError pre-fix if it tried to read query.data.
        await cb._dispatch_run_cmd(_FakeUpdate(None), _make_ctx())

    @pytest.mark.asyncio
    async def test_none_query_data_returns_silently(self) -> None:
        """query.data None is tolerated (defensive ''.join semantics)."""
        query = _FakeQuery(data=None)
        await cb._dispatch_run_cmd(_FakeUpdate(query), _make_ctx(), "chart")
        # No card edit attempted — the ''.join path produces "" and the
        # unknown-command fallback fires only when data is a non-empty string;
        # either way the function must not raise.
