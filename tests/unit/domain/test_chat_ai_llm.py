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
    _headers: ClassVar[dict[str, str]] = {}
    _raise: ClassVar[Exception | None] = None

    calls: ClassVar[list[tuple[str, dict]]] = []
    call_headers: ClassVar[list[dict | None]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeLLMClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Any:
        _FakeLLMClient.calls.append((url, json or {}))
        _FakeLLMClient.call_headers.append(headers)
        if _FakeLLMClient._raise is not None:
            raise _FakeLLMClient._raise

        class _Resp:
            status_code = _FakeLLMClient._status
            headers = _FakeLLMClient._headers

            def json(self) -> Any:
                return _FakeLLMClient._payload

        return _Resp()


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeLLMClient._status = 200
    _FakeLLMClient._payload = {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 7}}
    _FakeLLMClient._headers = {}
    _FakeLLMClient._raise = None
    _FakeLLMClient.calls = []
    _FakeLLMClient.call_headers = []
    monkeypatch.setattr(_llm.httpx, "AsyncClient", _FakeLLMClient)


# ── backend RAG chat (POST /api/v1/chat, auto-RAG tier-1) ──────────────────


async def test_backend_chat_posts_to_chat_endpoint() -> None:
    _FakeLLMClient._payload = {"message": "grounded answer", "usage": {"total_tokens": 7}, "model": "m"}
    _FakeLLMClient._headers = {"X-RAG-Context": "3"}
    result = await _llm._call_backend_chat("hi there", 1)
    assert result is not None
    assert result["provider"] == "backend"
    assert result["message"] == "grounded answer"
    assert result["rag_count"] == 3  # real RAG count from X-RAG-Context
    assert result["tokens"] == 7
    url, body = _FakeLLMClient.calls[0]
    assert url == _llm.CHAT_ENDPOINT
    assert body["message"] == "hi there"
    assert body["history"] == []
    # No context_override -> backend auto-RAG (news-aware grounding).
    assert "context_override" not in body


async def test_backend_chat_forwarded_for_header() -> None:
    _FakeLLMClient._payload = {"message": "ok"}
    await _llm._call_backend_chat("q", 777, history=[{"role": "user", "content": "first"}])
    headers = _FakeLLMClient.call_headers[0]
    assert headers is not None
    assert headers["X-Forwarded-For"] == "777"
    assert _FakeLLMClient.calls[0][1]["history"] == [{"role": "user", "content": "first"}]


async def test_backend_chat_message_truncated_to_2000() -> None:
    _FakeLLMClient._payload = {"message": "ok"}
    await _llm._call_backend_chat("x" * 5000, 1)
    _, body = _FakeLLMClient.calls[0]
    assert body["message"] == "x" * 2000


async def test_backend_chat_rag_count_zero_without_header() -> None:
    _FakeLLMClient._payload = {"message": "ok"}
    _FakeLLMClient._headers = {}
    result = await _llm._call_backend_chat("q", 1)
    assert result is not None
    assert result["rag_count"] == 0


async def test_backend_chat_non_200_returns_none() -> None:
    _FakeLLMClient._status = 500
    assert await _llm._call_backend_chat("q", 1) is None


async def test_backend_chat_network_error_returns_none() -> None:
    _FakeLLMClient._raise = OSError("backend down")
    assert await _llm._call_backend_chat("q", 1) is None


async def test_backend_chat_invalid_payload_returns_none() -> None:
    for payload in ({"message": ""}, "not a dict", {"choices": []}):
        _FakeLLMClient._payload = payload
        assert await _llm._call_backend_chat("q", 1) is None


# ── Zen cloud client ───────────────────────────────────────────────────────


async def test_zen_llm_deep_flag() -> None:
    _FakeLLMClient.calls.clear()
    await _llm._call_zen_llm("question", 1, deep=False)
    _, body = _FakeLLMClient.calls[0]
    assert body["max_tokens"] == 500
    assert "on-chain investigator" not in body["messages"][0]["content"]
    _FakeLLMClient.calls.clear()
    await _llm._call_zen_llm("question", 1, deep=True)
    _, body = _FakeLLMClient.calls[0]
    assert body["max_tokens"] == 2000
    assert "on-chain investigator" in body["messages"][0]["content"]
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


async def test_zen_llm_reports_real_rag_count_from_sources() -> None:
    _FakeLLMClient._payload = {"choices": [{"message": {"content": "grounded"}}], "usage": {}}
    result = await _llm._call_zen_llm(
        "q", 1, sources=[{"type": "web", "query": "honeypot"}, {"type": "rag", "query": "scam"}]
    )
    assert result is not None
    assert result["rag_count"] == 2  # real bridge grounding count, not hardcoded 0


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


async def test_local_llm_reports_real_rag_count_from_sources() -> None:
    _FakeLLMClient._payload = {"choices": [{"message": {"content": "grounded"}}], "usage": {}}
    result = await _llm._call_local_llm(
        "q", 1, sources=[{"type": "rag", "query": "honeypot"}, {"type": "web", "query": "news"}]
    )
    assert result is not None
    assert result["rag_count"] == 2  # real bridge grounding count, not hardcoded 0


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
    assert len(body["messages"]) == 1 + 1 + 1  # system + 1 inert history block + current
