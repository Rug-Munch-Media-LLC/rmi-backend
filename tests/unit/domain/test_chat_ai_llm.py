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
    # Tier-1 ships the sharp grounded persona via context_override so the
    # production model gets the grounding bullets + anti-hallucination rules
    # + response-formatting (not just the generic backend SYSTEM_PROMPT).
    assert body["context_override"]["system_context"] == _llm.PERSONA_CONTEXT
    assert "DATA GROUNDING RULES" in body["context_override"]["system_context"]
    assert "ANTI-PATTERNS" in body["context_override"]["system_context"]
    assert "RESPONSE FORMATTING" in body["context_override"]["system_context"]


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
    # Triplicated fallback prompts consolidated: fast + deep + local now
    # share ONE condensed system prompt; `deep` only changes the budget.
    assert body["messages"][0]["content"] == _llm.FALLBACK_SYSTEM_PROMPT
    _FakeLLMClient.calls.clear()
    await _llm._call_zen_llm("question", 1, deep=True)
    _, body = _FakeLLMClient.calls[0]
    assert body["max_tokens"] == 2000
    assert body["messages"][0]["content"] == _llm.FALLBACK_SYSTEM_PROMPT
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


# ── Complexity router (local-first) ──────────────────────────────────────


def test_route_local_model_simple_chat() -> None:
    """Greeting/price/scan/short questions route to 4B (chat)."""
    for q in ("hello", "what's the price of btc", "scan this token", "gm", "ok"):
        assert _llm._route_local_model(q) == "chat", q


def test_route_local_model_deep_analysis() -> None:
    """Investigate/compare/trace/explain/long questions route to 9B (analysis)."""
    for q in (
        "investigate this wallet",
        "explain how mev works",
        "why did this token dump",
        "compare these two tokens",
        "trace the funds on-chain",
    ):
        assert _llm._route_local_model(q) == "analysis", q
    # Long messages route to analysis (mirrors backend _route_task length gate).
    assert _llm._route_local_model("x" * 200) == "analysis"


async def test_local_llm_simple_question_tries_qwen_first() -> None:
    """A simple/grounded question routes to the fast 4B (qwen-chat) first."""
    _FakeLLMClient.calls.clear()
    await _llm._call_local_llm("what's the price of sol?", 1)
    url, _ = _FakeLLMClient.calls[0]
    assert url == _llm.QWEN_CHAT_URL


async def test_local_llm_deep_question_tries_deepseek_first() -> None:
    """A deep/analysis question routes to the 9B (qwen-analysis) first."""
    _FakeLLMClient.calls.clear()
    await _llm._call_local_llm("investigate this wallet's funding sources", 1)
    url, _ = _FakeLLMClient.calls[0]
    assert url == _llm.DEEPSEEK_CHAT_URL


def test_local_context_too_large_defers() -> None:
    """A prompt beyond the 16k-ctx budget must be flagged (routes to Zen)."""
    assert _llm._local_context_too_large("x" * 100, None, None) is False
    assert _llm._local_context_too_large("x" * (4 * _llm.LOCAL_CTX_LIMIT_TOKENS), None, None) is True


# ── host-load probe ──────────────────────────────────────────────────────


class _HealthProbeClient(_FakeLLMClient):
    """httpx fake with ``get`` support for the /health host-load probe."""

    _slots: ClassVar[list[int]] = []

    async def get(self, url: str) -> Any:
        slot = _HealthProbeClient._slots.pop(0)

        class _Resp:
            status_code = 200

            def json(self) -> dict:
                return {"slots_idle": slot}

        return _Resp()


async def test_local_host_loaded_both_busy() -> None:
    """Both llama servers reporting slots_idle == 0 → host loaded → Zen."""
    _HealthProbeClient._slots = [0, 0]
    _llm.httpx.AsyncClient = _HealthProbeClient  # type: ignore[assignment]
    try:
        assert await _llm._local_host_loaded() is True
    finally:
        _llm.httpx.AsyncClient = _FakeLLMClient  # type: ignore[assignment]


async def test_local_host_loaded_one_free() -> None:
    """One server with a free slot → not loaded → local still runs."""
    _HealthProbeClient._slots = [0, 1]
    _llm.httpx.AsyncClient = _HealthProbeClient  # type: ignore[assignment]
    try:
        assert await _llm._local_host_loaded() is False
    finally:
        _llm.httpx.AsyncClient = _FakeLLMClient  # type: ignore[assignment]


async def test_local_host_loaded_probe_error_fails_open() -> None:
    """A probe error (e.g. httpx fake without ``get``) must fail open → False."""
    _FakeLLMClient._raise = OSError("probe down")
    assert await _llm._local_host_loaded() is False
    _FakeLLMClient._raise = None
