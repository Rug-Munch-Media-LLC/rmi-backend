"""Unit tests for the degodded ``handlers/chat_ai/_llm.py`` module.

Covers the LLM call clients (backend/local/Qwen, DeepSeek) with
``httpx.AsyncClient`` faked at the module boundary — no network I/O.
"""

from __future__ import annotations

import asyncio
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
    # v12 slim pass renamed the sections ("DATA GROUNDING RULES" →
    # "DATA GROUNDING", "RESPONSE FORMATTING" → "FORMATTING") — assert the
    # current names plus the new ANSWER QUALITY block.
    assert "DATA GROUNDING" in body["context_override"]["system_context"]
    assert "ANTI-PATTERNS" in body["context_override"]["system_context"]
    assert "FORMATTING" in body["context_override"]["system_context"]
    assert "ANSWER QUALITY" in body["context_override"]["system_context"]


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


# ── reliability hardening: bounded retry + total budget (2026-08-15) ──────


async def test_local_llm_retries_transient_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient transport failure is retried once (bounded) and succeeds.

    The retry must NOT spin: exactly one extra POST on the failing model, and
    the second model is never touched once the retry recovers.
    """
    monkeypatch.setattr(_llm, "LOCAL_LLM_RETRY_BACKOFF", 0.0)  # keep the test fast
    failures = {"count": 0}

    class _FlakyClient(_FakeLLMClient):
        async def post(self, url: str, json: dict) -> Any:  # type: ignore[override]
            if failures["count"] == 0:
                failures["count"] += 1
                _FakeLLMClient.calls.append((url, json))
                raise OSError("connection reset")
            return await _FakeLLMClient.post(self, url, json=json)

    _FakeLLMClient._payload = {"choices": [{"message": {"content": "recovered"}}], "usage": {}}
    _FakeLLMClient.calls.clear()
    _llm.httpx.AsyncClient = _FlakyClient  # type: ignore[assignment]
    try:
        result = await _llm._call_local_llm("hello", 1)
    finally:
        _llm.httpx.AsyncClient = _FakeLLMClient  # type: ignore[assignment]
    assert result is not None
    assert result["message"] == "recovered"
    # qwen failed once (retried) then succeeded — only the one model ran.
    assert len(_FakeLLMClient.calls) == 2
    assert _FakeLLMClient.calls[0][0] == _llm.QWEN_CHAT_URL
    assert _FakeLLMClient.calls[1][0] == _llm.QWEN_CHAT_URL


async def test_local_llm_transient_errors_exhaust_retries_then_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every transient failure exhausts the bounded retry and fails open to None."""
    monkeypatch.setattr(_llm, "LOCAL_LLM_RETRY_BACKOFF", 0.0)
    _FakeLLMClient._raise = OSError("llama down")
    _FakeLLMClient.calls.clear()
    result = await _llm._call_local_llm("hello", 1)
    assert result is None
    # "hello" routes to chat → qwen first, deepseek fallback; each model gets
    # the bounded retry → 2 attempts x 2 models = 4 POSTs (never more).
    assert len(_FakeLLMClient.calls) == 4


async def test_local_llm_http_500_not_retried() -> None:
    """HTTP status failures are NOT retried — one POST per model only."""
    _FakeLLMClient._status = 500
    _FakeLLMClient.calls.clear()
    result = await _llm._call_local_llm("hello", 1)
    assert result is None
    assert len(_FakeLLMClient.calls) == 2  # qwen + deepseek, no retries


async def test_local_llm_total_budget_timeout_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled llama server cannot block the turn past LOCAL_LLM_TOTAL_BUDGET."""
    monkeypatch.setattr(_llm, "LOCAL_LLM_TOTAL_BUDGET", 0.1)

    class _StalledClient(_FakeLLMClient):
        async def post(self, url: str, json: dict) -> Any:  # type: ignore[override]
            _FakeLLMClient.calls.append((url, json))
            await asyncio.sleep(5)  # never answers; cancelled by the budget

            class _Resp:
                status_code = 200

                def json(self) -> Any:
                    return {"choices": [{"message": {"content": "never"}}]}

            return _Resp()

    _FakeLLMClient.calls.clear()
    _llm.httpx.AsyncClient = _StalledClient  # type: ignore[assignment]
    try:
        result = await _llm._call_local_llm("hello", 1)
    finally:
        _llm.httpx.AsyncClient = _FakeLLMClient  # type: ignore[assignment]
    assert result is None  # budget timeout → fail-open, never raises
    assert len(_FakeLLMClient.calls) == 1  # the first POST started, then the budget fired


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
    """A prompt beyond the 16k-ctx budget must be flagged."""
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
    """Both llama servers reporting slots_idle == 0 → host loaded."""
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
