"""Error-path tests for the LLM call clients (``handlers/chat_ai/_llm.py``).

Complements ``tests/unit/domain/test_chat_ai_llm.py`` with the REAL-WORLD
failure modes the pipeline must survive: malformed provider payloads
(non-dict body / non-str message / non-dict usage / non-int tokens),
timeouts, missing API keys and a crash INSIDE the remote client. All tests
are offline — ``httpx.AsyncClient`` and the cost-aware fallback are faked
at the module boundary, exactly like the existing suite.

Contract under test: the clients degrade to ``None`` / the graceful
unavailability message and never propagate an exception into the turn.
"""

from __future__ import annotations

import asyncio
import types
from typing import Any, ClassVar

import httpx
import pytest

from app.domains.telegram.rugmunchbot.handlers.chat_ai import _llm


class _FakeLLMClient:
    """httpx.AsyncClient class fake recording posts with a configurable response.

    Configuration lives on class attributes before monkeypatching
    ``_llm.httpx.AsyncClient``; the constructor ignores the ``timeout=``
    kwargs the clients pass.
    """

    _status: ClassVar[int] = 200
    _payload: ClassVar[Any] = {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 7}}
    _headers: ClassVar[dict[str, str]] = {}
    _raise: ClassVar[Exception | None] = None

    calls: ClassVar[list[tuple[str, dict]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeLLMClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> Any:
        _FakeLLMClient.calls.append((url, json or {}))
        if _FakeLLMClient._raise is not None:
            raise _FakeLLMClient._raise

        class _Resp:
            status_code = _FakeLLMClient._status
            headers = _FakeLLMClient._headers

            def json(self) -> Any:
                return _FakeLLMClient._payload

        return _Resp()


_SNAPPY_CFG = {
    "model": "deepseek-v4-flash-free",
    "base_url": "https://snappy.test/chat/completions",
    "api_key": "test-snappy-key",
    "max_tokens": 256,
    "temperature": 0.4,
}


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the module at the fake client + a no-op cost-aware fallback.

    ``chat_with_fallback`` is imported lazily inside ``_call_remote_fast``;
    patching it on ``app.llm_config`` guarantees the free-tier fallback
    chain never touches the network in these tests.
    """
    import app.llm_config

    _FakeLLMClient._status = 200
    _FakeLLMClient._payload = {"choices": [{"message": {"content": "hello"}}], "usage": {"total_tokens": 7}}
    _FakeLLMClient._headers = {}
    _FakeLLMClient._raise = None
    _FakeLLMClient.calls = []
    monkeypatch.setattr(_llm.httpx, "AsyncClient", _FakeLLMClient)
    monkeypatch.setattr(_llm, "get_snappy_config", lambda: dict(_SNAPPY_CFG))
    monkeypatch.setattr(_llm, "get_long_config", lambda: dict(_SNAPPY_CFG))

    async def _no_fallback(messages: Any, tier: str, **kwargs: Any) -> str:
        return ""

    monkeypatch.setattr(app.llm_config, "chat_with_fallback", _no_fallback)


# ── backend RAG chat — malformed payloads / timeouts ───────────────────────


class TestBackendChatMalformedPayloads:
    @pytest.mark.parametrize(
        "payload",
        [
            "not a dict",  # non-dict body
            ["also", "a", "list"],
            42,
            {"message": ""},  # empty message
            {"message": "   "},  # whitespace-only message
            {"message": 123},  # non-str message
            {"message": ["nested"]},  # non-str message (list)
            {"message": None},  # missing message
        ],
    )
    async def test_malformed_payloads_never_raise_and_return_none(self, payload: Any) -> None:
        _FakeLLMClient._payload = payload
        assert await _llm._call_backend_chat("q", 1) is None

    async def test_non_dict_usage_is_non_fatal_answer_kept(self) -> None:
        """A non-dict ``usage`` must NOT drop a valid tier-1 answer."""
        _FakeLLMClient._payload = {"message": "valid answer", "usage": "garbage"}
        result = await _llm._call_backend_chat("q", 1)
        assert result is not None
        assert result["message"] == "valid answer"
        assert result["tokens"] == 0

    async def test_non_int_tokens_are_non_fatal_answer_kept(self) -> None:
        """A bad token count defaults to 0 but keeps the valid answer."""
        _FakeLLMClient._payload = {"message": "valid answer", "usage": {"total_tokens": "seven"}}
        result = await _llm._call_backend_chat("q", 1)
        assert result is not None
        assert result["message"] == "valid answer"
        assert result["tokens"] == 0

    async def test_none_tokens_are_non_fatal_answer_kept(self) -> None:
        _FakeLLMClient._payload = {"message": "valid answer", "usage": {"total_tokens": None}}
        result = await _llm._call_backend_chat("q", 1)
        assert result is not None
        assert result["tokens"] == 0

    async def test_backend_timeout_returns_none(self) -> None:
        """A slow backend (wait_for deadline) degrades to None — never raises."""
        _FakeLLMClient._raise = TimeoutError("backend too slow")
        assert await _llm._call_backend_chat("q", 1) is None

    async def test_backend_httpx_timeout_returns_none(self) -> None:
        _FakeLLMClient._raise = httpx.TimeoutException("read timeout")
        assert await _llm._call_backend_chat("q", 1) is None


# ── remote LLM (snappy / long) — malformed payloads / timeouts ─────────────


class TestRemoteFastMalformedPayloads:
    @pytest.mark.parametrize(
        "payload",
        [
            "not a dict",  # non-dict body
            ["choices", "missing"],
            {"choices": []},  # empty choices
            {"choices": [{}]},  # choice without message
            {"choices": [{"message": {"content": 42}}]},  # non-str content
            {"choices": [{"message": {"content": None}}]},  # null content
            {"choices": [{"message": {"content": ""}}]},  # empty answer
            {"choices": [{"message": {"content": "   "}}]},  # whitespace answer
        ],
    )
    async def test_malformed_payloads_degrade_to_none(self, payload: Any) -> None:
        """Malformed bodies degrade through the (faked) fallback to None."""
        _FakeLLMClient._payload = payload
        assert await _llm._call_remote_fast("snappy", [{"role": "user", "content": "q"}], 1) is None

    async def test_numeric_string_token_count_is_coerced(self) -> None:
        """Providers occasionally return token counts as strings — int()
        coerces numeric strings and keeps the valid answer."""
        _FakeLLMClient._payload = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": "7"}}
        result = await _llm._call_remote_fast("snappy", [{"role": "user", "content": "q"}], 1)
        assert result is not None
        assert result["message"] == "ok"
        assert result["tokens"] == 7

    async def test_config_getter_raising_degrades_to_none(self) -> None:
        """A config provider that throws (bad env / import) must fail open."""
        _llm.get_snappy_config = lambda: (_ for _ in ()).throw(RuntimeError("config exploded"))  # type: ignore[assignment]
        assert await _llm._call_remote_fast("snappy", [{"role": "user", "content": "q"}], 1) is None

    async def test_remote_timeout_degrades_through_fallback_to_none(self) -> None:
        """A timed-out free provider drops into the cost-aware fallback;
        when the fallback also yields nothing the client returns None."""
        _FakeLLMClient._raise = TimeoutError("remote too slow")
        assert await _llm._call_remote_fast("snappy", [{"role": "user", "content": "q"}], 1) is None

    async def test_fallback_raising_still_returns_none(self) -> None:
        """The cost-aware fallback itself blowing up must never raise."""
        import app.llm_config

        async def _boom(messages: Any, tier: str, **kwargs: Any) -> str:
            raise RuntimeError("paid fallback exploded")

        app.llm_config.chat_with_fallback = _boom  # type: ignore[assignment]
        _FakeLLMClient._status = 429  # free tier rate-limited
        assert await _llm._call_remote_fast("snappy", [{"role": "user", "content": "q"}], 1) is None


class TestRemoteLlmOrchestrator:
    async def test_timeout_returns_graceful_unavailable_message(self) -> None:
        """A timed-out remote chain surfaces the fail-open message — the
        user never sees a traceback and the turn never crashes."""
        _FakeLLMClient._raise = TimeoutError("remote too slow")
        result = await _llm._call_remote_llm("hello", 1)
        assert result is not None
        assert result["message"] == _llm.REMOTE_UNAVAILABLE_MESSAGE
        assert result["provider"] == "unavailable"

    async def test_non_dict_usage_crash_is_caught_by_fallback_orchestrator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: a provider returning ``usage: null`` (or a non-dict
        usage / non-numeric token string) currently raises AttributeError /
        ValueError inside ``_call_remote_fast``. The turn-level contract
        that MUST hold is that the fallback orchestrator catches the crash
        and degrades to a tools-only / None answer — never a traceback.

        This asserts the graceful boundary; the client-level crash itself is
        tracked in [REVIEW] (a future app fix should guard the parsing).
        """
        user = types.SimpleNamespace(id=1)

        async def _empty() -> Any:
            return None

        async def _empty_dict() -> dict[Any, Any]:
            return {}

        async def _boom(*args: Any, **kwargs: Any) -> Any:
            raise AttributeError("'NoneType' object has no attribute 'get'")

        monkeypatch.setattr(_llm, "_call_remote_fast", _boom)
        daily = asyncio.create_task(_empty())
        scan = asyncio.create_task(_empty())
        bridge = asyncio.create_task(_empty())
        live = asyncio.create_task(_empty_dict())
        rag = asyncio.create_task(_empty())
        router = asyncio.create_task(_empty())

        try:
            result, _sources, _actions, _rag, _has_market = await _llm._build_fallback_answer(
                "hello",
                user,
                [],
                daily_ctx_task=daily,
                scan_ctx_task=scan,
                bridge_ctx_task=bridge,
                live_tools_task=live,
                rag_task=rag,
                router_ctx_task=router,
            )
        finally:
            # The helper does not cancel pending tasks; cancel for hygiene.
            for t in (daily, scan, bridge, live, rag, router):
                t.cancel()
        # No exception escaped: the crash degraded to no-LLM result (the
        # turn falls through to the canned reply path upstream).
        assert result is None or result.get("provider") == "tools"
