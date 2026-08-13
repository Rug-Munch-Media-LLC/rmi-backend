"""Unit tests for the degodded ``handlers/chat_ai/_llm.py`` module.

Covers the three LLM call clients (backend/local/Qwen, DeepSeek, Zen) with
``httpx.AsyncClient`` faked at the module boundary — no network I/O.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot.handlers.chat_ai import _llm


class _FakeLLMClient:
    """httpx.AsyncClient class fake recording posts with a configurable response.

    Configuration is set on class attributes before monkeypatching
    ``_llm.httpx.AsyncClient``; the constructor accepts (and ignores) the
    ``timeout=`` kwargs the clients pass.
    """

    _status: ClassVar[int] = 200
    _payload: ClassVar[Any] = {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 7}}
    _raise: ClassVar[Exception | None] = None

    calls: ClassVar[list[tuple[str, dict]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeLLMClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, json: dict) -> Any:
        _FakeLLMClient.calls.append((url, json))
        if _FakeLLMClient._raise is not None:
            raise _FakeLLMClient._raise

        class _Resp:
            status_code = _FakeLLMClient._status

            def json(self) -> Any:
                return _FakeLLMClient._payload

        return _Resp()


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeLLMClient._status = 200
    _FakeLLMClient._payload = {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 7}}
    _FakeLLMClient._raise = None
    _FakeLLMClient.calls = []
    monkeypatch.setattr(_llm.httpx, "AsyncClient", _FakeLLMClient)


# ── backend chat (local models) ────────────────────────────────────────────


async def test_backend_chat_shallow_uses_qwen() -> None:
    result = await _llm._call_backend_chat("hi there", 1)
    assert result is not None
    assert result["provider"] == "local"
    assert result["model"] == _llm.QWEN_MODEL
    assert result["tokens"] == 7
    url, body = _FakeLLMClient.calls[0]
    assert url == _llm.QWEN_CHAT_URL
    assert body["model"] == _llm.QWEN_MODEL


async def test_backend_chat_deep_keyword_uses_deepseek() -> None:
    for keyword in ("analyze", "why", "compare", "report", "technical"):
        _FakeLLMClient.calls.clear()
        result = await _llm._call_backend_chat(f"please {keyword} this token", 1)
        assert result is not None
        url, body = _FakeLLMClient.calls[0]
        assert url == _llm.DEEPSEEK_CHAT_URL
        assert body["model"] == _llm.DEEPSEEK_MODEL


async def test_backend_chat_history_last_five() -> None:
    history = [{"role": "user", "content": f"m{i}"} for i in range(7)]
    await _llm._call_backend_chat("q", 1, history=history)
    _, body = _FakeLLMClient.calls[0]
    assert len(body["messages"]) == 1 + 5 + 1  # system + 5 history + current
    assert body["messages"][-1] == {"role": "user", "content": "q"}


async def test_backend_chat_message_truncated_to_2000() -> None:
    await _llm._call_backend_chat("x" * 5000, 1)
    _, body = _FakeLLMClient.calls[0]
    assert body["messages"][-1]["content"] == "x" * 2000


async def test_backend_chat_strips_think_blocks() -> None:
    _FakeLLMClient._payload = {
        "choices": [{"message": {"content": "<think>internal reasoning</think>final answer"}}],
        "usage": {},
    }
    result = await _llm._call_backend_chat("q", 1)
    assert result is not None
    assert "<think>" not in result["message"]
    assert result["message"] == "final answer"


async def test_backend_chat_takes_last_substantive_paragraph() -> None:
    _FakeLLMClient._payload = {
        "choices": [{"message": {"content": "Okay, let me think about this.\n\nTHE REAL ANSWER HERE."}}],
        "usage": {},
    }
    result = await _llm._call_backend_chat("q", 1)
    assert result is not None
    assert result["message"] == "THE REAL ANSWER HERE."


async def test_backend_chat_non_200_returns_none() -> None:
    _FakeLLMClient._status = 500
    assert await _llm._call_backend_chat("q", 1) is None


async def test_backend_chat_network_error_returns_none() -> None:
    _FakeLLMClient._raise = OSError("llama down")
    assert await _llm._call_backend_chat("q", 1) is None


async def test_backend_chat_invalid_payload_returns_none() -> None:
    for payload in ({"choices": []}, "not a dict", {"choices": [{"message": {}}]}):
        _FakeLLMClient._payload = payload
        assert await _llm._call_backend_chat("q", 1) is None


# ── Zen cloud client ───────────────────────────────────────────────────────


async def test_zen_llm_deep_flag() -> None:
    _FakeLLMClient.calls.clear()
    await _llm._call_zen_llm("question", 1, deep=False)
    _, body = _FakeLLMClient.calls[0]
    assert body["max_tokens"] == 500
    assert "on-chain analyst" not in body["messages"][0]["content"]
    _FakeLLMClient.calls.clear()
    await _llm._call_zen_llm("question", 1, deep=True)
    _, body = _FakeLLMClient.calls[0]
    assert body["max_tokens"] == 2000
    assert "on-chain analyst" in body["messages"][0]["content"]
    assert body["thinking"] == {"type": "disabled"}


async def test_zen_llm_history_reference_only_block() -> None:
    history = [{"role": "assistant", "content": "old truncated answer..."}, {"role": "user", "content": "prior q"}]
    await _llm._call_zen_llm("new q", 1, history=history)
    _, body = _FakeLLMClient.calls[0]
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "user"]
    assert "PREVIOUS CONVERSATION" in body["messages"][1]["content"]
    assert "CURRENT QUESTION: new q" in body["messages"][-1]["content"]


async def test_zen_llm_response_shape() -> None:
    _FakeLLMClient._payload = {"choices": [{"message": {"content": "grounded"}}], "usage": {"total_tokens": 42}}
    result = await _llm._call_zen_llm("q", 1)
    assert result is not None
    assert result["provider"] == "zen"
    assert result["message"] == "grounded"
    assert result["tokens"] == 42


async def test_zen_llm_empty_answer_returns_none() -> None:
    _FakeLLMClient._payload = {"choices": [{"message": {"content": "   "}}], "usage": {}}
    assert await _llm._call_zen_llm("q", 1) is None


async def test_zen_llm_errors_return_none() -> None:
    _FakeLLMClient._raise = _llm.httpx.HTTPError("boom")
    assert await _llm._call_zen_llm("q", 1) is None
    _FakeLLMClient._raise = None
    _FakeLLMClient._status = 429
    assert await _llm._call_zen_llm("q", 1) is None


# ── local LLM fallback ─────────────────────────────────────────────────────


async def test_local_llm_tries_qwen_first() -> None:
    result = await _llm._call_local_llm("hello", 1)
    assert result is not None
    assert result["model"] == _llm.QWEN_MODEL
    url, _ = _FakeLLMClient.calls[0]
    assert url == _llm.QWEN_CHAT_URL


async def test_local_llm_falls_back_to_deepseek() -> None:
    # Qwen (first URL) returns HTTP 500 → the client falls through to DeepSeek
    statuses = [500, 200]

    class _StatusSequenceClient(_FakeLLMClient):
        async def post(self, url: str, json: dict) -> Any:  # type: ignore[override]
            _FakeLLMClient.calls.append((url, json))
            status = statuses.pop(0)

            class _Resp:
                status_code = status

                def json(self) -> Any:
                    return _FakeLLMClient._payload

            return _Resp()

    _FakeLLMClient.calls.clear()
    _FakeLLMClient._payload = {"choices": [{"message": {"content": "answer"}}], "usage": {"total_tokens": 3}}
    _llm.httpx.AsyncClient = _StatusSequenceClient  # type: ignore[assignment]
    try:
        result = await _llm._call_local_llm("hello", 1)
    finally:
        _llm.httpx.AsyncClient = _FakeLLMClient  # type: ignore[assignment]
    assert result is not None
    assert result["model"] == _llm.DEEPSEEK_MODEL
    assert _FakeLLMClient.calls[0][0] == _llm.QWEN_CHAT_URL
    assert _FakeLLMClient.calls[1][0] == _llm.DEEPSEEK_CHAT_URL
    assert len(_FakeLLMClient.calls) == 2


async def test_local_llm_all_fail_returns_none() -> None:
    _FakeLLMClient._status = 500
    assert await _llm._call_local_llm("hello", 1) is None


async def test_local_llm_history_capped_at_two() -> None:
    history = [{"role": "user", "content": f"m{i}"} for i in range(5)]
    await _llm._call_local_llm("q", 1, history=history)
    _, body = _FakeLLMClient.calls[0]
    assert len(body["messages"]) == 1 + 2 + 1  # system + 2 history + current
