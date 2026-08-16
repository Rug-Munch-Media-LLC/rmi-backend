"""Happy-path tests for the /chat user journey (remote LLM fallback chain).

Covers ``app/domains/telegram/rugmunchbot/handlers/chat_ai``:

  * ``_route_tier`` — a simple question is classified ``snappy`` and a
    forensic/market-deep question ``long``.
  * ``_call_remote_fast`` — builds the right OpenRouter call (URL, auth
    header, payload model/messages/max_tokens) and returns an answer dict.
  * ``_gather_context`` — a market question injects live market context.

``httpx.AsyncClient`` and the provider configs are faked at the module
boundary — no network I/O (mirrors ``tests/test_chat_ai_history.py``).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot.handlers.chat_ai import _llm
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult


class _FakeLLMClient:
    """httpx.AsyncClient class fake recording posts (configurable response)."""

    _status: ClassVar[int] = 200
    _payload: ClassVar[Any] = {
        "choices": [{"message": {"content": "SOL is trading at $145. per the scan."}}],
        "usage": {"total_tokens": 12},
    }

    calls: ClassVar[list[tuple[str, dict | None, dict]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeLLMClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Any:
        _FakeLLMClient.calls.append((url, headers, json or {}))

        class _Resp:
            status_code = _FakeLLMClient._status

            def json(self) -> Any:
                return _FakeLLMClient._payload

        return _Resp()


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeLLMClient._status = 200
    _FakeLLMClient._payload = {
        "choices": [{"message": {"content": "SOL is trading at $145. per the scan."}}],
        "usage": {"total_tokens": 12},
    }
    _FakeLLMClient.calls = []
    monkeypatch.setattr(_llm.httpx, "AsyncClient", _FakeLLMClient)
    monkeypatch.setattr(
        _llm,
        "get_snappy_config",
        lambda: {
            "model": "poolside/snappy",
            "base_url": "https://openrouter.test/api/v1/chat/completions",
            "api_key": "test-snappy-key",
            "max_tokens": 256,
            "temperature": 0.5,
        },
    )
    monkeypatch.setattr(
        _llm,
        "get_long_config",
        lambda: {
            "model": "nvidia/nemotron-long",
            "base_url": "https://openrouter.test/api/v1/chat/completions",
            "api_key": "test-long-key",
            "max_tokens": 1024,
            "temperature": 0.3,
        },
    )


class _FakeRegistry:
    """ToolRegistry fake: ``run_many_kwargs`` returns canned ToolResults."""

    def __init__(self, results: dict[str, ToolResult]) -> None:
        self._results = results

    async def run_many_kwargs(self, spec: dict[str, dict[str, Any]], **shared: Any) -> dict[str, ToolResult]:
        return {name: self._results[name] for name in spec if name in self._results}


# ── _route_tier: snappy vs long classification ────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "hello",
        "thanks!",
        "what is a honeypot",
        "explain what a rug pull is",
        "what's the price of solana",
    ],
)
def test_route_tier_classifies_simple_questions_snappy(message: str) -> None:
    assert _llm._route_tier(message) == "snappy"


@pytest.mark.parametrize(
    "message",
    [
        "analyze this contract 0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        "investigate the deployer of this token",
        "why did the price dump and what happens next",
        "trace the on-chain movement of these funds",
    ],
)
def test_route_tier_classifies_forensic_questions_long(message: str) -> None:
    assert _llm._route_tier(message) == "long"


def test_route_tier_very_long_message_is_long() -> None:
    long_msg = "Can you give me a full breakdown of everything that happened with this token "
    long_msg += "over the last week including all the price action, holders, volume and news "
    long_msg += "coverage so I can decide whether to keep my position?"
    assert len(long_msg) > _llm._TIER_ANALYSIS_LENGTH_THRESHOLD
    assert _llm._route_tier(long_msg) == "long"


# ── _call_remote_fast: OpenRouter call construction + answer ──────────────


async def test_call_remote_fast_builds_openrouter_call_and_returns_answer() -> None:
    messages = [
        {"role": "system", "content": "You are Rug Munch Bot."},
        {"role": "user", "content": "CURRENT QUESTION: what is the price of solana?"},
    ]
    sources = [{"type": "price_lookup", "query": "solana", "title": "price_lookup · coingecko"}]

    result = await _llm._call_remote_fast("snappy", messages, 1, sources=sources)

    url, headers, payload = _FakeLLMClient.calls[-1]
    # The call goes to the snappy provider config, with real auth headers.
    assert url == "https://openrouter.test/api/v1/chat/completions"
    assert headers is not None
    assert headers["Authorization"] == "Bearer test-snappy-key"
    assert headers["Content-Type"] == "application/json"
    assert headers["HTTP-Referer"] == "https://rugmunch.io"
    # The payload carries the model + the exact messages + a sane budget.
    assert payload["model"] == "poolside/snappy"
    assert payload["messages"] == messages
    assert payload["max_tokens"] == 256
    assert payload["temperature"] == 0.5

    # Behavioral answer: a dict with the expected fields.
    assert result is not None
    assert "SOL is trading at $145." in result["message"]
    assert result["rag_count"] == len(sources)  # real grounding source count
    assert result["provider"] == "remote-fast"
    assert result["model"] == "poolside/snappy"
    assert result["tokens"] == 12


async def test_call_remote_fast_long_uses_long_config_and_budget() -> None:
    messages = [{"role": "user", "content": "CURRENT QUESTION: analyze this contract"}]
    result = await _llm._call_remote_fast("long", messages, 1)

    url, headers, payload = _FakeLLMClient.calls[-1]
    assert url == "https://openrouter.test/api/v1/chat/completions"
    assert headers is not None
    assert headers["Authorization"] == "Bearer test-long-key"
    assert payload["model"] == "nvidia/nemotron-long"
    # Long tier floors the output budget so deep answers are never cut off.
    assert payload["max_tokens"] >= _llm.REMOTE_LONG_MAX_TOKENS
    assert result is not None
    assert result["provider"] == "remote-fast"


# ── _gather_context: market question injects market context ───────────────


async def test_gather_context_market_question_injects_market_context(monkeypatch: pytest.MonkeyPatch) -> None:
    price_result = ToolResult(
        tool="price_lookup",
        ok=True,
        data={"bitcoin": {"usd": 100_000, "usd_24h_change": 2.5}},
        source="coingecko",
    )
    monkeypatch.setattr(
        "app.domains.telegram.rugmunchbot.tools.get_registry",
        lambda: _FakeRegistry({"price_lookup": price_result}),
    )

    context_block, sources = await _llm._gather_context("what is the price of bitcoin right now?")

    assert context_block
    assert "LIVE MARKET DATA" in context_block
    assert "bitcoin" in context_block
    assert sources
    assert all(isinstance(s, dict) for s in sources)
    assert any(s.get("type") == "price_lookup" for s in sources)


async def test_gather_context_unknown_question_returns_empty() -> None:
    context_block, sources = await _llm._gather_context("hello there friend")
    assert context_block == ""
    assert sources == []


# ── _call_remote_llm: end-to-end snappy answer ────────────────────────────


async def test_call_remote_llm_answers_market_question_with_grounding() -> None:
    result = await _llm._call_remote_llm(
        "what is the price of solana?",
        1,
        sources=[{"type": "price_lookup", "query": "solana", "title": "price_lookup · coingecko"}],
    )
    assert result is not None
    assert "SOL is trading at $145." in result["message"]
    # The question is labeled so the model answers THIS question.
    _, _, payload = _FakeLLMClient.calls[-1]
    assert payload["messages"][-1]["content"] == "CURRENT QUESTION: what is the price of solana?"
