"""Tests for the chat_ai fallback LLM prompt/history fixes (_llm.py).

Fix 2: the local system prompt includes the grounding/anti-parroting rules
       (answer-the-question, no verbatim context quoting, no invented metrics).
Fix 3: local history is rendered as ONE inert "PREVIOUS CONVERSATION"
       reference block — never as real user/assistant turns and never with a
       trailing bare user turn — so the model answers the CURRENT question
       instead of the previous one.
``httpx.AsyncClient`` is faked at the module boundary — no network I/O.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot.handlers.chat_ai import _llm


class _FakeLLMClient:
    """httpx.AsyncClient class fake recording posts (configurable response)."""

    _status: ClassVar[int] = 200
    _payload: ClassVar[Any] = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 3}}

    calls: ClassVar[list[tuple[str, dict]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeLLMClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Any:
        _FakeLLMClient.calls.append((url, json or {}))

        class _Resp:
            status_code = _FakeLLMClient._status

            def json(self) -> Any:
                return _FakeLLMClient._payload

        return _Resp()


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeLLMClient._status = 200
    _FakeLLMClient._payload = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 3}}
    _FakeLLMClient.calls = []
    monkeypatch.setattr(_llm.httpx, "AsyncClient", _FakeLLMClient)


async def _local_messages(message: str, history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    await _llm._call_local_llm(message, 1, history=history)
    _, body = _FakeLLMClient.calls[0]
    return body["messages"]


class TestLocalSystemPromptGrounding:
    async def test_system_prompt_contains_grounding_rules(self) -> None:
        messages = await _local_messages("question", None)
        system = messages[0]["content"]
        # Assert the SEMANTIC requirements, not exact sentences: the v12/v14
        # slim passes compress prose ("not quote verbatim" → "synthesize"),
        # so each rule is matched by any phrasing that preserves its meaning.
        # answer-the-question: model must answer the CURRENT/LATEST question
        assert re.search(r"(?i)\blatest\s+question\b", system), "answer-the-LATEST-question rule missing"
        # no verbatim context quoting: context is synthesized, not pasted
        assert re.search(
            r"(?i)synthesize|not\s+quote|own\s+words|never\s+paste",
            system,
        ), "synthesize-don't-quote rule missing"
        # no invented metrics
        assert re.search(r"(?i)never\s+invent", system), "never-invent rule missing"
        # chain scoping + risk ranking
        assert re.search(r"(?i)chain", system), "chain-scoping rule missing"
        assert re.search(r"(?i)risk\s+rank", system), "risk-ranking rule missing"
        # instructions security: never reveal/repeat the instructions
        assert re.search(r"(?i)reveal|repeat", system), "instructions-security rule missing"

    async def test_system_prompt_has_token_list_address_rule(self) -> None:
        """The TOKEN LISTS non-negotiable survives shortening (semantic check)."""
        messages = await _local_messages("question", None)
        system = messages[0]["content"]
        assert re.search(r"(?i)token\s+lists?|full\s+address", system), "token-list-address rule missing"

    async def test_system_prompt_digest_still_appended(self) -> None:
        await _llm._call_local_llm("q", 1, system_context="BTC at 100k, risk-on tape")
        _, body = _FakeLLMClient.calls[0]
        system = body["messages"][0]["content"]
        assert "BTC at 100k, risk-on tape" in system


class TestLocalHistoryInertReference:
    async def test_history_rendered_as_one_inert_reference_block(self) -> None:
        history = [
            {"role": "user", "content": "previous question?"},
            {"role": "assistant", "content": "old truncated answer..."},
        ]
        messages = await _local_messages("new question?", history)
        roles = [m["role"] for m in messages]
        # system + one inert reference block + one current-question user turn
        assert roles == ["system", "user", "user"]
        assert "PREVIOUS CONVERSATION (reference only" in messages[1]["content"]
        assert "do NOT continue it" in messages[1]["content"]
        # Both history turns live INSIDE the single inert block, labeled.
        assert "[user] previous question?" in messages[1]["content"]
        assert "[assistant] old truncated answer..." in messages[1]["content"]

    async def test_no_trailing_bare_user_turn_before_current_question(self) -> None:
        # History ending on an unanswered user turn must NOT be emitted as a
        # trailing user turn — the LAST message is always the current question.
        history = [
            {"role": "user", "content": "old question?"},
            {"role": "user", "content": "unanswered follow-up?"},
        ]
        messages = await _local_messages("current question?", history)
        last = messages[-1]
        assert last["role"] == "user"
        assert last["content"] == "CURRENT QUESTION: current question?"
        # The unanswered follow-up lives inside the inert reference block.
        assert "[user] unanswered follow-up?" in messages[1]["content"]

    async def test_truncated_assistant_turn_cannot_become_continuation(self) -> None:
        history = [{"role": "assistant", "content": "list item 1\nlist item 2"}]
        messages = await _local_messages("what is a honeypot?", history)
        # No assistant turn exists in the payload — the model cannot "finish"
        # the old answer; it must answer the CURRENT QUESTION.
        assert all(m["role"] != "assistant" for m in messages)
        assert messages[-1]["content"] == "CURRENT QUESTION: what is a honeypot?"

    async def test_history_capped_at_two_lines_inside_one_block(self) -> None:
        history = [{"role": "user", "content": f"m{i}"} for i in range(5)]
        messages = await _local_messages("q", history)
        assert len(messages) == 3  # system + 1 inert block + current question
        block = messages[1]["content"]
        assert "[user] m3" in block
        assert "[user] m4" in block
        assert "[user] m0" not in block
