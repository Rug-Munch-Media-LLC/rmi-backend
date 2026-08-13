"""Integration-style wiring tests for the chat tool-use layer.

Covers the END-TO-END path — intent detection → executor dispatch →
context injection — for every intent the classifier can produce
(price/scan/ta/market/trace/news), plus the route-level guarantee that a
live tool result SUPPRESSES the RAG + web search fallbacks.

  - ``test_intent_executor_wiring_table``: ``_detect_tool_intent`` classifies
    each message and ``_EXECUTORS`` maps that intent to the expected executor.
  - ``test_run_tool_for_intent_dispatches_mocked_executor``: the dispatcher
    calls the executor registered for the intent with the original message
    and returns its output.
  - ``test_executor_set_matches_classifier_output``: every intent the
    classifier can return is wired in ``_EXECUTORS`` (no dead intent, no
    unregistered executor).
  - ``test_fetch_tool_context_flows_result_into_system_context``: for each
    intent, the real ``_detect_tool_intent`` + ``_run_tool_for_intent`` path
    (executor mocked) yields a result that ``_inject_tool_result`` appends to
    the system context.
  - ``test_route_tool_result_suppresses_rag_and_web``: with a live tool
    result present, the chat route skips both the RAG search and the web
    search, and the tool result lands in the system prompt handed to the LLM.
  - ``test_route_no_tool_result_still_uses_rag``: when the tool layer returns
    None, RAG grounding still runs (tools never disable RAG by their absence).

No network, DB, or LLM calls — executors, RAG, web search, and the LLM
completion are all mocked at the module boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains import chat_tools
from app.domains.chat_tools import (
    _detect_tool_intent,
    _exec_market,
    _exec_news,
    _exec_price,
    _exec_scan,
    _exec_ta,
    _exec_trace,
    _run_tool_for_intent,
)
from app.routers import chat as chat_mod
from app.routers.chat import _fetch_tool_context, _inject_tool_result, router as chat_router

if TYPE_CHECKING:
    from collections.abc import Iterator

_EVM = "0x" + "a" * 40
_SOL = "D8pom1W4v6j8kQkfQq2mNnXxYyZzAaBbCcDdEeFfGgHh"

# (message, expected_intent, executor) — one row per intent the classifier
# can produce. Kept in sync with _EXECUTORS below.
_INTENT_EXECUTOR_TABLE = [
    ("what is the price of solana", "price", _exec_price),
    (f"scan this token {_EVM}", "scan", _exec_scan),
    ("rsi of btc", "ta", _exec_ta),
    ("how's the market", "market", _exec_market),
    (f"who funded {_EVM}", "trace", _exec_trace),
    ("latest news", "news", _exec_news),
]


@pytest.fixture(autouse=True)
def _clear_chat_cache() -> None:
    """Route-level tests must never hit the in-process response cache."""
    chat_mod._chat_cache.clear()
    yield
    chat_mod._chat_cache.clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(chat_router)
    with TestClient(app) as c:
        yield c


def _completion_result(content: str = "Grounded answer.") -> dict[str, Any]:
    return {"content": content, "model": "test-model", "_provider": "test-provider", "_latency_ms": 1, "usage": {}}


# ── 1. Intent → executor wiring table ─────────────────────────────────


@pytest.mark.parametrize(("message", "intent", "executor"), _INTENT_EXECUTOR_TABLE)
def test_intent_executor_wiring_table(message: str, intent: str, executor: Any) -> None:
    """The classifier returns ``intent`` and the executor is wired for it."""
    assert _detect_tool_intent(message) == intent
    assert chat_tools._EXECUTORS[intent] is executor


def test_executor_set_matches_classifier_output() -> None:
    """No intent the classifier can return is missing from ``_EXECUTORS``."""
    expected = {intent for _, intent, _ in _INTENT_EXECUTOR_TABLE}
    assert set(chat_tools._EXECUTORS) == expected


# ── 2. Dispatcher routes each intent to its executor ───────────────────


@pytest.mark.parametrize(("message", "intent", "_"), _INTENT_EXECUTOR_TABLE)
async def test_run_tool_for_intent_dispatches_mocked_executor(message: str, intent: str, _: Any) -> None:
    """``_run_tool_for_intent`` awaits the executor registered for the intent
    with the original message and returns its output."""
    fake = AsyncMock(return_value=f"live {intent} data")
    with patch.dict(chat_tools._EXECUTORS, {intent: fake}):
        result = await _run_tool_for_intent(message, intent)
    assert result == f"live {intent} data"
    fake.assert_awaited_once_with(message)


# ── 3. Full path: intent → executor → context injection ────────────────


@pytest.mark.parametrize(("message", "intent", "_"), _INTENT_EXECUTOR_TABLE)
async def test_fetch_tool_context_flows_result_into_system_context(message: str, intent: str, _: Any) -> None:
    """Real classifier + dispatcher (executor mocked) → result flows into
    the system context via ``_inject_tool_result``."""
    fake = AsyncMock(return_value=f"live {intent} data")
    with patch.dict(chat_tools._EXECUTORS, {intent: fake}):
        tool_result = await _fetch_tool_context(message)
    assert tool_result == f"live {intent} data"
    fake.assert_awaited_once_with(message)

    ctx = _inject_tool_result("RAG Context:\nsome hits", tool_result)
    assert "TOOL RESULT (live data):" in ctx
    assert f"live {intent} data" in ctx
    assert ctx.index("TOOL RESULT") > ctx.index("RAG Context")


# ── 4. Route-level: tool result suppresses RAG + web ───────────────────


def test_route_tool_result_suppresses_rag_and_web(client: TestClient) -> None:
    """When the tool layer returns live data, the chat route must NOT run the
    RAG search or the web search — the tool result is the grounding."""
    captured: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["messages"]
        return _completion_result("Live-data answer.")

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_fetch_tool_context", new=AsyncMock(return_value="BTC is $67,000")),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(return_value=("RAG hits", 2))) as rag_mock,
        patch.object(chat_mod, "searxng_search", new=AsyncMock(return_value=[{"title": "t", "url": "u"}])) as web_mock,
    ):
        resp = client.post("/api/v1/chat", json={"message": "what is the price of solana"})

    assert resp.status_code == 200, resp.text
    rag_mock.assert_not_awaited()
    web_mock.assert_not_awaited()
    assert resp.headers.get("X-RAG-Context") == "0"

    system_msg = captured["messages"][0]["content"]
    assert "TOOL RESULT (live data):" in system_msg
    assert "BTC is $67,000" in system_msg


def test_route_no_tool_result_still_uses_rag(client: TestClient) -> None:
    """When the tool layer returns None (no intent / executor failed), RAG
    grounding still runs — tools never disable RAG by their absence."""
    captured: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["messages"]
        return _completion_result("RAG answer.")

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_fetch_tool_context", new=AsyncMock(return_value=None)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(return_value=("RAG hit content", 1))) as rag_mock,
        patch.object(chat_mod, "searxng_search", new=AsyncMock(return_value=[{"title": "t", "url": "u"}])) as web_mock,
    ):
        resp = client.post("/api/v1/chat", json={"message": "what is the price of solana"})

    assert resp.status_code == 200, resp.text
    rag_mock.assert_awaited_once()
    web_mock.assert_not_awaited()  # not a current-events query

    system_msg = captured["messages"][0]["content"]
    assert "RAG hit content" in system_msg
    assert "TOOL RESULT (live data):" not in system_msg
