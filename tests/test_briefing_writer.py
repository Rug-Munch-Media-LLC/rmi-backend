"""Pure tests for the RMI Morning Brief LLM writer — no network, no DB.

Covers ``llm_writer`` (system-prompt construction, message/kwargs passed to
the injectable ``completion``, ""-on-failure fallback contract) and the
``draft_briefing`` → deterministic-template fallback when ``llm_writer``
returns "".
"""

from __future__ import annotations

import functools
from typing import Any

from app.domains.content.briefing import (
    SECTIONS,
    draft_briefing,
    llm_writer,
)

MARKET = {
    "btc_price": 67231.15,
    "btc_change_24h": 2.1,
    "eth_price": 3412.5,
    "eth_change_24h": -0.4,
    "sol_price": 145.2,
    "sol_change_24h": 5.2,
}

CONTEXT: dict[str, Any] = {
    "market": dict(MARKET),
    "top_stories": [
        {
            "title": "Ethereum ETF inflows hit record",
            "detail": "Spot ETH ETFs saw record net inflows.",
            "source": "CoinDesk",
            "url": "https://example.com/eth-etf",
            "address": "",
        }
    ],
    "movers": [],
    "rug_watch": [],
    "whale_flows": [],
    "generated_at": "2026-08-13T05:30:00+00:00",
}

SKELETON: dict[str, Any] = {
    "title": "RMI Morning Brief — Thursday, August 13",
    "market_snapshot": "BTC $67,231.15 (▲2.1%) · ETH $3,412.50 (▼0.4%) · SOL $145.20 (▲5.2%)",
    "sections": {
        "Market Snapshot": None,
        "Top Stories": ["1. Ethereum ETF inflows hit record — CoinDesk https://example.com/eth-etf"],
        "Movers & Trending": None,
        "Rug & Scam Watch": None,
        "On-Chain Notable": None,
        "The Take": None,
    },
}


class CapturingCompletion:
    """Injectable ``completion`` that records calls and returns a canned result."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.result is None:
            return {}
        return self.result


def _system_content(calls: list[dict[str, Any]]) -> str:
    messages = calls[0]["messages"]
    return next(m["content"] for m in messages if m["role"] == "system")


def _user_content(calls: list[dict[str, Any]]) -> str:
    messages = calls[0]["messages"]
    return next(m["content"] for m in messages if m["role"] == "user")


# ── llm_writer — system prompt + message construction ───────────────────────


async def test_llm_writer_system_prompt_contains_voice_rules_and_skeleton() -> None:
    completion = CapturingCompletion(result={"content": "prose"})
    assert await llm_writer(CONTEXT, SKELETON, completion=completion) == "prose"

    system = _system_content(completion.calls)
    assert "Rug Munch Intelligence" in system  # persona
    assert "authoritative" in system.lower()  # §4 voice
    assert "zero hype" in system.lower()  # voice rule
    assert "cite" in system.lower()  # cite-or-omit
    assert "omit" in system.lower()  # cite-or-omit
    assert "never" in system.lower()
    assert "invent" in system.lower()
    assert "address" in system.lower()  # addresses on every mover
    assert "N/A" in system  # missing sections
    assert "skeleton" in system.lower()


async def test_llm_writer_user_prompt_contains_skeleton_and_context() -> None:
    completion = CapturingCompletion(result={"content": "prose"})
    assert await llm_writer(CONTEXT, SKELETON, completion=completion) == "prose"

    user = _user_content(completion.calls)
    # Skeleton content (title + section names + rendered market line)
    assert "RMI Morning Brief — Thursday, August 13" in user
    assert "Top Stories" in user
    assert "$67,231.15" in user
    # Context content (the ONLY allowed facts)
    assert "Ethereum ETF inflows hit record" in user
    assert "example.com/eth-etf" in user
    # Both roles present, system first
    assert [m["role"] for m in completion.calls[0]["messages"]] == ["system", "user"]


async def test_llm_writer_calls_completion_with_expected_kwargs() -> None:
    completion = CapturingCompletion(result={"content": "prose"})
    assert await llm_writer(CONTEXT, SKELETON, completion=completion) == "prose"

    kwargs = completion.calls[0]
    assert kwargs["model"] == "qwen-analysis"
    assert kwargs["task_type"] == "analysis"
    assert kwargs["temperature"] == 0.6
    assert kwargs["max_tokens"] == 512


async def test_llm_writer_respects_explicit_model_override() -> None:
    completion = CapturingCompletion(result={"content": "prose"})
    assert await llm_writer(CONTEXT, SKELETON, model="qwen-chat", completion=completion) == "prose"
    assert completion.calls[0]["model"] == "qwen-chat"


# ── llm_writer — fail-open contract ("" → deterministic fallback) ───────────


async def test_llm_writer_returns_empty_when_completion_raises() -> None:
    completion = CapturingCompletion(error=RuntimeError("llama down"))
    assert await llm_writer(CONTEXT, SKELETON, completion=completion) == ""


async def test_llm_writer_returns_empty_when_content_empty() -> None:
    completion = CapturingCompletion(result={"content": ""})
    assert await llm_writer(CONTEXT, SKELETON, completion=completion) == ""


async def test_llm_writer_returns_empty_when_result_missing_content() -> None:
    completion = CapturingCompletion(result=None)  # returns {} — no "content" key
    assert await llm_writer(CONTEXT, SKELETON, completion=completion) == ""


async def test_llm_writer_returns_empty_when_result_not_dict() -> None:
    completion = CapturingCompletion(result="not-a-dict")
    assert await llm_writer(CONTEXT, SKELETON, completion=completion) == ""


# ── draft_briefing — LLM writer → deterministic fallback ────────────────────


async def test_draft_briefing_uses_llm_writer_prose_when_injected() -> None:
    writer = functools.partial(llm_writer, completion=CapturingCompletion(result={"content": "LLM PROSE"}))
    assert await draft_briefing(CONTEXT, writer=writer) == "LLM PROSE"


async def test_draft_briefing_falls_back_when_llm_writer_returns_empty() -> None:
    writer = functools.partial(llm_writer, completion=CapturingCompletion(result={"content": ""}))
    draft = await draft_briefing({}, writer=writer)
    assert "RMI Morning Brief" in draft  # deterministic template
    assert "N/A" in draft  # empty sections render as N/A
    for section in SECTIONS:
        assert section in draft


async def test_draft_briefing_falls_back_when_llm_writer_raises() -> None:
    writer = functools.partial(llm_writer, completion=CapturingCompletion(error=RuntimeError("llm down")))
    draft = await draft_briefing(CONTEXT, writer=writer)
    assert "RMI Morning Brief" in draft
    assert "Ethereum ETF inflows hit record" in draft  # context still cited by fallback


# ── draft_briefing — default writer wiring (env-gated, no network) ──────────


async def test_draft_briefing_default_writer_is_llm_writer_when_enabled(monkeypatch: Any) -> None:
    import app.ai_router

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        return {"content": "LLM DEFAULT PROSE"}

    monkeypatch.setenv("RMI_BRIEFING_LLM_WRITER", "1")
    monkeypatch.setattr(app.ai_router, "chat_completion", fake_completion)
    assert await draft_briefing(CONTEXT) == "LLM DEFAULT PROSE"


async def test_draft_briefing_default_stays_deterministic_when_llm_disabled(monkeypatch: Any) -> None:
    monkeypatch.setenv("RMI_BRIEFING_LLM_WRITER", "0")
    draft = await draft_briefing(CONTEXT)
    assert "RMI Morning Brief" in draft
    assert "Ethereum ETF inflows hit record" in draft
