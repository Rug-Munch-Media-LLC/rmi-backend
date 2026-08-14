"""Routing tests for the RugMunchBot intent router.

Owned by the tools/intent.py router work: proves every newly-wired phrase
routes to the right tool, that the per-tool kwargs map passes the extracted
address / symbol / chain / kind correctly, and that ``run_for_message``
delivers those kwargs to the tools (mocked handlers — no live network).

Covers the tools that were registered but unreachable from a user message:
scanner_intel, token_meta, trend_by_chain, prediction_markets, birdeye_market,
market_overview.

Zero-fabrication contract: the router never invents symbols/addresses — it
only extracts what the user actually typed (checked in TestToolKwargs).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.telegram.rugmunchbot.tools import registry, run_for_message
from app.domains.telegram.rugmunchbot.tools.intent import (
    compare_pairs,
    detect_comparison,
    detect_exclusions,
    detect_negations,
    extract_chain,
    route_tools,
    symbols_in,
    tool_kwargs,
)
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@pytest.fixture(autouse=True)
def _clear_registry_cache() -> None:
    """Isolate run_for_message tests from the shared TTL cache."""
    registry.clear_cache()
    yield
    registry.clear_cache()


# ══════════════════════════════════════════════════════════════════════════
# scanner_intel / token_meta — scan / honeypot / rug check
# ══════════════════════════════════════════════════════════════════════════


class TestScannerRouting:
    def test_scan_phrases_route_to_scanner_intel_and_token_meta(self) -> None:
        for question in (
            f"scan {EVM}",
            f"scan {EVM} for risk",
            f"is {EVM} safe?",
            f"is {EVM} a honeypot?",
            f"honeypot check on {EVM}",
            f"rug check {EVM}",
            f"rug check {SOL}",
            f"do a security audit of {EVM}",
        ):
            names = route_tools(question)
            assert "scanner_intel" in names, f"failed to route scanner_intel: {question}"
            assert "token_meta" in names, f"failed to route token_meta: {question}"
            assert "security_scan" in names, f"failed to route security_scan: {question}"

    def test_security_word_without_address_is_not_scanner_routed(self) -> None:
        # scanner_intel / token_meta / security_scan all require an on-chain
        # address — never route a tool that cannot answer.
        assert "scanner_intel" not in route_tools("what is a honeypot?")
        assert "scanner_intel" not in route_tools("how do rug pulls work?")
        assert route_tools("what is a honeypot?") == []

    def test_symbol_only_security_question_stays_price(self) -> None:
        # "$BONK honeypot?" has a ticker but no address — only the price
        # tool can answer (scanner needs an address).
        names = route_tools("is $BONK a honeypot?")
        assert "scanner_intel" not in names
        assert "price_lookup" in names


# ══════════════════════════════════════════════════════════════════════════
# trend_by_chain — trending on {chain}
# ══════════════════════════════════════════════════════════════════════════


class TestTrendByChainRouting:
    def test_chain_trending_phrases_route_to_trend_by_chain(self) -> None:
        for question in (
            "what's hot on solana?",
            "what's hot on base",
            "what's pumping on ethereum",
            "trending on arbitrum",
            "bsc trending right now",
            "what's hot on op",
            "top gainers on solana",
        ):
            names = route_tools(question)
            assert "trend_by_chain" in names, f"failed to route trend_by_chain: {question}"

    def test_trending_without_chain_does_not_route_trend_by_chain(self) -> None:
        names = route_tools("what's trending?")
        assert "trending" in names
        assert "trend_by_chain" not in names

    def test_chain_extraction(self) -> None:
        assert extract_chain("what's hot on solana?") == "solana"
        assert extract_chain("base trending") == "base"
        assert extract_chain("trending on eth") == "ethereum"
        assert extract_chain("what's hot?") == ""
        # "op" must not match inside "top" / "polymarket".
        assert extract_chain("what's the top token?") == ""
        assert extract_chain("polymarket odds") == ""


# ══════════════════════════════════════════════════════════════════════════
# prediction_markets — prediction markets / polymarket / odds
# ══════════════════════════════════════════════════════════════════════════


class TestPredictionMarketsRouting:
    def test_prediction_phrases_route_to_prediction_markets(self) -> None:
        for question in (
            "what are the prediction markets saying?",
            "show me polymarket",
            "what are the odds on BTC hitting 100k?",
            "odds for the election",
            "kalshi markets",
            "who will win the next election",
            "chances of recession this year",
        ):
            names = route_tools(question)
            assert "prediction_markets" in names, f"failed to route prediction_markets: {question}"


# ══════════════════════════════════════════════════════════════════════════
# market_overview — market overview / how's the market / fear and greed / btc price
# ══════════════════════════════════════════════════════════════════════════


class TestMarketOverviewRouting:
    def test_market_overview_phrases_route_to_market_overview(self) -> None:
        for question in (
            "market overview",
            "how's the market",
            "how is the market today",
            "fear and greed",
            "fear and greed index",
            "crypto market",
            "market status",
        ):
            names = route_tools(question)
            assert "market_overview" in names, f"failed to route market_overview: {question}"

    def test_major_price_questions_route_to_market_overview_plus_price(self) -> None:
        for question in ("btc price", "bitcoin price", "eth price", "price of btc", "sol price"):
            names = route_tools(question)
            assert "market_overview" in names, f"failed to route market_overview: {question}"
            assert "price_lookup" in names, f"failed to route price_lookup: {question}"

    def test_altcoin_price_not_routed_to_market_overview(self) -> None:
        # market_overview only answers majors — a $BONK price question must
        # not be routed to a tool that cannot answer it.
        assert "market_overview" not in route_tools("price of $BONK")
        assert "price_lookup" in route_tools("price of $BONK")


# ══════════════════════════════════════════════════════════════════════════
# birdeye_market / smart money — smart money / who's buying / top traders
# ══════════════════════════════════════════════════════════════════════════


class TestSmartMoneyRouting:
    def test_smart_money_phrases_route_to_birdeye_and_onchain(self) -> None:
        for question in (
            "smart money",
            "smart-money flows",
            "who's buying?",
            "who is buying today",
            "top traders",
            "show me the whales",
            "what are early buyers accumulating",
        ):
            names = route_tools(question)
            assert "birdeye_market" in names, f"failed to route birdeye_market: {question}"
            assert "smart_money" in names, f"failed to route smart_money: {question}"

    def test_top_traders_requires_an_address(self) -> None:
        # birdeye top_traders / onchain top_traders need a token address;
        # without one we still route the chain-level smart-money feed.
        assert "top_traders" not in route_tools("top traders")
        names = route_tools(f"who's buying {EVM}?")
        assert "top_traders" in names
        assert "birdeye_market" in names


# ══════════════════════════════════════════════════════════════════════════
# tool_kwargs — the per-tool kwargs map passes extracted values, never fabricates
# ══════════════════════════════════════════════════════════════════════════


class TestToolKwargs:
    def test_scanner_tools_get_address_and_chain(self) -> None:
        names = route_tools(f"scan {EVM} on base")
        kw = tool_kwargs(f"scan {EVM} on base", names)
        assert kw["scanner_intel"]["address"] == EVM
        assert kw["scanner_intel"]["chain"] == "base"
        assert kw["token_meta"]["address"] == EVM
        assert kw["security_scan"]["address"] == EVM

    def test_trend_by_chain_gets_chain(self) -> None:
        kw = tool_kwargs("what's hot on solana?", ["trend_by_chain"])
        assert kw["trend_by_chain"]["chain"] == "solana"
        # No chain named -> the tool's cross-chain "all" default.
        kw = tool_kwargs("what's trending?", ["trend_by_chain"])
        assert kw["trend_by_chain"]["chain"] == "all"

    def test_birdeye_kind_depends_on_address_presence(self) -> None:
        kw = tool_kwargs("smart money", ["birdeye_market"])
        assert kw["birdeye_market"]["kind"] == "smart_money"
        kw = tool_kwargs(f"who's buying {EVM}?", ["birdeye_market"])
        assert kw["birdeye_market"]["kind"] == "top_traders"
        assert kw["birdeye_market"]["address"] == EVM

    def test_birdeye_and_smart_money_get_chain(self) -> None:
        kw = tool_kwargs("smart money on base", ["smart_money", "birdeye_market"])
        assert kw["smart_money"]["chain"] == "base"
        assert kw["birdeye_market"]["chain"] == "base"

    def test_price_lookup_symbol_fills_when_no_address(self) -> None:
        kw = tool_kwargs("price of $BONK", ["price_lookup"])
        assert kw["price_lookup"]["symbol"] == "BONK"
        assert kw["price_lookup"]["address"] == ""

    def test_address_takes_precedence_over_symbol(self) -> None:
        kw = tool_kwargs(f"price of {EVM} vs $BONK", ["price_lookup"])
        assert kw["price_lookup"]["address"] == EVM
        assert "symbol" not in kw["price_lookup"]

    def test_router_never_invents_symbols_or_addresses(self) -> None:
        # A symbol-less, address-less question routes no kwargs-bearing tools.
        kw = tool_kwargs("market overview", route_tools("market overview"))
        assert kw == {"market_overview": {}}
        # $SYMBOL is passed through exactly as typed (upper-cased), never
        # synthesized from prose.
        kw = tool_kwargs("is $BONK a honeypot?", route_tools("is $BONK a honeypot?"))
        assert kw["price_lookup"]["symbol"] == "BONK"


# ══════════════════════════════════════════════════════════════════════════
# market_condition — how's the market / sentiment / bullish-bearish / risk
# ══════════════════════════════════════════════════════════════════════════


class TestMarketConditionRouting:
    def test_market_condition_phrases_route_to_market_condition(self) -> None:
        for question in (
            "how's the market",
            "how is the market right now",
            "market sentiment",
            "what's the sentiment?",
            "what's the sentiment right now",
            "is the market bullish or bearish right now?",
            "is the market bullish or bearish?",
            "bullish or bearish",
            "risk on",
            "risk off",
            "risk-on/risk-off",
            "is now a good time to enter?",
            "is it a good time to enter",
            "good time to buy",
            "should I buy",
            "worth buying",
        ):
            names = route_tools(question)
            assert "market_condition" in names, f"failed to route market_condition: {question}"

    def test_hows_the_market_routes_overview_plus_condition(self) -> None:
        names = route_tools("how's the market")
        assert "market_overview" in names
        assert "market_condition" in names

    def test_plain_market_overview_stays_overview_only(self) -> None:
        # Exact-match contract: "market overview" must keep routing ONLY
        # market_overview (the tool_kwargs exact-equality test depends on it).
        assert route_tools("market overview") == ["market_overview"]
        assert "market_condition" not in route_tools("market overview")

    def test_bare_bullish_is_not_market_condition(self) -> None:
        # A bare "bullish" without market context is a TOKEN question —
        # do not misroute it to the macro read.
        assert "market_condition" not in route_tools("is $BONK bullish?")
        assert "price_lookup" in route_tools("is $BONK bullish?")

    def test_market_condition_takes_no_kwargs(self) -> None:
        kw = tool_kwargs("what's the sentiment?", route_tools("what's the sentiment?"))
        assert kw == {"market_condition": {}}


# ══════════════════════════════════════════════════════════════════════════
# buy-advice — should I buy X / is now a good time (macro read + token price)
# ══════════════════════════════════════════════════════════════════════════


class TestBuyAdviceRouting:
    def test_buy_advice_routes_market_condition_plus_price(self) -> None:
        names = route_tools("should I buy $BONK?")
        assert "market_condition" in names
        assert "price_lookup" in names
        kw = tool_kwargs("should I buy $BONK?", names)
        assert kw["price_lookup"]["symbol"] == "BONK"

    def test_good_time_to_enter_routes_market_condition(self) -> None:
        assert "market_condition" in route_tools("is now a good time to enter?")

    def test_buy_advice_does_not_route_smart_money(self) -> None:
        # "should I buy" is advice, not a "who's buying" feed question.
        names = route_tools("should I buy $BONK?")
        assert "smart_money" not in names
        assert "birdeye_market" not in names


# ══════════════════════════════════════════════════════════════════════════
# comparison — compare X vs Y (both $SYMBOLs extracted, price data routed)
# ══════════════════════════════════════════════════════════════════════════


class TestComparisonRouting:
    def test_compare_phrases_route_to_price_lookup(self) -> None:
        for question in (
            "compare $BONK vs $WIF",
            "compare $BONK versus $WIF",
            "which is better $BONK or $WIF",
            "$BONK vs $WIF",
        ):
            names = route_tools(question)
            assert "price_lookup" in names, f"failed to route price_lookup: {question}"
            assert detect_comparison(question)

    def test_compare_pairs_extracts_both_symbols(self) -> None:
        assert compare_pairs("compare $BONK vs $WIF") == [("BONK", "WIF")]
        assert symbols_in("compare $BONK vs $WIF") == ["BONK", "WIF"]
        assert compare_pairs("compare $BONK vs $WIF on solana") == [("BONK", "WIF")]

    def test_no_comparison_without_connector(self) -> None:
        assert not detect_comparison("price of $BONK")
        assert compare_pairs("price of $BONK") == []

    def test_price_kwargs_use_first_symbol(self) -> None:
        kw = tool_kwargs("compare $BONK vs $WIF", ["price_lookup"])
        assert kw["price_lookup"]["symbol"] == "BONK"


# ══════════════════════════════════════════════════════════════════════════
# negation & exclusion — "what's NOT trending" / "don't show me trending"
# ══════════════════════════════════════════════════════════════════════════


class TestNegationAndExclusion:
    def test_negation_metadata_captures_concept(self) -> None:
        assert detect_negations("what's NOT trending?") == {"trending"}
        assert detect_negations("is $BONK not a honeypot?") == {"security"}
        assert detect_negations("the market is not bullish") == {"sentiment"}

    def test_pure_negation_keeps_routing(self) -> None:
        # "what's NOT trending" still needs the trending feed — the LLM
        # answers the negative from real data.
        assert "trending" in route_tools("what's NOT trending?")

    def test_property_negation_keeps_security_routing(self) -> None:
        # "is X not a honeypot?" is still a security question — the scan
        # data is required to answer it.
        names = route_tools(f"is {EVM} not a honeypot?")
        assert "security_scan" in names
        assert "scanner_intel" in names

    def test_exclusion_suppresses_its_tool(self) -> None:
        assert detect_exclusions("don't show me trending") == {"trending"}
        assert "trending" not in route_tools("don't show me trending")

    def test_exclusion_keeps_other_intents(self) -> None:
        names = route_tools("except the news, what's trending?")
        assert "trending" in names
        assert "news_headlines" not in names
        assert "web_search" not in names

    def test_exclusion_suppresses_market_tools(self) -> None:
        assert detect_exclusions("without the market stuff") == {"market"}
        names = route_tools("without the market stuff, what's the sentiment?")
        assert "market_condition" not in names
        assert "market_overview" not in names

    def test_negative_market_phrase_still_routes_market_condition(self) -> None:
        # "is the market NOT bullish?" — negation metadata only; the market
        # question itself still routes the macro read.
        names = route_tools("is the market not bullish?")
        assert "market_condition" in names
        assert detect_negations("is the market not bullish?") == {"sentiment"}


# ══════════════════════════════════════════════════════════════════════════
# multi-intent — one question, several tools
# ══════════════════════════════════════════════════════════════════════════


class TestMultiIntent:
    def test_buy_advice_plus_sentiment_routes_both(self) -> None:
        names = route_tools("should I buy $BONK? what's the sentiment?")
        assert "price_lookup" in names
        assert "market_condition" in names

    def test_trending_plus_price_routes_both(self) -> None:
        names = route_tools("what's trending? and what's the price of $BONK")
        assert "trending" in names
        assert "price_lookup" in names


# ══════════════════════════════════════════════════════════════════════════
# run_for_message end-to-end with mocked handlers — kwargs reach the tools
# ══════════════════════════════════════════════════════════════════════════


class TestRunForMessageWiring:
    async def test_scan_question_delivers_address_to_scanner_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        received: dict[str, dict[str, Any]] = {}

        async def fake_scanner(**kwargs: Any) -> ToolResult:
            received["scanner_intel"] = kwargs
            return ToolResult(tool="scanner_intel", ok=True, data={}, addresses=[kwargs.get("address", "")])

        async def fake_token_meta(**kwargs: Any) -> ToolResult:
            received["token_meta"] = kwargs
            return ToolResult(tool="token_meta", ok=True, data={})

        async def fake_security_scan(**kwargs: Any) -> ToolResult:
            received["security_scan"] = kwargs
            return ToolResult(tool="security_scan", ok=True, data={})

        handlers = dict(registry._handlers)
        handlers.update(
            {
                "scanner_intel": fake_scanner,
                "token_meta": fake_token_meta,
                "security_scan": fake_security_scan,
            }
        )
        monkeypatch.setattr(registry, "_handlers", handlers)

        results = await run_for_message(f"scan {EVM} on base")
        assert "scanner_intel" in results
        assert received["scanner_intel"]["address"] == EVM
        assert received["scanner_intel"]["chain"] == "base"
        assert received["token_meta"]["address"] == EVM
        assert received["security_scan"]["address"] == EVM

    async def test_trending_question_delivers_chain_to_trend_by_chain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        received: dict[str, dict[str, Any]] = {}

        async def fake_trending(**kwargs: Any) -> ToolResult:
            received["trending"] = kwargs
            return ToolResult(tool="trending", ok=True, data={})

        async def fake_trend_by_chain(**kwargs: Any) -> ToolResult:
            received["trend_by_chain"] = kwargs
            return ToolResult(tool="trend_by_chain", ok=True, data={})

        handlers = dict(registry._handlers)
        handlers.update(
            {
                "trending": fake_trending,
                "trend_by_chain": fake_trend_by_chain,
            }
        )
        monkeypatch.setattr(registry, "_handlers", handlers)

        results = await run_for_message("what's hot on solana?")
        assert "trend_by_chain" in results
        assert received["trend_by_chain"]["chain"] == "solana"
        assert received["trending"] == {}

    async def test_smart_money_question_delivers_kind_to_birdeye(self, monkeypatch: pytest.MonkeyPatch) -> None:
        received: dict[str, dict[str, Any]] = {}

        async def fake_smart_money(**kwargs: Any) -> ToolResult:
            received["smart_money"] = kwargs
            return ToolResult(tool="smart_money", ok=True, data={})

        async def fake_birdeye(**kwargs: Any) -> ToolResult:
            received["birdeye_market"] = kwargs
            return ToolResult(tool="birdeye_market", ok=True, data={})

        handlers = dict(registry._handlers)
        handlers.update({"smart_money": fake_smart_money, "birdeye_market": fake_birdeye})
        monkeypatch.setattr(registry, "_handlers", handlers)

        results = await run_for_message("smart money on base")
        assert "birdeye_market" in results
        assert received["birdeye_market"]["kind"] == "smart_money"
        assert received["birdeye_market"]["chain"] == "base"
        assert received["smart_money"]["chain"] == "base"

    async def test_sentiment_question_delivers_no_kwargs_to_market_condition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        received: dict[str, dict[str, Any]] = {}

        async def fake_market_condition(**kwargs: Any) -> ToolResult:
            received["market_condition"] = kwargs
            return ToolResult(tool="market_condition", ok=True, data={"verdict": "neutral"})

        handlers = dict(registry._handlers)
        handlers["market_condition"] = fake_market_condition
        monkeypatch.setattr(registry, "_handlers", handlers)

        results = await run_for_message("what's the sentiment?")
        assert "market_condition" in results
        # market_condition takes no address/symbol/chain kwargs — empty map.
        assert received["market_condition"] == {}
