"""Unit tests for the RugMunchBot zero-fabrication tool registry
(``app/domains/telegram/rugmunchbot/tools/``).

Proves the four hard guarantees:

  (a) a trending question routes to a tool that returns ONLY real symbols —
      never a fabricated ticker;
  (b) when a data source is down / rate-limited the tool returns ``ok=False``
      and the chat layer must answer with the exact ``NO_LIVE_DATA_MESSAGE``;
  (c) every HTTP call in the async tool paths goes through
      ``httpx.AsyncClient`` (no ``requests``/``urllib``/sync ``httpx.Client``,
      no un-awaited client calls) — verified statically (AST scan over
      ``tools/*.py``) and dynamically (a recorded fake AsyncClient);
  (d) ``sanitize_answer``/``fabrication_note`` strip a ``$SYMBOL`` that no
      tool returned, and never corrupt USD amounts or known symbols.

Mock boundary: ``httpx.AsyncClient`` is faked at the ``_client`` module
boundary (mirrors ``tests/unit/domain/test_chat_ai_llm.py``) — no live
network I/O, no live APIs.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot import tools
from app.domains.telegram.rugmunchbot.tools import _client, known_addresses, known_symbols, registry, run_for_message
from app.domains.telegram.rugmunchbot.tools.guardrails import (
    NO_LIVE_DATA_MESSAGE,
    fabrication_note,
    sanitize_answer,
)
from app.domains.telegram.rugmunchbot.tools.intent import (
    build_tool_prompt_block,
    extract_addresses as intent_extract_addresses,
    route_tools,
    symbols_in,
)
from app.domains.telegram.rugmunchbot.tools.price import price_lookup
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult
from app.domains.telegram.rugmunchbot.tools.trending import news_headlines, trending

EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# The live payload the backend /api/v1/market/trending would return.
_TRENDING_PAYLOAD: dict[str, Any] = {
    "items": [
        {
            "symbol": "BONK",
            "name": "Bonk",
            "address": "ADDR_BONK",
            "chain": "solana",
            "price": 0.00002,
            "change_24h": 5.0,
            "volume_24h": 1000,
            "market_cap": 9000,
            "rank": 1,
            "source": "dexscreener",
        },
        {
            "symbol": "WIF",
            "name": "Dogwifhat",
            "address": "ADDR_WIF",
            "chain": "solana",
            "price": 1.2,
            "change_24h": -2.0,
            "volume_24h": 2000,
            "market_cap": 8000,
            "rank": 2,
            "source": "dexscreener",
        },
    ]
}


class _FakeAsyncClient:
    """httpx.AsyncClient class fake — records requests, routes canned bodies.

    Mirrors the class-fake style of ``tests/unit/domain/test_chat_ai_llm.py``.
    ``_responses`` maps URL substrings to JSON bodies; an unmatched URL
    returns HTTP 404 (a dead source). Set ``_raise`` to simulate network
    failure / rate-limit transport errors.
    """

    _responses: ClassVar[dict[str, Any]] = {}
    _status: ClassVar[int] = 200
    _raise: ClassVar[Exception | None] = None
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeAsyncClient.calls.append({"method": "GET", "url": url, "params": params or {}, "headers": headers or {}})
        return self._respond(url)

    async def post(self, url: str, json: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeAsyncClient.calls.append({"method": "POST", "url": url, "json": json or {}, "headers": headers or {}})
        return self._respond(url)

    def _respond(self, url: str) -> Any:
        if _FakeAsyncClient._raise is not None:
            raise _FakeAsyncClient._raise
        for prefix, body in _FakeAsyncClient._responses.items():
            if prefix in url:

                class _Resp:
                    status_code = _FakeAsyncClient._status

                    def json(self, _body: Any = body) -> Any:
                        return _body

                return _Resp()

        class _Resp404:
            status_code = 404

            def json(self) -> Any:
                return {"error": "not found"}

        return _Resp404()


@pytest.fixture(autouse=True)
def _fake_http_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the httpx boundary for every test: no live network, isolated cache.

    Resets the class-fake state and clears the shared registry TTL cache so
    tests are order-independent (tools cache by kwargs for up to 120s).
    """
    _FakeAsyncClient._responses = {}
    _FakeAsyncClient._status = 200
    _FakeAsyncClient._raise = None
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(_client.httpx, "AsyncClient", _FakeAsyncClient)
    registry.clear_cache()
    yield
    registry.clear_cache()


# ══════════════════════════════════════════════════════════════════════════
# (d) sanitize_answer / fabrication_note — fabrication guardrail
# ══════════════════════════════════════════════════════════════════════════


class TestSanitizeAnswer:
    def test_fabricated_symbol_stripped(self) -> None:
        answer, stripped, addresses = sanitize_answer("Buy $FAKETOKEN now", {"BONK"}, set())
        assert "$FAKETOKEN" not in answer
        assert stripped == ["FAKETOKEN"]
        assert addresses == []

    def test_mixed_real_and_fabricated(self) -> None:
        answer, stripped, _ = sanitize_answer("$BONK and $FAKETOKEN both moving", {"BONK", "WIF"}, set())
        assert "$BONK" in answer  # real tool data kept
        assert "$FAKETOKEN" not in answer  # fabricated stripped
        assert stripped == ["FAKETOKEN"]

    def test_known_symbol_kept(self) -> None:
        answer, stripped, _ = sanitize_answer("$BONK is up 12%", {"BONK"}, set())
        assert "$BONK" in answer
        assert stripped == []

    def test_lowercase_fabricated_symbol_removed(self) -> None:
        # The known set is upper-cased but the LLM answer may write $pepe.
        # The strip must be case-insensitive or the fabrication stays visible
        # while the code claims it was stripped.
        answer, stripped, _ = sanitize_answer("$pepe is a scam, do not buy $pepe", {"BONK"}, set())
        assert "$pepe" not in answer
        assert "pepe" not in answer.lower()
        assert stripped == ["PEPE"]  # reported once, deduplicated

    def test_mixed_case_fabricated_symbol_removed(self) -> None:
        answer, stripped, _ = sanitize_answer("buy $PePe now", {"BONK"}, set())
        assert "$PePe" not in answer
        assert stripped == ["PEPE"]

    def test_dollar_amount_not_stripped(self) -> None:
        # $50 is a USD amount, not a ticker — the guardrail must not destroy
        # legitimate prose.
        answer, stripped, _ = sanitize_answer("the project raised $50 million in funding", {"BONK"}, set())
        assert "$50" in answer
        assert stripped == []

    def test_dollar_amount_with_unit_not_stripped(self) -> None:
        answer, stripped, _ = sanitize_answer("market cap of $2.5B and raised $50m", {"BONK"}, set())
        assert "$2.5B" in answer
        assert "$50m" in answer
        assert stripped == []

    def test_fabricated_address_replaced(self) -> None:
        answer, stripped, addresses = sanitize_answer(
            "check 0x1111111111111111111111111111111111111111 it is a scam",
            set(),
            {"0x2222222222222222222222222222222222222222"},
        )
        assert "[address not verified]" in answer
        assert "0x1111111111111111111111111111111111111111" not in answer
        assert stripped == []
        assert addresses == ["0x1111111111111111111111111111111111111111"]

    def test_known_address_kept(self) -> None:
        answer, _, addresses = sanitize_answer(f"wallet {EVM} holds BONK", set(), {EVM})
        assert EVM in answer
        assert addresses == []

    def test_unchanged_when_nothing_fabricated(self) -> None:
        original = "BONK is up 12% on strong volume today."
        answer, stripped, addresses = sanitize_answer(original, {"BONK"}, set())
        assert answer == original
        assert stripped == []
        assert addresses == []

    def test_duplicate_fabricated_symbol_listed_once(self) -> None:
        answer, stripped, _ = sanitize_answer("$FAKE is hot. $FAKE will moon.", {"BONK"}, set())
        assert "$FAKE" not in answer
        assert stripped == ["FAKE"]


class TestFabricationNote:
    def test_symbols_note(self) -> None:
        note = fabrication_note(["FAKETOKEN"], [])
        assert "$FAKETOKEN" in note
        assert "not verified against live data" in note

    def test_addresses_note(self) -> None:
        note = fabrication_note([], ["0x1111111111111111111111111111111111111111"])
        assert "addresses were not verified against live data" in note

    def test_empty_when_nothing_stripped(self) -> None:
        assert fabrication_note([], []) == ""

    def test_duplicate_tickers_listed_once(self) -> None:
        note = fabrication_note(["FAKE", "FAKE"], [])
        assert note.count("$FAKE") == 1


# ══════════════════════════════════════════════════════════════════════════
# (a) intent routing — trending questions select the trending tool
# ══════════════════════════════════════════════════════════════════════════


class TestIntentRouting:
    def test_trending_question_routes_to_trending(self) -> None:
        for question in (
            "what's trending?",
            "what is trending today",
            "what's hot right now",
            "top gainers today",
            "what's pumping on solana",
            "show me the movers",
            "top tokens right now",
        ):
            assert "trending" in route_tools(question), f"failed to route: {question}"

    def test_price_question_routes_to_price_lookup(self) -> None:
        assert "price_lookup" in route_tools("price of $BONK")
        assert "price_lookup" in route_tools("what is the price of BONK")
        assert "price_lookup" in route_tools("$BONK chart")

    def test_security_question_routes_to_security_scan(self) -> None:
        assert "security_scan" in route_tools(f"is {EVM} a honeypot?")
        assert "security_scan" in route_tools(f"scan {EVM} for risk")

    def test_wallet_question_routes_to_wallet_analysis(self) -> None:
        assert "wallet_analysis" in route_tools(f"what does wallet {EVM} hold?")
        assert "wallet_analysis" in route_tools(f"pnl of {EVM}")

    def test_bare_address_defaults_to_security_scan(self) -> None:
        assert route_tools(EVM) == ["security_scan"]

    def test_address_extraction_evm_and_solana(self) -> None:
        assert intent_extract_addresses(f"is {EVM} safe?") == [EVM]
        assert intent_extract_addresses(f"check {SOL} on jupiter") == [SOL]

    def test_symbols_in_uppercases(self) -> None:
        assert symbols_in("check $BONK and $pepe") == ["BONK", "PEPE"]


class TestBuildToolPromptBlock:
    def test_only_ok_results_included(self) -> None:
        ok = ToolResult(
            tool="trending", ok=True, data={"tokens": [{"symbol": "BONK"}]}, symbols=["BONK"], source="backend:/x"
        )
        bad = ToolResult(tool="price_lookup", ok=False, error="down")
        block = build_tool_prompt_block({"trending": ok, "price_lookup": bad})
        assert "LIVE MARKET DATA" in block
        assert "trending" in block
        assert "price_lookup" not in block  # failed sources are never shown
        assert "may ONLY cite symbols" in block

    def test_empty_results_still_labeled_live_data(self) -> None:
        block = build_tool_prompt_block({})
        assert block.startswith("LIVE MARKET DATA")


# ══════════════════════════════════════════════════════════════════════════
# (a) tools return ONLY symbols the live source actually returned
# ══════════════════════════════════════════════════════════════════════════


class TestTrendingToolRealSymbols:
    async def test_trending_returns_only_source_symbols(self) -> None:
        _FakeAsyncClient._responses = {"/api/v1/market/trending": _TRENDING_PAYLOAD}
        result = await trending(limit=10)
        assert result.ok
        # Symbols come from the live payload and NOTHING else — no invented
        # tickers, no empty symbol synthesized from the query.
        assert result.symbol_set == {"BONK", "WIF"}
        assert sorted(result.symbols) == ["BONK", "WIF"]
        assert result.addresses == ["ADDR_BONK", "ADDR_WIF"]
        assert result.source == "backend:/api/v1/market/trending"

    async def test_trending_hits_backend_over_http(self) -> None:
        _FakeAsyncClient._responses = {"/api/v1/market/trending": _TRENDING_PAYLOAD}
        result = await trending(limit=10)
        assert result.ok
        assert _FakeAsyncClient.calls, "expected the tool to call the backend over HTTP"
        assert _FakeAsyncClient.calls[0]["method"] == "GET"
        assert "/api/v1/market/trending" in _FakeAsyncClient.calls[0]["url"]

    async def test_trending_backend_down_ok_false(self) -> None:
        # No routes configured → every request 404s → source "down".
        result = await trending(limit=5)
        assert not result.ok
        assert result.symbols == []
        assert result.addresses == []
        assert result.error  # a real failure reason, not silence

    async def test_trending_rate_limited_ok_false(self) -> None:
        _FakeAsyncClient._responses = {"/api/v1/market/trending": {}}
        _FakeAsyncClient._status = 429
        result = await trending(limit=5)
        assert not result.ok
        assert result.symbols == []

    async def test_trending_transport_error_ok_false(self) -> None:
        _FakeAsyncClient._raise = _client.httpx.ConnectError("backend unreachable")
        result = await trending(limit=5)
        assert not result.ok
        assert result.symbols == []


class TestPriceLookupToolRealSymbols:
    async def test_price_lookup_returns_only_source_symbols(self) -> None:
        _FakeAsyncClient._responses = {
            "dexscreener.com/latest/dex/search": {
                "pairs": [
                    {
                        "chainId": "solana",
                        "dexId": "raydium",
                        "pairAddress": "PAIR1",
                        "baseToken": {"address": "ADDR_BONK", "symbol": "BONK", "name": "Bonk"},
                        "priceUsd": "0.00002",
                        "priceChange": {"h1": 1.0, "h24": 5.0},
                        "volume": {"h24": 12345},
                        "liquidity": {"usd": 50000},
                        "marketCap": 900000,
                        "fdv": 950000,
                    },
                    {
                        "chainId": "ethereum",
                        "dexId": "uniswap",
                        "pairAddress": "PAIR2",
                        "baseToken": {"address": "ADDR_PEPE", "symbol": "PEPE", "name": "Pepe"},
                        "priceUsd": "0.5",
                        "priceChange": {},
                        "volume": {},
                        "liquidity": {},
                    },
                ]
            }
        }
        result = await price_lookup(symbol="BONK")
        assert result.ok
        # Exactly the symbols the source returned — the tool adds nothing.
        assert result.symbol_set == {"BONK", "PEPE"}
        assert sorted(result.symbols) == ["BONK", "PEPE"]
        assert result.addresses == ["ADDR_BONK", "ADDR_PEPE"]

    async def test_price_lookup_source_down_ok_false(self) -> None:
        result = await price_lookup(symbol="BONK")
        assert not result.ok
        assert result.symbols == []

    async def test_price_lookup_backend_first_then_dexscreener(self) -> None:
        # Backend databus returns pairs → used, and DexScreener is never hit.
        _FakeAsyncClient._responses = {
            "/api/v1/databus/token/ADDR1": {
                "pairs": [
                    {
                        "chainId": "solana",
                        "dexId": "raydium",
                        "pairAddress": "PAIR1",
                        "baseToken": {"address": "ADDR1", "symbol": "BONK", "name": "Bonk"},
                        "priceUsd": "0.00002",
                        "priceChange": {},
                        "volume": {},
                        "liquidity": {},
                    }
                ]
            }
        }
        result = await price_lookup(address="ADDR1")
        assert result.ok
        assert result.source == "backend:/api/v1/databus/token"
        urls = [c["url"] for c in _FakeAsyncClient.calls]
        assert any("dexscreener" in u for u in urls) is False


class TestNewsHeadlinesTool:
    async def test_news_headlines_returns_source_titles(self) -> None:
        _FakeAsyncClient._responses = {
            "/api/v1/news/trending": {
                "items": [
                    {"title": "Solana volume hits a record", "url": "https://example.com/1", "source": "x"},
                    {"title": "New memecoin listings", "url": "https://example.com/2", "source": "y"},
                ]
            }
        }
        result = await news_headlines(limit=5)
        assert result.ok
        assert result.data["headlines"][0]["title"] == "Solana volume hits a record"
        assert result.symbols == []  # headlines carry no tickers

    async def test_news_headlines_down_ok_false(self) -> None:
        result = await news_headlines(limit=5)
        assert not result.ok


# ══════════════════════════════════════════════════════════════════════════
# (b) NO_LIVE_DATA_MESSAGE when the source is down / rate-limited
# ══════════════════════════════════════════════════════════════════════════


class TestNoLiveData:
    async def test_trending_down_produces_no_live_data_contract(self) -> None:
        results = await run_for_message("what's trending right now?")
        assert "trending" in results
        assert not results["trending"].ok
        # Nothing to verify against → the guardrail has no known symbols.
        assert known_symbols(results) == set()
        assert known_addresses(results) == set()
        # The chat layer's fallback branch appends exactly this message.
        from app.domains.telegram.rugmunchbot.handlers import chat_ai

        assert chat_ai.NO_LIVE_DATA_MESSAGE is NO_LIVE_DATA_MESSAGE
        assert NO_LIVE_DATA_MESSAGE == "I don't have live data for that right now."

    async def test_price_down_produces_no_live_data_contract(self) -> None:
        results = await run_for_message("price of $BONK")
        assert "price_lookup" in results
        assert not results["price_lookup"].ok
        assert known_symbols(results) == set()

    async def test_news_down_ok_false_message_available(self) -> None:
        result = await news_headlines(limit=5)
        assert not result.ok
        # The caller-facing constant the handler uses for the failure path.
        assert NO_LIVE_DATA_MESSAGE == "I don't have live data for that right now."


# ══════════════════════════════════════════════════════════════════════════
# (c) no blocking calls in async paths — all HTTP via httpx.AsyncClient
# ══════════════════════════════════════════════════════════════════════════


class TestAsyncNoBlocking:
    def test_no_blocking_http_imports_or_calls_in_tool_modules(self) -> None:
        """Static AST proof: tools/*.py never imports or calls a blocking
        HTTP client, and every ``client.get/post`` call is awaited."""
        tools_dir = Path(tools.__file__).parent
        modules = sorted(p for p in tools_dir.glob("*.py") if p.name != "__init__.py")
        assert modules, "expected tool modules to scan"

        forbidden_import_roots = {"requests", "urllib", "urllib3", "http", "socket"}
        sync_httpx_attrs = {"Client", "get", "post", "put", "patch", "delete", "request", "stream"}

        for path in modules:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        assert root not in forbidden_import_roots, f"{path.name}: blocking import {alias.name}"
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    assert root not in forbidden_import_roots, f"{path.name}: blocking import {node.module}"
                elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "httpx":
                    assert node.attr not in sync_httpx_attrs, f"{path.name}: blocking httpx.{node.attr}"
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    func = node.func
                    if (
                        func.attr in ("get", "post", "request")
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "client"
                    ):
                        # Every client.get/post must be awaited inside an async def.
                        ancestors: list[ast.AST] = []
                        stack: list[tuple[ast.AST, list[ast.AST]]] = [(tree, [])]
                        while stack:
                            cur, parents = stack.pop()
                            for child in ast.iter_child_nodes(cur):
                                child_parents = [*parents, cur]
                                if child is node:
                                    ancestors = child_parents
                                    stack.clear()
                                    break
                                stack.append((child, child_parents))
                        assert any(isinstance(a, ast.Await) for a in ancestors), (
                            f"{path.name}: client.{func.attr}() not awaited"
                        )
                        assert any(isinstance(a, ast.AsyncFunctionDef) for a in ancestors), (
                            f"{path.name}: client.{func.attr}() outside async def"
                        )

    def test_no_time_sleep_in_tool_modules(self) -> None:
        """Blocking ``time.sleep`` would stall the bot event loop."""
        tools_dir = Path(tools.__file__).parent
        for path in sorted(p for p in tools_dir.glob("*.py") if p.name != "__init__.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "sleep"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "time"
                ):
                    raise AssertionError(f"{path.name}: blocking time.sleep()")

    async def test_http_goes_through_httpx_async_client_dynamically(self) -> None:
        _FakeAsyncClient._responses = {"/api/v1/market/trending": _TRENDING_PAYLOAD}
        result = await trending(limit=3)
        assert result.ok
        # Every HTTP call was recorded on the AsyncClient fake.
        assert _FakeAsyncClient.calls, "expected HTTP via AsyncClient"
        assert all(c["method"] in ("GET", "POST") for c in _FakeAsyncClient.calls)
        # The shared factory produces an AsyncClient (faked), never a sync Client.
        assert isinstance(_client.make_client(), _FakeAsyncClient)

    async def test_no_sync_httpx_client_instantiated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _SyncBomb:
            def __init__(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("sync httpx.Client was instantiated")

        monkeypatch.setattr(_client.httpx, "Client", _SyncBomb)
        _FakeAsyncClient._responses = {"/api/v1/market/trending": _TRENDING_PAYLOAD}
        result = await trending(limit=3)
        assert result.ok
        assert _FakeAsyncClient.calls


# ══════════════════════════════════════════════════════════════════════════
# End-to-end: routing → tool → guardrail (requirements a + d together)
# ══════════════════════════════════════════════════════════════════════════


class TestRunForMessageFlow:
    async def test_trending_question_full_flow_no_fabricated_tickers(self) -> None:
        # 1. A trending question routes to the trending tool.
        assert "trending" in route_tools("what's trending on solana right now?")
        # 2. The source returns exactly BONK + WIF.
        _FakeAsyncClient._responses = {"/api/v1/market/trending": _TRENDING_PAYLOAD}
        results = await run_for_message("what's trending on solana right now?")
        assert "trending" in results
        assert results["trending"].ok
        # 3. An LLM answer citing $BONK (real) + $FAKETOKEN (fabricated) keeps
        #    the real one and strips the fabricated one.
        answer = "The hot tokens are $BONK and $FAKETOKEN right now."
        clean, stripped_syms, _ = sanitize_answer(answer, known_symbols(results), known_addresses(results))
        assert "$BONK" in clean
        assert "$FAKETOKEN" not in clean
        assert stripped_syms == ["FAKETOKEN"]

    async def test_unknown_question_returns_no_tools(self) -> None:
        # A general-knowledge question routes to nothing (backend RAG grounds it).
        assert await run_for_message("what is a honeypot?") == {}


# ══════════════════════════════════════════════════════════════════════════
# Registry resilience — never raises, caches real results, retries once
# ══════════════════════════════════════════════════════════════════════════


class TestRegistryResilience:
    async def test_unknown_tool_returns_ok_false(self) -> None:
        result = await registry.run("does_not_exist")
        assert not result.ok
        assert "unknown tool" in (result.error or "")

    async def test_handler_exception_returns_ok_false_never_raises(self) -> None:
        from app.domains.telegram.rugmunchbot.tools.registry import ToolRegistry

        reg = ToolRegistry()

        async def boom(**kwargs: Any) -> ToolResult:
            raise RuntimeError("source exploded")

        reg.register("boom", boom, ttl=0.0)
        result = await reg.run("boom")
        assert not result.ok
        assert "RuntimeError" in (result.error or "")

    async def test_ok_result_is_cached_until_cleared(self) -> None:
        from app.domains.telegram.rugmunchbot.tools.registry import ToolRegistry

        reg = ToolRegistry()
        calls: list[int] = []

        async def counting(**kwargs: Any) -> ToolResult:
            calls.append(1)
            return ToolResult(tool="counting", ok=True, data={"n": len(calls)})

        reg.register("counting", counting, ttl=60.0)
        first = await reg.run("counting")
        second = await reg.run("counting")
        assert first.ok
        assert second.ok
        assert len(calls) == 1  # second call served from cache
        assert second.cached is True

        reg.clear_cache()
        third = await reg.run("counting")
        assert third.ok
        assert len(calls) == 2  # cache cleared → handler ran again
