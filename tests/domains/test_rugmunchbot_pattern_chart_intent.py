"""Routing tests for the chart + pattern_recall tools.

Owned by the charting/pattern-memory work: proves the newly-wired phrases
("chart for X", "price chart", "have we seen this pattern before", "similar
patterns") route to the right tools, that the per-tool kwargs map passes the
extracted address / symbol, that exclusions suppress exactly the new tools,
and that ``run_for_message`` delivers the kwargs to mocked handlers.

The chart phrase must NOT steal existing intents: "chart patterns?" stays
technical_analysis, "$BONK chart" keeps price_lookup, and pattern phrases are
deliberately specific so prose never fires them.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.telegram.rugmunchbot.tools import registry, run_for_message
from app.domains.telegram.rugmunchbot.tools.intent import (
    detect_exclusions,
    route_tools,
    tool_kwargs,
)
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"


@pytest.fixture(autouse=True)
def _clear_registry_cache() -> None:
    """Isolate run_for_message tests from the shared TTL cache."""
    registry.clear_cache()
    yield
    registry.clear_cache()


# ══════════════════════════════════════════════════════════════════════════
# chart — "chart for X" / "price chart" / "$SYMBOL chart"
# ══════════════════════════════════════════════════════════════════════════


class TestChartRouting:
    def test_chart_phrases_route_to_chart(self) -> None:
        for question in (
            "chart for $BONK",
            "chart for $BONK please",
            "price chart",
            "price chart of $PEPE",
            "show me a chart",
            "show me the chart for $WIF",
            "draw a chart of $SOL",
            "candlestick chart of eth",
            "candle chart for $BONK",
            "ascii chart",
            "sparkline for $BONK",
            "$BONK chart",
            "chart $BONK",
        ):
            names = route_tools(question)
            assert "chart" in names, f"failed to route chart: {question}"

    def test_chart_question_keeps_price_lookup(self) -> None:
        # "$BONK chart" already routed price_lookup (the chart word in the
        # price intent) — the new chart tool must be ADDITIVE, not replace it.
        names = route_tools("$BONK chart")
        assert "price_lookup" in names
        assert "chart" in names

    def test_chart_not_routed_without_chart_phrase(self) -> None:
        assert "chart" not in route_tools("market overview")
        assert "chart" not in route_tools("what's the price of $BONK")
        assert "chart" not in route_tools("compare $BONK vs $WIF")

    def test_chart_patterns_phrase_stays_ta(self) -> None:
        # "chart patterns" belongs to technical_analysis; the chart tool is
        # an optional ADD, never a replacement.
        names = route_tools("what chart patterns do you see in $BONK?")
        assert "technical_analysis" in names

    def test_chart_kwargs_carry_symbol(self) -> None:
        kw = tool_kwargs("chart for $BONK", ["chart"])
        assert kw["chart"]["symbol"] == "BONK"
        assert kw["chart"]["address"] == ""

    def test_chart_kwargs_carry_address_and_chain(self) -> None:
        kw = tool_kwargs(f"chart of {EVM} on base", ["chart"])
        assert kw["chart"]["address"] == EVM
        assert kw["chart"]["chain"] == "base"

    def test_chart_exclusion_suppresses_chart_only(self) -> None:
        assert detect_exclusions("don't show me the chart") == {"chart"}
        names = route_tools("don't show me the chart")
        assert "chart" not in names


# ══════════════════════════════════════════════════════════════════════════
# pattern_recall — "have we seen this pattern before" / "similar patterns"
# ══════════════════════════════════════════════════════════════════════════


class TestPatternRecallRouting:
    def test_pattern_phrases_route_to_pattern_recall(self) -> None:
        for question in (
            "have we seen this pattern before?",
            "have we seen this before?",
            "seen this pattern before",
            "seen this before",
            "similar patterns",
            "are there similar patterns on other tokens?",
            "pattern memory",
            "have we seen a pump-and-dump before",
            "have we seen this honeypot before",
        ):
            names = route_tools(question)
            assert "pattern_recall" in names, f"failed to route pattern_recall: {question}"

    def test_pattern_recall_not_routed_for_generic_questions(self) -> None:
        # Prose that merely mentions "pattern" must not fire the memory tool.
        assert "pattern_recall" not in route_tools("what is a honeypot?")
        assert "pattern_recall" not in route_tools("what pattern is this?")
        assert "pattern_recall" not in route_tools("candlestick patterns in $BONK")
        assert route_tools("what is a honeypot?") == []

    def test_pattern_question_with_symbol_routes_recall_plus_price(self) -> None:
        names = route_tools("have we seen this pattern before on $BONK?")
        assert "pattern_recall" in names
        # A $SYMBOL mention keeps the live price intent as well.
        assert "price_lookup" in names

    def test_pattern_kwargs_carry_symbol(self) -> None:
        kw = tool_kwargs("have we seen this pattern before on $BONK?", ["pattern_recall"])
        assert kw["pattern_recall"]["symbol"] == "BONK"

    def test_pattern_kwargs_carry_address(self) -> None:
        kw = tool_kwargs(f"similar patterns for {EVM}?", ["pattern_recall"])
        assert kw["pattern_recall"]["address"] == EVM

    def test_pattern_exclusion_suppresses_recall(self) -> None:
        assert detect_exclusions("skip the pattern recall") == {"pattern"}
        names = route_tools("skip the pattern recall")
        assert "pattern_recall" not in names


# ══════════════════════════════════════════════════════════════════════════
# run_for_message end-to-end with mocked handlers — kwargs reach the tools
# ══════════════════════════════════════════════════════════════════════════


class TestRunForMessageWiring:
    async def test_chart_question_delivers_symbol_to_chart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        received: dict[str, dict[str, Any]] = {}

        async def fake_chart(**kwargs: Any) -> ToolResult:
            received["chart"] = kwargs
            return ToolResult(tool="chart", ok=True, data={"chart_text": "mock"}, symbols=[kwargs.get("symbol", "")])

        async def fake_price(**kwargs: Any) -> ToolResult:
            received["price_lookup"] = kwargs
            return ToolResult(tool="price_lookup", ok=True, data={})

        handlers = dict(registry._handlers)
        handlers.update({"chart": fake_chart, "price_lookup": fake_price})
        monkeypatch.setattr(registry, "_handlers", handlers)

        results = await run_for_message("chart for $BONK")
        assert "chart" in results
        assert received["chart"]["symbol"] == "BONK"
        assert received["chart"]["address"] == ""

    async def test_pattern_question_delivers_address_to_recall(self, monkeypatch: pytest.MonkeyPatch) -> None:
        received: dict[str, dict[str, Any]] = {}

        async def fake_recall(**kwargs: Any) -> ToolResult:
            received["pattern_recall"] = kwargs
            return ToolResult(tool="pattern_recall", ok=True, data={"matches": []})

        handlers = dict(registry._handlers)
        handlers["pattern_recall"] = fake_recall
        monkeypatch.setattr(registry, "_handlers", handlers)

        results = await run_for_message(f"have we seen this pattern before on {EVM}?")
        assert "pattern_recall" in results
        assert received["pattern_recall"]["address"] == EVM
